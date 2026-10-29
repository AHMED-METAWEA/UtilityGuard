"""Forecasting and failure prediction, trained on simulated history (spec 7).

Demand forecast: one LightGBM model per network (zone is a feature), direct multi-horizon features only (no 1-step
lags), so a 24 h forecast needs no recursion. Inputs are the clock, a weather forecast, the holiday flag and demand at
the same hour yesterday and last week. Training history contains heat waves and holidays so the model learns what a
scenario does to demand; the weather forecast carries a deliberate error (WEATHER_SIGMA) so measured accuracy is honest.

The history is split in time: a development window, and a test slice nothing was fitted on. The uncertainty band comes
from the model's own relative errors on the development window, predicted out-of-fold by blocked cross-validation (so
every day's error is a true out-of-sample error and day-to-day swings are all represented). The test slice is scored against three naive
baselines, so the page can show whether the model earns its place, and how often the band really contained the truth.
Failure prediction: gradient boosting on asset condition features with labels from a known hazard function.
"""

from __future__ import annotations

import logging
import pickle
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import precision_score, recall_score, roc_auc_score
from sklearn.model_selection import train_test_split

from app.schemas import FeatureImportance, Forecast, ForecastMetrics, ForecastPoint, NetworkMetrics
from app.sim.engine import CAIRO, Engine, Event, ambient_temp
from app.sim.topology import ZONE_BASE

log = logging.getLogger("utilityguard.ml")
MODELS = Path(__file__).resolve().parents[2] / "data" / "models.pkl"
MODEL_VERSION = 5
HISTORY_DAYS = 75  # the window always ends at the start of today, so the model has seen the latest weather
FOLDS = 5  # blocked cross-validation folds over the development window
TEST_DAYS = 10
NOMINAL = 0.8  # share of real values the band is built to contain
WEATHER_SIGMA = 1.0  # deg C, assumed error of the weather forecast the model is given at prediction time
ANCHOR_TAUS = (0, 2.0, 4.0, 8.0, 16.0)  # hours over which a live correction fades; 0 = no correction
NETWORKS = ("water", "electricity", "cooling")
UNITS = {"water": "m3/h", "electricity": "MW", "cooling": "MW"}
FEATURES = ["hour_sin", "hour_cos", "hour", "weekday", "weekend", "holiday", "temperature", "lag_24h", "lag_168h", "zone_base", "zone_type"]
FEATURE_LABEL = {"hour_sin": "hour_of_day", "hour_cos": "hour_of_day", "hour": "hour_of_day", "weekday": "weekday",
                 "weekend": "weekend", "holiday": "holiday", "temperature": "temperature", "lag_24h": "same_hour_yesterday",
                 "lag_168h": "same_hour_last_week", "zone_base": "zone_size", "zone_type": "zone_type"}
ZTYPE = {"government": 0, "business": 1, "residential": 2, "services": 3}
FAIL_FEATURES = ["age_years", "run_hours_k", "vibration_trend", "pressure_variance", "load_factor", "temp_excess"]


def _feat(ts: pd.Series, temp: np.ndarray, holiday: np.ndarray | float = 0.0) -> pd.DataFrame:
    local = ts.dt.tz_convert(CAIRO)
    hour = local.dt.hour + local.dt.minute / 60
    return pd.DataFrame({
        "hour_sin": np.sin(2 * np.pi * hour / 24), "hour_cos": np.cos(2 * np.pi * hour / 24), "hour": hour,
        "weekday": local.dt.weekday, "weekend": local.dt.weekday.isin([4, 5]).astype(int),
        "holiday": np.broadcast_to(np.asarray(holiday, dtype=float), len(ts)).copy(), "temperature": temp})


def lag_values(hist: pd.Series, times: pd.DatetimeIndex, days: int) -> np.ndarray:
    """Value `days` earlier. History ends in the past, so a lag that lands after its end steps back whole weeks."""
    ref = times.tz_convert(CAIRO) - pd.Timedelta(days=days)
    end = hist.index[-1]
    weeks = np.ceil(np.maximum((ref - end) / pd.Timedelta(days=7), 0))
    ref = ref - pd.to_timedelta(weeks * 7, unit="D")
    return hist.values[hist.index.get_indexer(ref, method="nearest")]


