"""Live ML: twin-expected values on readings, failure probabilities, forecasts and metrics."""

from __future__ import annotations

import asyncio
import logging
import pickle
from collections import deque
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from app import ml
from app.ml.evaluate import evaluate
from app.runtime import Runtime
from app.schemas import (
    AssetStatus, BaselineScore, CorrectionScore, DemandSeries, FeatureImportance, Forecast, ForecastDrivers, ForecastEvidence,
    ForecastMetrics, ForecastPoint, HourError, ModelComparisonRow, ModelMetrics, ModelScore, ScenarioScore, SeriesPoint, ZoneScore,
)
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
        self.recent: deque = deque(maxlen=60)  # (time, {zone: (water, electricity, cooling)}) of the latest ticks, the live correction's input
        self.retraining = False
        rt.tick_hooks.insert(0, self.tick)
        rt.reset_hooks.append(self.shadow.reset)
        rt.reset_hooks.append(self.recent.clear)

    async def tick(self, rt: Runtime) -> None:
        _, truth, _ = self.shadow.step(rt.now)
        rt.forecasts = {k: round(float(v), 3) for k, v in truth.items()}
        self.recent.append((rt.now, dict(rt.engine.zone_demand)))
        if rt.engine.tick % FAILURE_EVERY == 1:
            await self._failure(rt)
        if rt.engine.tick % FORECAST_EVERY == 2:
            await rt.broadcast("forecast_update", self.forecast("water", "zone-gov", 24))
            if ml.is_stale(self.models, rt.now) and not self.retraining:
                self.retraining = True
                asyncio.create_task(self._retrain())

    async def _retrain(self) -> None:
        """Daily refresh: train on the latest window in a thread, on a private engine so the live twin is untouched."""
        try:
            fresh = await asyncio.to_thread(lambda: ml.train(Engine(self.rt.seed)))
            fresh.detection = self.models.detection
            self.models = fresh
            ml.MODELS.write_bytes(pickle.dumps(fresh))
            log.info("forecast models refreshed through %s", fresh.trained_through.date())
        except Exception:  # noqa: BLE001
            log.exception("daily retrain failed, keeping the current models")
        finally:
            self.retraining = False

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

    # ---------- forecasting ----------
    def scenario(self) -> tuple[float, float]:
        """(heat wave peak deg C, holiday factor) of the scenarios running now, 0 when none."""
        eng = self.rt.engine
        return (max((e.severity for e in eng._active("heat_wave")), default=0.0),
                max((e.severity for e in eng._active("holiday")), default=0.0))

    def weather(self, heat: float):
        """The weather feed: the air temperature the twin will have, plus the feed's assumed error."""
        eng, seed = self.rt.engine, self.rt.seed

        def feed(times: pd.DatetimeIndex) -> np.ndarray:
            sec = np.array([t.timestamp() for t in times])
            return np.array([eng.ambient_at(t.to_pydatetime(), heat) for t in times]) + ml.weather_error(sec, seed)
        return feed

    def _start(self) -> datetime:
        n = self.rt.now
        return n.replace(minute=0 if n.minute < 30 else 30, second=0, microsecond=0) + timedelta(minutes=30)

    def live_ratio(self, network: str, zone_id: str, heat: float, holiday: float) -> float:
        """Live demand over what the model says for right now. Averages the latest ticks; 1 until there are enough."""
        i = ml.NETWORKS.index(network)
        obs = [d[zone_id][i] for _, d in list(self.recent)[-30:] if zone_id in d]
        if len(obs) < 15:
            return 1.0
        now = pd.DatetimeIndex([pd.Timestamp(self.rt.now)])
        pred = float(ml.predict(self.models, network, zone_id, now, self.weather(heat)(now), holiday)[0])
        return float(np.clip(np.mean(obs) / max(pred, 1e-6), 0.7, 1.4))

    def forecast(self, network: str, zone_id: str, horizon_hours: int, scenario: bool = True) -> Forecast:
        heat, holiday = self.scenario() if scenario else (0.0, 0.0)
        anchor = self.live_ratio(network, zone_id, heat, holiday) if scenario else 1.0
        return ml.forecast(self.models, network, zone_id, self._start(), horizon_hours, weather=self.weather(heat), holiday=holiday,
                           heat=heat, anchor=anchor, now=self.rt.now)

    def _total(self, network: str, zone_id: str, fcs: list[Forecast], regime: str) -> list[ForecastPoint]:
        """Sum districts. The band of a total is its own measured band, not the sum of the district bands."""
        lo, hi = self.models.band[(network, zone_id if zone_id == "all" else fcs[0].zone_id, regime)]
        n = len(fcs[0].points)
        out = []
        for i in range(n):
            p = sum(f.points[i].predicted for f in fcs)
            out.append(ForecastPoint(timestamp=fcs[0].points[i].timestamp, predicted=round(p, 2), lower=round(p * (1 + min(lo, 0)), 2),
                                     upper=round(p * (1 + max(hi, 0)), 2)))
        return out

    def demand_view(self, network: str, zone_id: str, hours: int, times: list[datetime], actual: list[float]) -> dict:
        """Everything the forecast chart adds to the measured series: forecast, normal-day baseline, hindcast and drivers."""
        m = self.models
        ids = [z.id for z in self.rt.engine.g.topology.zones if (network, z.id) in m.series] if zone_id == "all" else [zone_id]
        key = "all" if zone_id == "all" else zone_id
        heat, holiday = self.scenario()
        regime = ml.regime(heat, holiday)
        fcs = [self.forecast(network, z, hours) for z in ids]
        pts = self._total(network, key, fcs, regime)
        baseline = None
        if heat or holiday:
            baseline = self._total(network, key, [self.forecast(network, z, hours, scenario=False) for z in ids], "normal")
        idx = pd.DatetimeIndex([pd.Timestamp(t) for t in times])
        weather0 = self.weather(0.0)(idx)
        hind = np.sum([ml.predict(m, network, z, idx, weather0) for z in ids], axis=0)
        act = np.array(actual)
        ok = act > 0
        mape = float(np.mean(np.abs(hind[ok] - act[ok]) / act[ok]) * 100) if ok.any() else None
        ev = m.evidence[network]
        score = ev["model"] if zone_id == "all" else ev["zones"][zone_id]
        future = pd.DatetimeIndex([pd.Timestamp(p.timestamp) for p in pts])
        ratios = [self.live_ratio(network, z, heat, holiday) for z in ids]
        drivers = ForecastDrivers(
            temperature_now=round(float(self.rt.engine.ambient), 1), temperature_peak=round(float(self.weather(heat)(future).max()), 1),
            heat_wave=heat or None, holiday=holiday or None,
            live_correction=round(float(np.mean(ratios)), 3) if m.anchor_tau.get(network) else 1.0,
            correction_hours=float(m.anchor_tau.get(network, 0)), weather_error_c=ml.WEATHER_SIGMA, trained_through=m.trained_through)
        return dict(forecast=pts, baseline=baseline, hindcast=[SeriesPoint(t=t, v=round(float(v), 2)) for t, v in zip(times, hind)],
                    hindcast_mape=None if mape is None else round(mape, 2), drivers=drivers,
                    metrics=ForecastMetrics(mae=round(score["mae"], 2), rmse=round(score["rmse"], 2), mape=round(score["mape"], 2)))

    def evidence(self, network: str) -> ForecastEvidence:
        m = self.models
        e = m.evidence[network]
        names = {z.id: z.name for z in self.rt.engine.g.topology.zones}
        sc = e["scenario"]
        return ForecastEvidence(
            network=network, unit=e["unit"], train_days=e["train_days"], test_days=e["test_days"], points=e["points"],
            trained_through=m.trained_through, weather_error_c=e["weather_error_c"],
            model=ModelScore(**{k: round(v, 3) for k, v in e["model"].items()}),
            comparison=[ModelComparisonRow(**{k: round(v, 3) if k in ("mae", "rmse", "mape", "bias", "fit_seconds") else v for k, v in row.items()})
                        for row in e.get("comparison", [])],
            selected=e.get("selected", "lightgbm"), selected_matches_live=e.get("selected_matches_live", True),
            comparison_note=e.get("comparison_note", ""),
            baselines=[BaselineScore(name=n, mae=round(b["mae"], 3), mape=round(b["mape"], 3)) for n, b in e["baselines"].items()],
            skill=round(e["skill"], 3), coverage=round(e["coverage"], 3), nominal=e["nominal"],
            by_hour=[HourError(hour=h["hour"], mape=round(h["mape"], 2)) for h in e["by_hour"]],
            scenario=ScenarioScore(days=sc["days"], mape=round(sc["mape"], 2), mape_blind=round(sc["mape_blind"], 2)),
            live_correction=CorrectionScore(tau_hours=e["anchor"]["tau_hours"], mae_without=round(e["anchor"]["mae_without"], 3), mae_with=round(e["anchor"]["mae_with"], 3)),
            zones=[ZoneScore(zone_id=z, name=names.get(z, z), mae=round(v["mae"], 3), rmse=round(v["rmse"], 3), mape=round(v["mape"], 2)) for z, v in e["zones"].items()],
            importances=[FeatureImportance(feature=f, importance=round(v, 3)) for f, v in m.importances[f"forecast-{network}"]])

    def metrics(self) -> ModelMetrics:
        fi = {k: [FeatureImportance(feature=f, importance=round(v, 3)) for f, v in vals] for k, vals in self.models.importances.items()}
        det = {k: v for k, v in self.models.detection.items()} | {"failureAuc": round(self.models.failure_auc, 3)}
        return ModelMetrics(forecasting=ml.network_metrics(self.models), detection=det, last_run=self.models.last_run,
                            feature_importances=fi)
