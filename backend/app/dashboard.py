"""Numbers behind the dashboard: KPIs and chart series.

Everything is computed from the twin. History comes from a separate baseline engine (no events), so charts show
what normal looks like and the live twin is never disturbed. The newest point of each series is the live value.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from app import ml
from app.cost import TONS_PER_MW
from app.schemas import DailySeries, DailyValue, DemandSeries, ForecastMetrics, ForecastPoint, Kpi, NamedSeries, SeriesPoint, SubstationSeries
from app.sim.engine import CAIRO, Engine
from app.sim.topology import SUBSTATION_BACKUP

if TYPE_CHECKING:
    from app.ml.service import MLService
    from app.runtime import Runtime

SUB_CAPACITY_MW = 110.0  # firm capacity per substation (assumption shown as the chart's capacity line)
TARGET_NRW = 10.0  # percent, the common international benchmark for well-run utilities
TARGET_KW_PER_TR = 0.80  # plant efficiency target for district cooling
UNITS = {"water": "m3/h", "electricity": "MW", "cooling": "MW"}


class History:
    """Hourly baseline history on its own engine. Serialised and cached because replaying 30 days costs about 0.3 s."""

    def __init__(self, seed: int):
        self.engine = Engine(seed)
        self.lock = threading.Lock()
        self._cache: OrderedDict[tuple, list[tuple[datetime, dict]]] = OrderedDict()

    def hourly(self, end: datetime, hours: int) -> list[tuple[datetime, dict]]:
        end_h = end.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        key = (end_h, hours)
        with self.lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            rows = self.engine.history(end_h - timedelta(hours=hours), hours, 60)
            self._cache[key] = rows
            while len(self._cache) > 6:
                self._cache.popitem(last=False)
            return rows


def nrw(r: dict) -> float:
    p = r["kpi:water_production"]
    return (p - r["kpi:water_billed"]) / p * 100 if p > 0 else 0.0


def kw_per_tr(r: dict) -> float | None:
    d = r["kpi:cooling_delivered_mw"]
    return r["kpi:cooling_elec_mw"] * 1000 / (d * TONS_PER_MW) if d > 0.5 else None


def _live_row(rt: "Runtime") -> dict:
    return {f"kpi:{k}": v for k, v in rt.engine.metrics.items()}


def _pts(rows, fn, now: datetime, live: float | None) -> list[SeriesPoint]:
    out = [SeriesPoint(t=t, v=round(v, 4)) for t, r in rows if (v := fn(r)) is not None]
    if live is not None:
        out.append(SeriesPoint(t=now, v=round(live, 4)))
    return out


def build_kpis(rt: "Runtime", hist: History, savings_total: float | None) -> list[Kpi]:
    now = rt.now
    rows = hist.hourly(now, 24)
    live = _live_row(rt) or rows[-1][1]  # right after a reset the twin has not ticked yet
    first = rows[0][1]
    g = rt.engine.g

    nrw_now, nrw_before = nrw(live), nrw(first)
    prod_now = live["kpi:water_production"]
    elec_now = live["kpi:elec_total_mw"]
    cap = SUB_CAPACITY_MW * len(SUBSTATION_BACKUP)
    kwtr_now, kwtr_before = kw_per_tr(live), kw_per_tr(first)

    affected = {a.zone_id for a in g.topology.assets
                if rt.asset_status.get(a.id) and rt.asset_status[a.id].status in ("offline", "fault")}
    served = len(g.topology.zones) - len(affected)

    out = [
        Kpi(id="nrw", label="Non-revenue water", value=round(nrw_now, 1), unit="%", digits=1, target=TARGET_NRW, better="lower",
            delta=round(nrw_now - nrw_before, 1), series=_pts(rows, nrw, now, nrw_now), network="water",
            note=f"Target {TARGET_NRW:g}%. Water produced but not billed.", formula="(production − billed) ÷ production"),
        Kpi(id="water_production", label="Water production", value=round(prod_now), unit="m3/h", better=None,
            delta=round(prod_now - first["kpi:water_production"]), series=_pts(rows, lambda r: r["kpi:water_production"], now, prod_now), network="water",
            note="Reservoir outflow into the district.", formula="reservoir flow sensor"),
        Kpi(id="electric_load", label="Electrical load", value=round(elec_now, 1), unit="MW", digits=1, better=None,
            delta=round(elec_now - first["kpi:elec_total_mw"], 1), series=_pts(rows, lambda r: r["kpi:elec_total_mw"], now, elec_now), network="electricity",
            note=f"{elec_now / cap:.0%} of {cap:g} MW firm capacity.", formula="sum of substation loads"),
    ]
    if kwtr_now is not None:
        out.append(Kpi(id="cooling_efficiency", label="Cooling efficiency", value=round(kwtr_now, 2), unit="kW/TR", digits=2, target=TARGET_KW_PER_TR, better="lower",
                       delta=round(kwtr_now - kwtr_before, 2) if kwtr_before else None,
                       series=_pts(rows, kw_per_tr, now, kwtr_now), network="cooling",
                       note=f"Target {TARGET_KW_PER_TR:.2f}. Electricity per ton of cooling.", formula="chiller electric power ÷ cooling delivered"))
    out.append(Kpi(id="districts_served", label="Districts fully served", value=served, unit=f"of {len(g.topology.zones)}", better="higher",
                   note="Districts with no offline or faulted asset.", formula="zones without offline or faulted assets"))
    if savings_total is not None:
        out.append(Kpi(id="savings_available", label="Savings available", value=round(savings_total), unit="EGP / month", better="higher",
                       note="Not yet captured. Open the Savings advisor.", formula="sum of recommended opportunities"))
    return out


def _slot(now: datetime) -> datetime:
    return now.replace(minute=0 if now.minute < 30 else 30, second=0, microsecond=0) + timedelta(minutes=30)


def demand_series(rt: "Runtime", hist: History, mls: "MLService", network: str, zone_id: str, hours: int = 24) -> DemandSeries:
    g = rt.engine.g
    zones = [z.id for z in g.topology.zones if (network, z.id) in mls.models.series] if zone_id == "all" else [zone_id]
    if not zones or any((network, z) not in mls.models.series for z in zones):
        raise KeyError(f"{network}/{zone_id}")
    idx = ("water", "electricity", "cooling").index(network)
    rows = hist.hourly(rt.now, hours)
    actual = [SeriesPoint(t=t, v=round(sum(r[f"demand:{network}:{z}"] for z in zones), 2)) for t, r in rows]
    if all(z in rt.engine.zone_demand for z in zones):  # not before the first tick after a reset
        actual.append(SeriesPoint(t=rt.now, v=round(sum(rt.engine.zone_demand[z][idx] for z in zones), 2)))

    start = _slot(rt.now)
    fcs = [ml.forecast(mls.models, network, z, start, hours) for z in zones]
    pts = [ForecastPoint(timestamp=fcs[0].points[i].timestamp, predicted=round(sum(f.points[i].predicted for f in fcs), 2),
                         lower=round(sum(f.points[i].lower for f in fcs), 2), upper=round(sum(f.points[i].upper for f in fcs), 2))
           for i in range(len(fcs[0].points))]
    mae, rmse, mape = mls.models.metrics[network]
    return DemandSeries(network=network, zone_id=zone_id, unit=UNITS[network], now=rt.now, actual=actual, forecast=pts,
                        metrics=ForecastMetrics(mae=round(mae, 2), rmse=round(rmse, 2), mape=round(mape, 2)))


def substation_series(rt: "Runtime", hist: History, hours: int = 24) -> SubstationSeries:
    rows = hist.hourly(rt.now, hours)
    names = {a.id: a.name for a in rt.engine.g.topology.assets if a.kind == "substation"}
    lines = []
    for sid in SUBSTATION_BACKUP:
        pts = [SeriesPoint(t=t, v=round(r[f"{sid}-load"], 2)) for t, r in rows]
        live = rt.latest.get(f"{sid}-load")
        if live and live.value is not None:
            pts.append(SeriesPoint(t=rt.now, v=round(live.value, 2)))
        lines.append(NamedSeries(id=sid, name=names[sid], points=pts))
    return SubstationSeries(capacity=SUB_CAPACITY_MW, lines=lines)


def cooling_efficiency_daily(rt: "Runtime", hist: History, days: int = 30) -> DailySeries:
    rows = hist.hourly(rt.now, days * 24)
    by_day: dict[str, list[dict]] = {}
    for t, r in rows:
        by_day.setdefault(t.astimezone(CAIRO).date().isoformat(), []).append(r)
    out = []
    for d, rs in by_day.items():
        if len(rs) < 12:  # skip a day we only saw the edge of
            continue
        deliv = sum(r["kpi:cooling_delivered_mw"] for r in rs)
        if deliv <= 0:
            continue
        out.append(DailyValue(date=d, value=round(sum(r["kpi:cooling_elec_mw"] for r in rs) * 1000 / (deliv * TONS_PER_MW), 3),
                              ambient_max=round(max(r["kpi:ambient"] for r in rs), 1)))
    return DailySeries(unit="kW/TR", target=TARGET_KW_PER_TR, days=out)
