"""Forecasting and failure prediction, trained on simulated history (spec 7).

Demand forecast: one LightGBM model per network (zone is a feature), direct
multi-horizon features only (no 1-step lags), so a 24 h forecast needs no recursion.
Quantile models at 10% and 90% give the uncertainty band. Metrics come from a
held-out last 10 days. Failure prediction: gradient boosting on asset condition
features with labels from a known hazard function, evaluated on a held-out split.
"""

from __future__ import annotations

import logging
import pickle
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import precision_score, recall_score, roc_auc_score
from sklearn.model_selection import train_test_split

from app.schemas import FeatureImportance, Forecast, ForecastMetrics, ForecastPoint, NetworkMetrics
from app.sim.engine import CAIRO, Engine, ambient_temp
from app.sim.topology import ZONE_BASE

log = logging.getLogger("utilityguard.ml")
MODELS = Path(__file__).resolve().parents[2] / "data" / "models.pkl"
HISTORY_START = datetime(2026, 8, 20, tzinfo=timezone.utc)
HISTORY_DAYS = 60
TEST_DAYS = 10
UNITS = {"water": "m3/h", "electricity": "MW", "cooling": "MW"}
FEATURES = ["hour_sin", "hour_cos", "hour", "weekday", "weekend", "temperature", "lag_24h", "lag_168h", "zone_base", "zone_type"]
FEATURE_LABEL = {"hour_sin": "hour_of_day", "hour_cos": "hour_of_day", "hour": "hour_of_day", "weekday": "weekday",
                 "weekend": "weekend", "temperature": "temperature", "lag_24h": "same_hour_yesterday",
                 "lag_168h": "same_hour_last_week", "zone_base": "zone_size", "zone_type": "zone_type"}
ZTYPE = {"government": 0, "business": 1, "residential": 2, "services": 3}
FAIL_FEATURES = ["age_years", "run_hours_k", "vibration_trend", "pressure_variance", "load_factor", "temp_excess"]


def _feat(ts: pd.Series, temp: np.ndarray) -> pd.DataFrame:
    local = ts.dt.tz_convert(CAIRO)
    hour = local.dt.hour + local.dt.minute / 60
    return pd.DataFrame({
        "hour_sin": np.sin(2 * np.pi * hour / 24), "hour_cos": np.cos(2 * np.pi * hour / 24), "hour": hour,
        "weekday": local.dt.weekday, "weekend": local.dt.weekday.isin([4, 5]).astype(int), "temperature": temp})


@dataclass
class Models:
    demand: dict = field(default_factory=dict)  # network -> (median, lower, upper)
    metrics: dict = field(default_factory=dict)  # network -> (mae, rmse, mape)
    importances: dict = field(default_factory=dict)
    series: dict = field(default_factory=dict)  # (network, zone) -> pd.Series of history
    zone_types: dict = field(default_factory=dict)
    failure: GradientBoostingClassifier | None = None
    failure_auc: float = 0.0
    detection: dict = field(default_factory=dict)
    last_run: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def train(engine: Engine, step_minutes: int = 30) -> Models:
    t0 = time.time()
    hist = engine.history(HISTORY_START, HISTORY_DAYS * 24, step_minutes)
    df = pd.DataFrame([r for _, r in hist], index=pd.DatetimeIndex([t for t, _ in hist]))
    m = Models(zone_types={z.id: z.type for z in engine.g.topology.zones})
    per_day = 24 * 60 // step_minutes
    split = df.index[-TEST_DAYS * per_day]
    for net in ("water", "electricity", "cooling"):
        rows = []
        for z, ztype in m.zone_types.items():
            y = df[f"demand:{net}:{z}"]
            if y.mean() < 0.05:
                continue
            m.series[(net, z)] = y
            f = _feat(pd.Series(df.index), df["weather:ambient"].values)
            f["lag_24h"], f["lag_168h"] = y.shift(per_day).values, y.shift(7 * per_day).values
            f["zone_base"], f["zone_type"] = ZONE_BASE[z][("water", "electricity", "cooling").index(net)], ZTYPE[ztype]
            f["y"], f["ts"] = y.values, df.index
            rows.append(f.dropna())
        data = pd.concat(rows)
        tr, te = data[data.ts < split], data[data.ts >= split]
        params = dict(n_estimators=300, learning_rate=0.05, num_leaves=31, min_child_samples=20, verbose=-1, random_state=engine.seed)
        med = lgb.LGBMRegressor(**params).fit(tr[FEATURES], tr.y)
        lo = lgb.LGBMRegressor(objective="quantile", alpha=0.1, **params).fit(tr[FEATURES], tr.y)
        hi = lgb.LGBMRegressor(objective="quantile", alpha=0.9, **params).fit(tr[FEATURES], tr.y)
        pred = med.predict(te[FEATURES])
        err = pred - te.y.values
        m.metrics[net] = (float(np.mean(np.abs(err))), float(np.sqrt(np.mean(err ** 2))),
                          float(np.mean(np.abs(err) / np.maximum(te.y.values, 1e-6)) * 100))
        imp: dict[str, float] = {}
        for fname, v in zip(FEATURES, med.booster_.feature_importance("gain")):
            imp[FEATURE_LABEL[fname]] = imp.get(FEATURE_LABEL[fname], 0) + float(v)
        total = sum(imp.values()) or 1
        m.importances[f"forecast-{net}"] = sorted(((k, v / total) for k, v in imp.items()), key=lambda kv: -kv[1])[:6]
        m.demand[net] = (med, lo, hi)
        log.info("trained %s forecast mae=%.2f mape=%.1f%%", net, m.metrics[net][0], m.metrics[net][2])

    m.failure, m.failure_auc, fi = _train_failure(engine.seed)
    m.importances["failure"] = fi
    m.last_run = datetime.now(timezone.utc)
    log.info("ml training done in %.1fs", time.time() - t0)
    return m


