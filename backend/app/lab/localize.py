"""Leak localization from precomputed signatures.

For every candidate pipe the twin is run once with a reference leak, and the residual response of all sensors (reading minus
the operator's expectation) is stored per m3/h of leak. At run time the observed residual vector is explained by one leak on
one pipe plus a demand-error term (the expectation's demand is never exact): r ~ a * s_pipe + d * s_demand, a >= 0, d free.
Pipes are ranked by the share of the residual's energy that this explains. A disabled sensor is dropped from the fit, which
is how the cost of a missing sensor shows up as a wider list of candidates.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.lab.detectors import Scale
from app.lab.harness import ONSET, Bench, Fault, random_start
from app.schemas.lab import Localization, LocalizationCandidate

REF_SEVERITY = 0.5
LEAK_PER_SEVERITY = 300.0  # m3/h at severity 1, from the engine
SIGNATURE_HOURS = (2, 8, 14, 20)
WINDOW = slice(ONSET + 25, ONSET + 40)  # after the ramp has settled
AMBIGUITY = 0.03  # score gap under which the top two are called ambiguous


@dataclass
class Localizer:
    bench: Bench
    scale: Scale
    pipes: list[str]
    signatures: np.ndarray  # [pipes, sensors], standardised residual per m3/h
    demand_sig: np.ndarray  # [sensors], standardised residual per +1% demand error

    @classmethod
    def build(cls, bench: Bench, clean_runs: list, seed: int = 0) -> "Localizer":
        scale = Scale.fit(clean_runs, bench)
        rng = np.random.default_rng(seed + 77)
        pipes = list(bench.g.zone_pipe.values())
        from datetime import datetime, timezone, timedelta
        day = datetime(2026, 10, 12, tzinfo=timezone.utc)
        sigs = []
        for i, pipe in enumerate(pipes):
            vecs = []
            for h in SIGNATURE_HOURS:
                r = bench.run(Fault("pipe_burst", pipe, REF_SEVERITY), 900 + i * 7 + h, day + timedelta(hours=h), mismatch=False)
                vecs.append(np.nanmean(r.residual[WINDOW], axis=0))
            sigs.append(np.mean(vecs, axis=0) / (LEAK_PER_SEVERITY * REF_SEVERITY) / scale.sigma)
        d = bench.run(None, 990, day + timedelta(hours=10), mismatch=False, truth_scale=1.01)
        demand = np.nanmean(d.residual[WINDOW], axis=0) / scale.sigma
        return cls(bench, scale, pipes, np.array(sigs), demand)

    def residual_from(self, readings: np.ndarray, expected: np.ndarray, disabled: frozenset[str] = frozenset()) -> np.ndarray:
        r = (readings - expected) / self.scale.sigma
        for sid in disabled:
            r[self.bench.index[sid]] = np.nan
        return r

    def localize(self, z: np.ndarray) -> Localization:
        """z: standardised residual per sensor (nan = unavailable)."""
        ok = np.isfinite(z)
        zz = z[ok]
        sst = float(zz @ zz) or 1e-9
        fits = []
        for p, sig in zip(self.pipes, self.signatures):
            X = np.column_stack([sig[ok], self.demand_sig[ok]])
            a, d = _nnls_one(X, zz)
            resid = zz - X @ np.array([a, d])
            fits.append((p, 1 - float(resid @ resid) / sst, a, d))
        fits.sort(key=lambda t: -t[1])
        g = self.bench.g
        names = {x.id: x.name for x in g.topology.assets}
        cands = [LocalizationCandidate(rank=i + 1, asset_id=p, name=names[p], zone_id=self.bench.zone_of_pipe[p], score=round(max(s, 0.0), 3),
                                       leak_m3h=round(max(a, 0.0), 1)) for i, (p, s, a, _) in enumerate(fits[:3])]
        ambiguous = len(fits) > 1 and fits[0][1] - fits[1][1] < AMBIGUITY
        return Localization(method="Residual signatures: one leak on one pipe plus a demand-error term, ranked by the share of the residual explained",
                            sensors_used=int(ok.sum()), sensors_total=len(z), demand_error_pct=round(fits[0][3], 2), candidates=cands, ambiguous=ambiguous)

    def trial(self, pipe: str, leak_m3h: float, seed: int, disabled: frozenset[str] = frozenset(), start=None, mismatch: bool = True) -> tuple[Localization, int | None]:
        rng = np.random.default_rng(seed)
        r = self.bench.run(Fault("pipe_burst", pipe, leak_m3h / LEAK_PER_SEVERITY), seed, start or random_start(rng), disabled=disabled, mismatch=mismatch)
        z = np.nanmean(r.residual[WINDOW], axis=0) / self.scale.sigma
        z[[self.bench.index[s] for s in disabled]] = np.nan
        loc = self.localize(z)
        rank = next((c.rank for c in loc.candidates if c.asset_id == pipe), None)
        return loc, rank

    def accuracy(self, sizes: list[float], per_size: int, seed: int, disabled: frozenset[str] = frozenset()) -> dict:
        rng = np.random.default_rng(seed)
        rows = []
        for size in sizes:
            hits1 = hits3 = n = 0
            for pipe in self.pipes:
                for _ in range(per_size):
                    _, rank = self.trial(pipe, size, int(rng.integers(1, 10 ** 6)), disabled)
                    n += 1
                    hits1 += rank == 1
                    hits3 += rank is not None
            rows.append({"leak_m3h": size, "top1": hits1 / n, "top3": hits3 / n, "runs": n})
        return {"rows": rows}


def _nnls_one(X: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Least squares for [a, d] with a >= 0. If the unconstrained a is negative, refit with a = 0."""
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    if coef[0] >= 0:
        return float(coef[0]), float(coef[1])
    col = X[:, 1]
    d = float(col @ y / max(col @ col, 1e-9))
    return 0.0, d