def scenario_calendar(seed: int, start: date) -> dict[date, tuple[float, float]]:
    """Days of the training history with a heat wave (peak deg C) or a holiday (residential factor).
    The development window and the test slice both get some, so the model learns them and the test can score them."""
    rng = np.random.default_rng(seed + 21)
    cal: dict[date, tuple[float, float]] = {}
    windows = [(0, HISTORY_DAYS - TEST_DAYS, 10), (HISTORY_DAYS - TEST_DAYS, HISTORY_DAYS, 2)]
    for lo, hi, n in windows:
        days = [d for d in range(lo + 1, hi - 1) if (start + timedelta(days=d)).weekday() not in (4, 5)]
        picks = rng.choice(days, size=2 * n, replace=False)
        for d in picks[:n]:
            cal[start + timedelta(days=int(d))] = (float(rng.uniform(41, 48)), 0.0)
        for d in picks[n:]:
            cal[start + timedelta(days=int(d))] = (0.0, float(rng.uniform(1.15, 1.6)))
    return cal


@dataclass
class Models:
    version: int = MODEL_VERSION
    demand: dict = field(default_factory=dict)  # network -> median model
    metrics: dict = field(default_factory=dict)  # network -> (mae, rmse, mape) of the whole network on the test slice
    importances: dict = field(default_factory=dict)
    series: dict = field(default_factory=dict)  # (network, zone) -> normal-day history, the source of the lag inputs
    zone_types: dict = field(default_factory=dict)
    band: dict = field(default_factory=dict)  # (network, zone or "all", "normal", "heat" or "holiday") -> (low, high) relative error quantiles
    anchor_tau: dict = field(default_factory=dict)  # network -> hours a live correction lasts (0 = none)
    evidence: dict = field(default_factory=dict)  # network -> everything the accuracy page shows
    failure: GradientBoostingClassifier | None = None
    failure_auc: float = 0.0
    detection: dict = field(default_factory=dict)
    last_run: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    trained_through: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def weather_error(seconds: np.ndarray, seed: int) -> np.ndarray:
    """Error of the weather feed, deg C. Smooth in time like a real forecast's (a front arrives late, not jittering every half hour)."""
    ph = np.random.default_rng(seed + 31).uniform(0, 2 * np.pi, 2)
    return WEATHER_SIGMA * (np.sin(2 * np.pi * seconds / (13 * 3600) + ph[0]) + np.sin(2 * np.pi * seconds / (5 * 3600) + ph[1]))


def regime(heat: float, holiday: float) -> str:
    return "heat" if heat else "holiday" if holiday else "normal"


