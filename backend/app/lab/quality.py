"""Data-quality flags on sensor readings: missing, stuck, drifting.

The same function runs on the live history and on the benchmark's fault runs, so the numbers it is scored with are the numbers
the operator sees. A flag needs evidence the process cannot explain: a drift that other sensors of the same asset share, or
that many sensors of the same kind show at once (a heat wave, a holiday), is a change in the process and is not flagged.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

WINDOW = 30  # ticks looked at
MISSING_TICKS = 3
STUCK_TICKS = 12
DRIFT_Z = 6.0  # total walk over the window, in standard deviations of the sensor's residual
DRIFT_T = 12.0  # t-statistic of the trend
DRIFT_CORR = 0.92
ISOLATED_Z = 3.0  # neighbours must stay under this
COMMON_SHARE = 0.25  # share of same-kind sensors deviating at once that marks a process change


@dataclass(frozen=True)
class Flag:
    sensor_id: str
    status: str  # missing | stuck | drift
    detail: str


class QualityModel:
    def __init__(self, sensors: list[str], sigma: np.ndarray, asset_of: dict[str, str], group_of: dict[str, str]):
        self.sensors, self.sigma = sensors, np.maximum(sigma, 1e-6)
        self.index = {s: i for i, s in enumerate(sensors)}
        self.neigh = {s: [self.index[o] for o in sensors if asset_of[o] == asset_of[s] and o != s] for s in sensors}
        self.group = {s: [self.index[o] for o in sensors if group_of[o] == group_of[s]] for s in sensors}

    def _common_mode(self, res: np.ndarray) -> np.ndarray:
        """The median residual of each sensor's group at every tick. Demand-forecast error moves a whole group together; one drifting sensor does not."""
        cm = np.zeros_like(res)
        for i, sid in enumerate(self.sensors):
            g = self.group[sid]
            if len(g) >= 5:
                with np.errstate(all="ignore"):
                    cm[:, i] = np.nan_to_num(np.nanmedian(res[:, g], axis=1))
        return cm

    def assess(self, readings: np.ndarray, expected: np.ndarray) -> list[Flag]:
        """readings, expected: [ticks, sensors] up to now (nan where missing). Returns the flags for the latest tick."""
        r, e = readings[-WINDOW:], expected[-WINDOW:]
        res = (r - e) / self.sigma
        res = res - self._common_mode(res)
        recent = np.nanmean(res[-5:], axis=0) if len(res) >= 5 else np.full(len(self.sensors), np.nan)
        out: list[Flag] = []
        for i, sid in enumerate(self.sensors):
            col = r[:, i]
            if len(col) >= MISSING_TICKS and np.isnan(col[-MISSING_TICKS:]).all():
                out.append(Flag(sid, "missing", f"No reading for the last {MISSING_TICKS} ticks"))
                continue
            tail = col[-STUCK_TICKS:]
            if len(tail) >= STUCK_TICKS and np.isfinite(tail).all() and float(tail.max() - tail.min()) < 1e-9:
                out.append(Flag(sid, "stuck", f"The same value for {STUCK_TICKS} ticks, real sensors always show noise"))
                continue
            y = res[:, i]
            ok = np.isfinite(y)
            if ok.sum() < WINDOW * 0.8 or len(res) < WINDOW:
                continue
            k = np.arange(len(y))[ok]
            slope, icpt = np.polyfit(k, y[ok], 1)
            fit = slope * k + icpt
            rss = float(((y[ok] - fit) ** 2).sum())
            se = np.sqrt(rss / max(ok.sum() - 2, 1) / max(((k - k.mean()) ** 2).sum(), 1e-9))
            t_stat = abs(slope) / max(se, 1e-9)
            walk = abs(slope) * (len(y) - 1)
            corr = abs(np.corrcoef(k, y[ok])[0, 1])
            if walk < DRIFT_Z or t_stat < DRIFT_T or corr < DRIFT_CORR:
                continue
            others = [abs(recent[j]) for j in self.neigh[sid] if np.isfinite(recent[j])]
            if others and max(others) > ISOLATED_Z:
                continue  # its own asset's other sensors move too: the process changed, not the sensor
            peers = [recent[j] for j in self.group[sid] if j != i and np.isfinite(recent[j])]
            if peers and np.mean(np.abs(peers) > ISOLATED_Z) > COMMON_SHARE:
                continue  # many sensors of this kind deviate at once
            out.append(Flag(sid, "drift", f"Reading is walking away from the twin's expectation: {walk:.1f} standard deviations over {len(y)} ticks"))
        return out


