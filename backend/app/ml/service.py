"""Live ML: twin-expected values on readings, failure probabilities, forecasts and metrics."""

from __future__ import annotations

import logging
import pickle
from datetime import timedelta

import numpy as np

from app import ml
from app.ml.evaluate import evaluate
from app.runtime import Runtime
from app.schemas import AssetStatus, FeatureImportance, ModelMetrics
from app.sim.engine import Engine

log = logging.getLogger("utilityguard.ml.service")

FAILURE_EVERY = 10  # ticks
FORECAST_EVERY = 150  # ticks (5 min)


class MLService:
    def __init__(self, rt: Runtime, detector):
        self.rt = rt
        self.models = ml.load_or_train(rt.engine)
        if not self.models.detection:
            self.models.detection = evaluate(lambda: Runtime(rt.seed), detector)
            ml.MODELS.write_bytes(pickle.dumps(self.models))
        # shadow twin: same seed and clock, never receives events -> the "expected" value
        self.shadow = Engine(rt.seed)
        rng = np.random.default_rng(rt.seed + 11)
        assets = rt.engine.g.topology.assets
        self.age = {a.id: float(rng.uniform(1, 22)) for a in assets}
        self.run_hours = {a.id: float(rng.uniform(5, 55)) for a in assets}
        rt.tick_hooks.insert(0, self.tick)
        rt.reset_hooks.append(self.shadow.reset)

    async def tick(self, rt: Runtime) -> None:
        _, truth, _ = self.shadow.step(rt.now)
        rt.forecasts = {k: round(float(v), 3) for k, v in truth.items()}
        if rt.engine.tick % FAILURE_EVERY == 1:
            await self._failure(rt)
        if rt.engine.tick % FORECAST_EVERY == 2:
            await rt.broadcast("forecast_update", self.forecast("water", "zone-gov", 24))

    def features(self, asset_id: str) -> list[float]:
        rt, g = self.rt, self.rt.engine.g
        sensors = [s for s in g.topology.sensors if s.asset_id == asset_id]
        vib = pvar = load = temp = 0.0
        for s in sensors:
            h = np.array([x for x in rt.history.get(s.id, [])[-30:] if x == x])
            if not len(h):
                continue
            span = max(s.normal_range.max - s.normal_range.min, 1e-6)
            exp = rt.forecasts.get(s.id, h[-1])
            vib = max(vib, abs(h[-1] - exp) / span * 2)  # deviation from the twin as a wear proxy
            pvar = max(pvar, float(np.std(h)) / span * 4)
            load = max(load, (h[-1] - s.normal_range.min) / span)
            if s.kind == "temperature":
                temp = max(temp, h[-1] - s.normal_range.max)
        return [self.age[asset_id], self.run_hours[asset_id], vib, pvar, min(load, 1.3), max(temp, 0)]

    async def _failure(self, rt: Runtime) -> None:
        ids = [a.id for a in rt.engine.g.topology.assets if a.kind in ("pump", "transformer", "pipe", "plant", "substation")]
        probs = ml.failure_probability(self.models, np.array([self.features(a) for a in ids]))
        for aid, p in zip(ids, probs):
            cur = rt.asset_status.get(aid)
            if not cur:
                continue
            p = round(float(p), 3)
            if cur.failure_probability is None or abs(cur.failure_probability - p) >= 0.03:
                new = cur.model_copy(update={"failure_probability": p})
                rt.asset_status[aid] = new
                if cur.failure_probability is not None:
                    await rt.broadcast("asset_status", new)

    def forecast(self, network: str, zone_id: str, horizon_hours: int):
        start = self.rt.now.replace(minute=0 if self.rt.now.minute < 30 else 30, second=0, microsecond=0) + timedelta(minutes=30)
        return ml.forecast(self.models, network, zone_id, start, horizon_hours)

    def metrics(self) -> ModelMetrics:
        fi = {k: [FeatureImportance(feature=f, importance=round(v, 3)) for f, v in vals] for k, vals in self.models.importances.items()}
        det = {k: v for k, v in self.models.detection.items()} | {"failureAuc": round(self.models.failure_auc, 3)}
        return ModelMetrics(forecasting=ml.network_metrics(self.models), detection=det, last_run=self.models.last_run,
                            feature_importances=fi)