def _stats(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    e = p - y
    return {"mae": float(np.mean(np.abs(e))), "rmse": float(np.sqrt(np.mean(e ** 2))),
            "mape": float(np.mean(np.abs(e) / np.maximum(y, 1e-6)) * 100), "bias": float(np.mean(e) / max(np.mean(y), 1e-6) * 100)}


def _anchored(y: np.ndarray, p: np.ndarray, tau: float, step_h: float, horizon: int, every: int = 4, back: int = 4):
    """Replay the live correction: at each origin scale the next `horizon` steps by the recent actual/forecast ratio, fading over tau."""
    ys, ps = [], []
    k = np.arange(1, horizon + 1)
    fade = np.exp(-k * step_h / tau) if tau else np.zeros(horizon)
    for i in range(back, len(y) - horizon, every):
        ratio = np.clip(y[i - back + 1:i + 1].mean() / max(p[i - back + 1:i + 1].mean(), 1e-6), 0.7, 1.4)
        ys.append(y[i + 1:i + 1 + horizon])
        ps.append(p[i + 1:i + 1 + horizon] * (1 + (ratio - 1) * fade))
    return np.concatenate(ys), np.concatenate(ps)


def window_end(now: datetime) -> datetime:
    """Start of today in Cairo, as UTC. Training always ends here, so it sees the days right before the live ones."""
    return now.astimezone(CAIRO).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def train(engine: Engine, step_minutes: int = 30, now: datetime | None = None) -> Models:
    t0 = time.time()
    end = window_end(now or datetime.now(timezone.utc))
    start = end - timedelta(days=HISTORY_DAYS)
    cal = scenario_calendar(engine.seed, start.astimezone(CAIRO).date())

    def schedule(ts: datetime) -> list[Event]:
        heat, hol = cal.get(ts.astimezone(CAIRO).date(), (0.0, 0.0))
        return ([Event("heat_wave", None, heat)] if heat else []) + ([Event("holiday", None, hol)] if hol else [])

    def frame(rows):
        return pd.DataFrame([r for _, r in rows], index=pd.DatetimeIndex([t for t, _ in rows]))

    hours = HISTORY_DAYS * 24
    clean = frame(engine.history(start, hours, step_minutes))  # a normal-day history, the lag source
    df = frame(engine.history(start, hours, step_minutes, schedule=schedule))  # the same days with the scenarios in
    m = Models(zone_types={z.id: z.type for z in engine.g.topology.zones}, trained_through=end)
    per_day = 24 * 60 // step_minutes
    t_test = df.index[-TEST_DAYS * per_day]
    ts_all = pd.Series(df.index)
    holiday = np.array([cal.get(t.astimezone(CAIRO).date(), (0, 0))[1] for t in df.index])  # the holiday's residential factor, 0 on normal days
    heat = np.array([1.0 if cal.get(t.astimezone(CAIRO).date(), (0, 0))[0] else 0.0 for t in df.index])
    w_err = weather_error(df.index.astype("int64").values / 1e9, engine.seed)
    temp_fc = df["weather:ambient"].values + w_err  # weather feed with its error
    temp_blind = clean["weather:ambient"].values + w_err  # a feed that never heard of the heat wave
    step_h = step_minutes / 60

    for net in NETWORKS:
        parts = []
        for z, ztype in m.zone_types.items():
            y = df[f"demand:{net}:{z}"]
            if y.mean() < 0.05:
                continue
            base = clean[f"demand:{net}:{z}"]
            m.series[(net, z)] = base
            f = _feat(ts_all, df["weather:ambient"].values, holiday)
            f["temp_fc"], f["temp_blind"] = temp_fc, temp_blind
            zb = ZONE_BASE[z][NETWORKS.index(net)]
            f["lag_24h_raw"], f["lag_168h_raw"] = base.shift(per_day).values, base.shift(7 * per_day).values
            f["lag_24h"], f["lag_168h"] = f["lag_24h_raw"] / zb, f["lag_168h_raw"] / zb  # the model works in units of the district's base demand
            f["zone_base"], f["zone_type"] = zb, ZTYPE[ztype]
            f["y"], f["yn"], f["ts"], f["zone"], f["heat"] = y.values, y.values / zb, df.index, z, heat
            parts.append(f.dropna())
        data = pd.concat(parts)
        dev, te = data[data.ts < t_test], data[data.ts >= t_test]
        params = dict(n_estimators=300, learning_rate=0.05, num_leaves=31, min_child_samples=20, verbose=-1, random_state=engine.seed)
        fit_start = time.perf_counter()
        med = lgb.LGBMRegressor(**params).fit(dev[FEATURES], dev.yn)
        live_seconds = time.perf_counter() - fit_start
        m.demand[net] = med

        def score(model, part, temp_col="temp_fc", blind=False):
            X = part[FEATURES].copy()
            X["temperature"] = part[temp_col].values
            if blind:
                X["holiday"] = 0.0
            return model.predict(X) * part.zone_base.values

        # out-of-fold predictions on the development window: each block of days is predicted by a model that never saw it
        block = np.minimum((dev.ts.rank(method="dense").values - 1) * FOLDS // dev.ts.nunique(), FOLDS - 1).astype(int)
        oof = np.zeros(len(dev))
        for k in range(FOLDS):
            fit_start = time.perf_counter()
            fit = lgb.LGBMRegressor(**params).fit(dev[FEATURES][block != k], dev.yn[block != k])
            live_seconds += time.perf_counter() - fit_start
            oof[block == k] = score(fit, dev[block == k], "temp_fc")
        ca, te = dev.assign(p=oof), te.assign(p=score(med, te), p_blind=score(med, te, "temp_blind", True))
        # band: relative error quantiles of those out-of-fold errors, per zone and for the network total, kept apart for
        # heat-wave days because demand there is more sensitive to the weather feed's error
        for z in list(ca.zone.unique()) + ["all"]:
            sub = ca if z == "all" else ca[ca.zone == z]
            if z == "all":
                sub = sub.groupby("ts").agg(y=("y", "sum"), p=("p", "sum"), heat=("heat", "max"), holiday=("holiday", "max"))
            rel = (sub.y - sub.p) / sub.p
            for name, pick in (("normal", (sub.heat == 0) & (sub.holiday == 0)), ("heat", sub.heat > 0), ("holiday", (sub.heat == 0) & (sub.holiday > 0))):
                r = rel[pick].values
                m.band[(net, z, name)] = (float(np.quantile(r, (1 - NOMINAL) / 2)), float(np.quantile(r, 1 - (1 - NOMINAL) / 2)))

        agg = te.groupby("ts")[["y", "p", "p_blind", "lag_24h_raw", "lag_168h_raw"]].sum()
        agg["avg"] = (agg.lag_24h_raw + agg.lag_168h_raw) / 2
        agg["heat"] = te.groupby("ts").heat.max()
        agg["holiday"] = te.groupby("ts").holiday.max()
        y, p = agg.y.values, agg.p.values
        model_stats = _stats(y, p)
        base_stats = {name: _stats(y, agg[col].values) for name, col in
                      (("Same hour yesterday", "lag_24h_raw"), ("Same hour last week", "lag_168h_raw"), ("Average of both", "avg"))}
        best = min(b["mae"] for b in base_stats.values())
        edges = np.array([m.band[(net, "all", regime(h, v))] for h, v in zip(agg.heat.values, agg.holiday.values)])
        inside = (y >= p * (1 + edges[:, 0])) & (y <= p * (1 + edges[:, 1]))
        coverage = float(inside.mean())
        hour = agg.index.tz_convert(CAIRO).hour
        by_hour = [{"hour": h, "mape": float(np.mean(np.abs(agg.p[hour == h] - agg.y[hour == h]) / agg.y[hour == h]) * 100)} for h in range(24)]
        scen = (agg.heat > 0) | (agg.holiday > 0)
        scenario = {"points": int(scen.sum()), "days": int(scen.sum() // per_day),
                    "mape": _stats(y[scen], p[scen])["mape"] if scen.any() else 0.0,
                    "mape_blind": _stats(y[scen], agg.p_blind.values[scen])["mape"] if scen.any() else 0.0}
        # live correction: pick how long it should last on the out-of-fold errors, report it on the test slice
        cal_agg = ca.groupby("ts")[["y", "p"]].sum()
        errs = {tau: _stats(*_anchored(cal_agg.y.values, cal_agg.p.values, tau, step_h, per_day))["mae"] for tau in ANCHOR_TAUS}
        tau = min(errs, key=errs.get)
        m.anchor_tau[net] = tau
        ya0, pa0 = _anchored(y, p, 0, step_h, per_day)
        ya, pa = _anchored(y, p, tau, step_h, per_day)
        zones = {z: {k: v for k, v in _stats(g.y.values, g.p.values).items() if k != "bias"} for z, g in te.groupby("zone")}
        m.evidence[net] = {
            "train_days": HISTORY_DAYS - TEST_DAYS, "test_days": TEST_DAYS, "points": int(len(agg)),
            "weather_error_c": WEATHER_SIGMA, "unit": UNITS[net], "model": model_stats, "baselines": base_stats,
            "skill": 1 - model_stats["mae"] / best, "coverage": coverage, "nominal": NOMINAL, "by_hour": by_hour,
            "scenario": scenario, "anchor": {"tau_hours": tau, "mae_without": _stats(ya0, pa0)["mae"], "mae_with": _stats(ya, pa)["mae"]},
            "zones": zones,
        }
        from app.ml.comparison import compare
        m.evidence[net].update(compare(dev, te, block, oof, agg, best, live_seconds, engine.seed, per_day))
        m.metrics[net] = (model_stats["mae"], model_stats["rmse"], model_stats["mape"])
        imp: dict[str, float] = {}
        for fname, v in zip(FEATURES, med.booster_.feature_importance("gain")):
            imp[FEATURE_LABEL[fname]] = imp.get(FEATURE_LABEL[fname], 0) + float(v)
        total = sum(imp.values()) or 1
        m.importances[f"forecast-{net}"] = sorted(((k, v / total) for k, v in imp.items()), key=lambda kv: -kv[1])[:6]
        log.info("trained %s forecast mape=%.1f%% skill=%.0f%% coverage=%.0f%% anchor tau=%sh", net, model_stats["mape"], m.evidence[net]["skill"] * 100, coverage * 100, tau)

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


def is_stale(m: Models, now: datetime) -> bool:
    return window_end(now) > m.trained_through


def load_or_train(engine: Engine) -> Models:
    if MODELS.exists():
        try:
            m = pickle.loads(MODELS.read_bytes())
            if isinstance(m, Models) and getattr(m, "version", 0) == MODEL_VERSION and not is_stale(m, datetime.now(timezone.utc)):
                return m
        except Exception:  # noqa: BLE001
            log.warning("model cache unreadable, retraining")
    m = train(engine)
    MODELS.parent.mkdir(parents=True, exist_ok=True)
    MODELS.write_bytes(pickle.dumps(m))
    return m


def predict(m: Models, network: str, zone_id: str, times: pd.DatetimeIndex, temps: np.ndarray, holiday: float = 0.0) -> np.ndarray:
    """Median demand of one district at the given times, from the model alone."""
    y = m.series[(network, zone_id)]
    f = _feat(pd.Series(times), temps, holiday)
    zb = ZONE_BASE[zone_id][NETWORKS.index(network)]
    f["lag_24h"], f["lag_168h"] = lag_values(y, times, 1) / zb, lag_values(y, times, 7) / zb
    f["zone_base"] = zb
    f["zone_type"] = ZTYPE[m.zone_types[zone_id]]
    return m.demand[network].predict(f[FEATURES]) * zb


def climatology(times: pd.DatetimeIndex) -> np.ndarray:
    return np.array([ambient_temp(t.tz_convert(CAIRO).hour + t.minute / 60, t.tz_convert(CAIRO).dayofyear) for t in times])


def forecast(m: Models, network: str, zone_id: str, start: datetime, horizon_hours: int, step_minutes: int = 30, *,
             weather=None, holiday: float = 0.0, heat: float = 0.0, anchor: float = 1.0, now: datetime | None = None) -> Forecast:
    """`weather(times) -> deg C` is the weather feed; `anchor` is the live actual/forecast ratio, faded over the horizon."""
    if network not in m.demand or (network, zone_id) not in m.series:
        raise KeyError(f"{network}/{zone_id}")
    times = pd.date_range(start, periods=horizon_hours * 60 // step_minutes, freq=f"{step_minutes}min")
    p = predict(m, network, zone_id, times, (weather or climatology)(times), holiday)
    tau = m.anchor_tau.get(network, 0)
    if tau and anchor != 1.0:
        hours = np.maximum((times - pd.Timestamp(now or start)).total_seconds().values / 3600, 0)
        p = p * (1 + (anchor - 1) * np.exp(-hours / tau))
    lo, hi = m.band[(network, zone_id, regime(heat, holiday))]
    pts = [ForecastPoint(timestamp=t.to_pydatetime(), predicted=round(float(a), 2), lower=round(float(a * (1 + min(lo, 0))), 2),
                         upper=round(float(a * (1 + max(hi, 0))), 2)) for t, a in zip(times, p)]
    z = m.evidence[network]["zones"][zone_id]
    top = [FeatureImportance(feature=k, importance=round(v, 3)) for k, v in m.importances[f"forecast-{network}"][:5]]
    return Forecast(network=network, zone_id=zone_id, unit=UNITS[network], horizon_hours=horizon_hours, points=pts,
                    metrics=ForecastMetrics(mae=round(z["mae"], 2), rmse=round(z["rmse"], 2), mape=round(z["mape"], 2)), top_features=top)


def failure_probability(m: Models, features: np.ndarray) -> np.ndarray:
    return m.failure.predict_proba(features)[:, 1]


def network_metrics(m: Models) -> list[NetworkMetrics]:
    return [NetworkMetrics(network=n, mae=round(a, 2), rmse=round(b, 2), mape=round(c, 2), unit=UNITS[n]) for n, (a, b, c) in m.metrics.items()]


def detection_metrics(labels: list[bool], preds: list[bool]) -> dict[str, float]:
    p = float(precision_score(labels, preds, zero_division=0))
    r = float(recall_score(labels, preds, zero_division=0))
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0}