def model_for(bench, sigma: np.ndarray) -> QualityModel:
    topo = bench.g.topology
    return QualityModel(bench.sensors, sigma, {s.id: s.asset_id for s in topo.sensors}, {s.id: f"{s.network}:{s.kind}" for s in topo.sensors})


def live_quality(rt, qm: QualityModel) -> list[Flag]:
    """Flags for the running twin, from the runtime's reading history and the shadow twin's expectation."""
    n = min((len(rt.history.get(s, [])) for s in qm.sensors), default=0)
    if n < WINDOW:
        return []
    take = WINDOW
    r = np.array([rt.history[s][-take:] for s in qm.sensors], dtype=float).T
    e = np.array([[np.nan if v is None else v for v in rt.expected_history.get(s, [])[-take:]] for s in qm.sensors], dtype=float).T
    return qm.assess(r, e) if r.shape == e.shape else []


# ---------- offline evaluation, on the same fault runs as the detectors ----------
INJECT = (("sensor_dropout", 1.0, "Sensor stopped reporting", "missing"), ("stuck_sensor", 1.0, "Sensor stuck on one value", "stuck"),
          ("sensor_drift", 0.5, "Slow drift, 0.5% of range per minute", "drift"), ("sensor_drift", 1.0, "Fast drift, 1% of range per minute", "drift"))
CHECK_EVERY = 3


def _first_flag(qm: QualityModel, run, sensor: str, status: str) -> int | None:
    from app.lab.harness import ONSET, TICKS
    for k in range(ONSET + 1, TICKS, CHECK_EVERY):
        if any(f.sensor_id == sensor and f.status == status for f in qm.assess(run.readings[:k], run.expected[:k])):
            return k - ONSET
    return None


def evaluate(bench, sigma: np.ndarray, clean_test: list, rng: np.random.Generator, n_sensors: int = 16) -> dict:
    from app.lab.harness import ONSET, TICKS, Fault, random_start
    qm = model_for(bench, sigma)
    flagged = set()
    for i, r in enumerate(clean_test):
        for k in range(40, TICKS, 6):
            flagged |= {(i, f.sensor_id) for f in qm.assess(r.readings[:k], r.expected[:k])}
    sensor_hours = len(clean_test) * len(bench.sensors) * TICKS / 60
    process = [Fault("pipe_burst", bench.g.zone_pipe["zone-gov"], 0.6), Fault("pipe_burst", bench.g.zone_pipe["zone-cbd"], 1.0),
               Fault("substation_trip", "elec-sub-02"), Fault("heat_wave", None, 46), Fault("holiday", None, 1.5),
               Fault("pump_failure", bench.g.zone_pump["zone-cbd"]), Fault("cooling_fault", "cool-plant-01", 0.7)]
    pflags = 0
    for i, f in enumerate(process):
        r = bench.run(f, 4000 + i, random_start(rng))
        seen = set()
        for k in range(ONSET + 1, TICKS, 6):
            seen |= {x.sensor_id for x in qm.assess(r.readings[:k], r.expected[:k])}
        pflags += len(seen)
    picks = [bench.sensors[int(j)] for j in rng.choice(len(bench.sensors), size=n_sensors, replace=False)]
    rows = []
    for kind, sev, label, status in INJECT:
        delays = []
        for j, sid in enumerate(picks):
            r = bench.run(Fault(kind, sid, sev), 6000 + j, random_start(rng))
            delays.append(_first_flag(qm, r, sid, status))
        rows.append(dict(kind=f"{kind}:{sev:g}", label=label, delays=delays))
    return dict(rows=rows, flagged_clean=len(flagged), sensor_hours=sensor_hours, process_flags=pflags, process_runs=len(process), sensors=picks)