def failure_hazard(x: np.ndarray) -> np.ndarray:
    """Ground-truth hazard used to label synthetic maintenance history."""
    age, hrs, vib, pvar, load, temp = x.T
    z = -5.2 + 0.09 * age + 0.06 * hrs + 2.4 * vib + 1.6 * pvar + 1.8 * np.maximum(load - 0.8, 0) * 5 + 0.08 * temp
    return 1 / (1 + np.exp(-z))


def _train_failure(seed: int):
    rng = np.random.default_rng(seed + 3)
    n = 6000
    X = np.column_stack([rng.uniform(0, 25, n), rng.uniform(0, 60, n), rng.gamma(1.2, 0.35, n),
                         rng.gamma(1.0, 0.3, n), rng.uniform(0.3, 1.15, n), np.maximum(rng.normal(0, 6, n), 0)])
    y = rng.uniform(size=n) < failure_hazard(X)
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.25, random_state=seed)
    clf = GradientBoostingClassifier(n_estimators=150, max_depth=3, random_state=seed).fit(Xtr, ytr)
    auc = float(roc_auc_score(yte, clf.predict_proba(Xte)[:, 1]))
    fi = sorted(zip(FAIL_FEATURES, map(float, clf.feature_importances_)), key=lambda kv: -kv[1])
    return clf, auc, fi


def load_or_train(engine: Engine) -> Models:
    if MODELS.exists():
        try:
            m = pickle.loads(MODELS.read_bytes())
            if isinstance(m, Models):
                return m
        except Exception:  # noqa: BLE001
            log.warning("model cache unreadable, retraining")
    m = train(engine)
    MODELS.parent.mkdir(parents=True, exist_ok=True)
    MODELS.write_bytes(pickle.dumps(m))
    return m


def forecast(m: Models, network: str, zone_id: str, start: datetime, horizon_hours: int, step_minutes: int = 30) -> Forecast:
    if network not in m.demand or (network, zone_id) not in m.series:
        raise KeyError(f"{network}/{zone_id}")
    med, lo, hi = m.demand[network]
    y = m.series[(network, zone_id)]
    ts = pd.Series(pd.date_range(start, periods=horizon_hours * 60 // step_minutes, freq=f"{step_minutes}min"))
    # weather forecast: climatology for the target day
    temps = np.array([ambient_temp(t.tz_convert(CAIRO).hour + t.minute / 60, t.tz_convert(CAIRO).dayofyear) for t in ts])
    f = _feat(ts, temps)
    # history is a fixed past window; align lags by time of day and weekday
    hist = y.copy()
    hist.index = hist.index.tz_convert(CAIRO)
    def lag(t, days):
        ref = t.tz_convert(CAIRO) - timedelta(days=days)
        while ref > hist.index[-1]:
            ref -= timedelta(days=7)
        return float(hist.iloc[hist.index.get_indexer([ref], method="nearest")[0]])
    f["lag_24h"] = [lag(t, 1) for t in ts]
    f["lag_168h"] = [lag(t, 7) for t in ts]
    f["zone_base"] = ZONE_BASE[zone_id][("water", "electricity", "cooling").index(network)]
    f["zone_type"] = ZTYPE[m.zone_types[zone_id]]
    p, l, u = med.predict(f[FEATURES]), lo.predict(f[FEATURES]), hi.predict(f[FEATURES])
    pts = [ForecastPoint(timestamp=t.to_pydatetime(), predicted=round(float(a), 2), lower=round(float(min(b, a)), 2),
                         upper=round(float(max(c, a)), 2)) for t, a, b, c in zip(ts, p, l, u)]
    mae, rmse, mape = m.metrics[network]
    top = [FeatureImportance(feature=k, importance=round(v, 3)) for k, v in m.importances[f"forecast-{network}"][:5]]
    return Forecast(network=network, zone_id=zone_id, unit=UNITS[network], horizon_hours=horizon_hours, points=pts,
                    metrics=ForecastMetrics(mae=round(mae, 2), rmse=round(rmse, 2), mape=round(mape, 2)), top_features=top)


def failure_probability(m: Models, features: np.ndarray) -> np.ndarray:
    return m.failure.predict_proba(features)[:, 1]


def network_metrics(m: Models) -> list[NetworkMetrics]:
    return [NetworkMetrics(network=n, mae=round(a, 2), rmse=round(b, 2), mape=round(c, 2), unit=UNITS[n]) for n, (a, b, c) in m.metrics.items()]


def detection_metrics(labels: list[bool], preds: list[bool]) -> dict[str, float]:
    p = float(precision_score(labels, preds, zero_division=0))
    r = float(recall_score(labels, preds, zero_division=0))
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0}

