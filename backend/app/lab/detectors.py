"""Four detectors on the same inputs: readings and the operator's expectation, never the truth or the fault tag.

D1 fixed threshold      the naive baseline: a reading outside its calibrated normal range
D2 residual CUSUM       statistical: cumulative sum of standardised (reading - expectation), per sensor
D3 Isolation Forest     unsupervised: how unusual is the whole residual vector compared with clean operation
D4 LightGBM             supervised: trained on injected faults, using residuals plus mass balance (flow in - flow out)

An alarm needs PERSIST consecutive ticks over the line. Thresholds of D3 and D4 are set on clean calibration runs so that
none of them raises an alarm there; D2 uses the textbook k = 0.5, h = 8.
"""

from __future__ import annotations

from dataclasses import dataclass

import lightgbm as lgb
import numpy as np
from sklearn.ensemble import IsolationForest

import warnings

from app.lab.harness import ONSET, Run

warnings.filterwarnings("ignore", message="'force_all_finite'")

PERSIST = 3
SMOOTH = 3  # ticks averaged before a residual is judged


def _roll(x: np.ndarray, n: int = SMOOTH) -> np.ndarray:
    """Trailing mean over n ticks that ignores gaps (disabled sensors stay nan)."""
    out = np.full_like(x, np.nan)
    for k in range(len(x)):
        w = x[max(0, k - n + 1):k + 1]
        out[k] = np.nanmean(w, axis=0) if np.isfinite(w).any() else np.nan
    return out


def _alarm_on(flag: np.ndarray, persist: int = PERSIST) -> np.ndarray:
    """True on every tick that completes `persist` flagged ticks in a row."""
    on = np.zeros(len(flag), bool)
    run = 0
    for k, f in enumerate(flag):
        run = run + 1 if f else 0
        on[k] = run >= persist
    return on


def scan(flag: np.ndarray, onset: int | None) -> tuple[bool, int | None]:
    """(an alarm before the onset, tick of the first alarm at or after it). With no fault every alarm counts as early."""
    on = _alarm_on(flag)
    if onset is None:
        return bool(on.any()), None
    after = np.flatnonzero(on[onset:])
    return bool(on[:onset].any()), (int(after[0]) + onset if len(after) else None)


@dataclass
class Scale:
    sigma: np.ndarray  # per sensor, robust sd of the residual in clean operation
    imb_sigma: np.ndarray  # per pipe, same for flow in - flow out
    pipes: list[tuple[int, int]]  # (flow-in column, flow-out column)
    ranges: np.ndarray  # [sensors, 2] calibrated normal range

    @classmethod
    def fit(cls, clean: list[Run], bench) -> "Scale":
        res = np.concatenate([r.residual for r in clean])
        sigma = 1.4826 * np.nanmedian(np.abs(res - np.nanmedian(res, axis=0)), axis=0)
        sigma = np.maximum(sigma, 1e-3)
        pipes = [(bench.index[f"{p}-flow-in"], bench.index[f"{p}-flow-out"]) for p in bench.g.zone_pipe.values()]
        imb = np.concatenate([np.column_stack([r.readings[:, a] - r.readings[:, b] for a, b in pipes]) for r in clean])
        imb_sigma = np.maximum(1.4826 * np.nanmedian(np.abs(imb - np.nanmedian(imb, axis=0)), axis=0), 0.5)
        ranges = np.array([[bench.g.sensor_index[s].normal_range.min, bench.g.sensor_index[s].normal_range.max] for s in bench.sensors])
        return cls(sigma, imb_sigma, pipes, ranges)

    def z(self, run: Run) -> np.ndarray:
        return _roll(run.residual) / self.sigma

    def imbalance(self, run: Run) -> np.ndarray:
        raw = np.column_stack([run.readings[:, a] - run.readings[:, b] for a, b in self.pipes])
        return _roll(raw) / self.imb_sigma

    def compact(self, run: Run) -> np.ndarray:
        """The mass balance of each pipe and the largest residual: what an operator would watch, without 89 noisy columns."""
        f = self.features(run)
        return f[:, len(self.sigma):]

    def features(self, run: Run) -> np.ndarray:
        z, imb = self.z(run), self.imbalance(run)
        zz = np.nan_to_num(z)
        return np.column_stack([zz, np.nan_to_num(imb), np.abs(zz).max(axis=1), np.abs(np.nan_to_num(imb)).max(axis=1)])


class D1:
    id, name, kind = "d1", "Fixed threshold", "baseline"
    note = "Alarm when a reading leaves its calibrated normal range. What a plain SCADA alarm does."

    def __init__(self, scale: Scale):
        self.s = scale

    def flag(self, run: Run) -> np.ndarray:
        r = run.readings
        return ((r < self.s.ranges[:, 0]) | (r > self.s.ranges[:, 1])).any(axis=1)


class D2:
    id, name, kind = "d2", "Residual CUSUM", "statistical"
    note = "Cumulative sum of each sensor's standardised difference from the twin's expectation (k = 0.5). The limit h is set on clean calibration runs, because a forecast error that lingers fools the textbook h = 8."
    K = 0.5

    def __init__(self, scale: Scale, calibration: list[Run]):
        self.s = scale
        self.H = max(8.0, max(float(_persisted(self.stat(r)).max()) for r in calibration) * 1.02)

    def stat(self, run: Run) -> np.ndarray:
        z = np.nan_to_num(run.residual / self.s.sigma)
        pos = neg = np.zeros(z.shape[1])
        out = np.zeros(len(z))
        for k in range(len(z)):
            pos = np.maximum(0, pos + z[k] - self.K)
            neg = np.maximum(0, neg - z[k] - self.K)
            out[k] = max(pos.max(), neg.max())
        return out

    def flag(self, run: Run) -> np.ndarray:
        return self.stat(run) > self.H


class D3:
    id, name, kind = "d3", "Isolation Forest", "unsupervised"
    note = "Scores how unusual a tick is compared with clean operation, on the pipes' mass balance and the largest residual. Never sees a fault in training."

    def __init__(self, scale: Scale, train: list[Run], calibration: list[Run], seed: int):
        self.s = scale
        X = np.concatenate([scale.compact(r) for r in train])
        self.model = IsolationForest(n_estimators=200, random_state=seed).fit(X)
        self.threshold = self._calibrate(calibration)

    def score(self, run: Run) -> np.ndarray:
        return -self.model.score_samples(self.s.compact(run))

    def _calibrate(self, calibration: list[Run]) -> float:
        worst = max(float(_persisted(self.score(r)).max()) for r in calibration)
        return worst * 1.02

    def flag(self, run: Run) -> np.ndarray:
        return self.score(run) > self.threshold


class D4:
    id, name, kind = "d4", "LightGBM classifier", "supervised"
    note = "Trained on injected faults of other sizes, start times and (partly) other locations than the test. Reads residuals and per-pipe mass balance."

    def __init__(self, scale: Scale, faulty: list[Run], clean: list[Run], calibration: list[Run], seed: int):
        self.s = scale
        X, y = [], []
        for r in faulty:
            f = scale.features(r)
            lab = np.zeros(len(f), int)
            lab[r.onset + 3:] = 1
            keep = np.ones(len(f), bool)
            keep[r.onset:r.onset + 3] = False  # the first ticks of a ramp are neither clean nor faulty
            X.append(f[keep]), y.append(lab[keep])
        for r in clean:
            f = scale.features(r)
            X.append(f), y.append(np.zeros(len(f), int))
        self.model = lgb.LGBMClassifier(n_estimators=250, learning_rate=0.05, num_leaves=15, min_child_samples=20, class_weight="balanced",
                                        verbose=-1, random_state=seed).fit(np.concatenate(X), np.concatenate(y))
        worst = max(float(_persisted(self.prob(r)).max()) for r in calibration)
        self.threshold = min(0.995, max(0.5, worst + 0.005))

    def prob(self, run: Run) -> np.ndarray:
        return self.model.predict_proba(self.s.features(run))[:, 1]

    def flag(self, run: Run) -> np.ndarray:
        return self.prob(run) > self.threshold


def _persisted(score: np.ndarray, n: int = PERSIST) -> np.ndarray:
    """Per tick, the lowest score over the last n ticks: the level that held for n ticks in a row."""
    out = np.full_like(score, -np.inf)
    for k in range(n - 1, len(score)):
        out[k] = score[k - n + 1:k + 1].min()
    return out


DETECTORS = ("d1", "d2", "d3", "d4")
__all__ = ["D1", "D2", "D3", "D4", "Scale", "DETECTORS", "ONSET", "scan"]
