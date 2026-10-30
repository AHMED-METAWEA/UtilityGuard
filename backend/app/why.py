"""'Why this number?': the formula, the inputs and the source of every input, for the numbers that appear on screen.

One endpoint, one shape (`WhyRecord`), a reference per surface:

    kpi:<id>              a tile on the dashboard (nrw, water_production, electric_load, cooling_efficiency, districts_served, savings_available)
    asset-health:<id>     the health score of an asset
    asset-risk:<id>       the failure risk score of an asset
    warning:<id>          an early warning and its maintenance decision
    saving:<id>           a savings opportunity
    forecast:<network>    the accuracy of the demand forecast

Incident costs, board plans and agent answers already have their own calculation records (`calc-…`).
Nothing here is written by a language model, and the numbers are the ones the screens show, read from the same functions.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app import ml
from app.cost import TONS_PER_MW, Assumptions, _a, _ai, _summary, assumption_keys
from app.dashboard import SUB_CAPACITY_MW, TARGET_KW_PER_TR, TARGET_NRW
from app.schemas import CalcInput
from app.schemas.insight import WhyRecord
from app.sim.topology import SUBSTATION_BACKUP


class Why:
    def __init__(self, rt, A: Assumptions, mls, kpis, savings):
        self.rt, self.A, self.mls = rt, A, mls
        self._kpis, self._savings = kpis, savings  # callables returning the same objects the dashboard shows

    def record(self, ref: str) -> WhyRecord:
        kind, _, key = ref.partition(":")
        fn = {"kpi": self._kpi, "asset-health": self._health, "asset-risk": self._risk, "warning": self._warning, "saving": self._saving,
              "forecast": self._forecast}.get(kind)
        if fn is None or not key:
            raise KeyError(ref)
        return fn(ref, key)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _latest(self, sensor_id: str) -> float:
        r = self.rt.latest.get(sensor_id)
        return float(r.value) if r and r.value is not None else 0.0

    # ---------- dashboard tiles ----------
    def _kpi(self, ref: str, key: str) -> WhyRecord:
        k = next((x for x in self._kpis() if x.id == key), None)
        if k is None:
            raise KeyError(ref)
        m, A = self.rt.engine.metrics, self.A
        inputs: list[CalcInput] = []
        steps: list[str] = []
        notes = [k.note] if k.note else []
        links = [{"label": "Open the dashboard", "href": "#/live"}]
        if key == "nrw":
            p, b = m["water_production"], m["water_billed"]
            inputs = [_a("Water produced", p, "m3/h", "measured", "Reservoir outflow sensor water-res-01-flow."),
                      _a("Water billed", b, "m3/h", "measured", "Sum of the billed demand of the districts, from the twin's customer meters."),
                      _a("Target", TARGET_NRW, "%", "rule", "The common international benchmark for well-run utilities (a fixed target, not a measurement).")]
            steps = [f"({p:,.1f} − {b:,.1f}) ÷ {p:,.1f} × 100 = {k.value:.1f}%"]
        elif key == "water_production":
            inputs = [_a("Reservoir outflow", self._latest("water-res-01-flow"), "m3/h", "measured", "Sensor water-res-01-flow, latest reading.")]
            steps = [f"{k.value:,.0f} m3/h is the latest reading of the reservoir flow sensor"]
        elif key == "electric_load":
            loads = [(s, self._latest(f"{s}-load")) for s in SUBSTATION_BACKUP]
            inputs = [_a(f"Load on {self.rt.engine.g.asset_index[s].name}", v, "MW", "measured", f"Sensor {s}-load, latest reading.") for s, v in loads]
            inputs.append(_a("Firm capacity per substation", SUB_CAPACITY_MW, "MW", "rule", "A fixed planning figure shown as the capacity line on the chart."))
            steps = [" + ".join(f"{v:.1f}" for _, v in loads) + f" = {sum(v for _, v in loads):.1f} MW"]
        elif key == "cooling_efficiency":
            e, d = m["cooling_elec_mw"], m["cooling_delivered_mw"]
            inputs = [_a("Chiller electric power", e, "MW", "measured", "Sum of the plants' modelled electric draw."),
                      _a("Cooling delivered", d, "MW", "measured", "Thermal output of the plants."),
                      _a("Tons per MW thermal", TONS_PER_MW, "TR/MW", "rule", "Engineering conversion: one refrigeration ton is about 3.517 kW."),
                      _a("Target", TARGET_KW_PER_TR, "kW/TR", "rule", "Planning target for district cooling plants.")]
            steps = [f"{e:.2f} MW × 1000 ÷ ({d:.2f} MW × {TONS_PER_MW}) = {k.value:.2f} kW/TR"]
        elif key == "districts_served":
            g = self.rt.engine.g
            down = sorted({a.zone_id for a in g.topology.assets if self.rt.asset_status.get(a.id) and self.rt.asset_status[a.id].status in ("offline", "fault")})
            inputs = [_a("Districts", len(g.topology.zones), "districts", "rule", "Districts in the twin."),
                      _a("Districts with an offline or faulted asset", len(down), "districts", "measured", "From the live status of every asset.")]
            steps = [f"{len(g.topology.zones)} − {len(down)} = {k.value:.0f}"]
            notes += [f"Affected: {', '.join(down)}."] if down else []
        elif key == "savings_available":
            rep = self._savings()
            inputs = [_a(o.title, o.monthly_saving, "EGP/month", "assumed", "Each opportunity is a named formula over 30 days of baseline history and editable assumptions.") for o in rep.opportunities]
            steps = [" + ".join(f"{o.monthly_saving:,.0f}" for o in rep.opportunities) + f" = {k.value:,.0f} EGP/month"] if rep.opportunities else []
            links = [{"label": "Open the savings advisor", "href": "#/savings"}]
        return WhyRecord(ref=ref, title=k.label, value=k.value, unit=k.unit, evidence=k.evidence, formula=k.formula, steps=steps, inputs=inputs, notes=notes,
                         links=links, computed_at=self._now())

    # ---------- assets ----------
    def _asset(self, ref: str, key: str):
        a = self.rt.engine.g.asset_index.get(key)
        st = self.rt.asset_status.get(key)
        if a is None or st is None:
            raise KeyError(ref)
        return a, st

    def _health(self, ref: str, key: str) -> WhyRecord:
        a, st = self._asset(ref, key)
        worst, worst_s = 0.0, None
        for s in (s for s in self.rt.engine.g.topology.sensors if s.asset_id == key):
            v = self._latest(s.id)
            lo, hi = s.normal_range.min, s.normal_range.max
            dev = max(lo - v, v - hi, 0) / max(hi - lo, 1e-6)
            if dev >= worst:
                worst, worst_s = dev, (s, v)
        base = self.rt._base_health[key]
        offline = st.status == "offline"
        inputs = [_a("Base condition", base, "points", "rule", "A fixed value per asset in the simulation (seeded, between 84 and 98). It stands for the asset's physical condition and is not measured from a sensor.")]
        if worst_s:
            s, v = worst_s
            inputs.append(_a(f"Worst sensor deviation ({s.id})", worst, "of normal range", "measured",
                             f"Reading {v:.2f} {s.unit} against the normal range {s.normal_range.min:g} to {s.normal_range.max:g}; 0 when inside it."))
        inputs += [_a("Penalty per unit deviation", 50, "points", "rule", "Fixed weight of the deviation (capped at 1.5 times the range)."),
                   _a("Offline penalty", 40 if offline else 0, "points", "rule", "Applied when the asset is offline.")]
        steps = [f"{base:.1f} − min({worst:.2f}, 1.5) × 50 − {40 if offline else 0} = {max(0.0, base - min(worst, 1.5) * 50 - (40 if offline else 0)):.1f}"]
        return WhyRecord(ref=ref, title=f"Health of {a.name}", value=st.health_score, unit="points", evidence="calculated",
                         formula="base condition − 50 × min(worst sensor deviation, 1.5) − 40 if offline, never below 0", steps=steps, inputs=inputs,
                         notes=["Health is a simple indicator for ranking attention, not an engineering condition assessment."],
                         links=[{"label": "Open the live twin", "href": "#/live"}], computed_at=self._now())

    def _risk(self, ref: str, key: str) -> WhyRecord:
        a, st = self._asset(ref, key)
        if st.failure_probability is None:
            raise KeyError(ref)
        f = self.mls.features(key)
        names = [("Age", "years", "rule", "A fixed value per asset in the simulation (seeded)."),
                 ("Running hours", "thousand h", "rule", "A fixed value per asset in the simulation (seeded)."),
                 ("Deviation from the twin", "of normal range", "measured", "How far the asset's sensors are from what the twin expects now, as a wear proxy."),
                 ("Signal variability", "of normal range", "measured", "Standard deviation of the latest readings relative to the normal range."),
                 ("Load factor", "of normal range", "measured", "Where the loading sits inside the normal range."),
                 ("Temperature above normal", "°C", "measured", "How far a temperature sensor is above its normal maximum.")]
        inputs = [_a(n, round(float(v), 3), u, s, b) for (n, u, s, b), v in zip(names, f)]
        imp = ", ".join(f"{k} {v:.0%}" for k, v in self.mls.models.importances.get("failure", [])[:4])
        return WhyRecord(ref=ref, title=f"Failure risk of {a.name}", value=st.failure_probability, unit="score", evidence="model",
                         formula="gradient-boosting classifier over the six inputs below (risk score between 0 and 1)",
                         steps=[f"inputs: {', '.join(f'{n} {float(v):.2f}' for (n, *_), v in zip(names, f))} → score {st.failure_probability:.2f}"], inputs=inputs,
                         notes=["The model is trained on a synthetic failure hazard (age, hours, deviation, variability, load, temperature), so this is a ranking score, not a calibrated probability of failing."]
                         + ([f"Inputs the model leans on most: {imp}."] if imp else []),
                         links=[{"label": "See what could fail next", "href": "#/executive"}], computed_at=self._now())

    # ---------- early warning ----------
    def _warning(self, ref: str, key: str) -> WhyRecord:
        w = self.mls.warnings.warnings.get(key)
        if w is None:
            raise KeyError(ref)
        from app.savings import _failure_facts
        A = self.A
        f = _failure_facts(self.rt, self.rt.engine.g.asset_index[w.asset_id], A)
        n = _summary(f, A)
        inputs = [_a("Risk score now", w.cost.risk, "score", "forecast", "The failure model's score for this asset (a ranking, not a calibrated probability)."),
                  _a("Risk score from age and load alone", w.peer_risk, "score", "forecast", "The same score with the condition signals set to the median of the other assets of the same kind."),
                  _a("Cost if it fails", w.cost.unplanned, "EGP", "rule", f"Losses until the crew works ({n['c_delay']:,.0f} EGP/h) over the delay plus a late repair; cost model pump-failure proxy."),
                  _a("Cost of planned maintenance", w.cost.planned, "EGP", "rule", "planned_factor × emergency repair cost")]
        inputs += [_a(s.label, s.value, s.unit, "measured", f"The median of the other assets of the same kind is {s.peer_value:g}.") for s in w.signals]
        inputs += [_ai(A, "planned_factor")] + [_ai(A, k) for k in assumption_keys(f, A) if k != "planned_factor"]
        return WhyRecord(ref=ref, title=f"Early warning: {w.asset_name}", value=w.cost.expected_saving, unit="EGP", evidence="calculated",
                         formula="risk × cost if it fails − cost of planned maintenance; maintenance pays when the risk is above planned ÷ unplanned",
                         steps=[f"{w.cost.risk:.3f} × {w.cost.unplanned:,.0f} − {w.cost.planned:,.0f} = {w.cost.expected_saving:,.0f} EGP",
                                f"break-even risk = {w.cost.planned:,.0f} ÷ {w.cost.unplanned:,.0f} = {w.cost.break_even:.3f}"], inputs=inputs,
                         notes=["The warning opens when the risk score exceeds what age and load explain by 0.15 for three samples in a row while the asset is still running.",
                                "The risk model is trained on a synthetic hazard: this shows the mechanism, not field accuracy."],
                         links=[{"label": "Edit the assumptions", "href": "#/savings"}], computed_at=self._now())

    # ---------- savings and forecast ----------
    def _saving(self, ref: str, key: str) -> WhyRecord:
        o = next((x for x in self._savings().opportunities if x.id == key), None)
        if o is None:
            raise KeyError(ref)
        steps = [c.formula for c in o.calculations]
        return WhyRecord(ref=ref, title=o.title, value=o.monthly_saving, unit="EGP/month", evidence="assumed", formula=o.formula, steps=steps, inputs=o.inputs,
                         notes=[o.summary, f"Range over the registered assumptions: P10 {o.saving_range.p10:,.0f}, P50 {o.saving_range.p50:,.0f}, P90 {o.saving_range.p90:,.0f} EGP/month."],
                         links=[{"label": "Open the savings advisor", "href": f"#/savings/{key}"}], computed_at=self._now())

    def _forecast(self, ref: str, key: str) -> WhyRecord:
        if key not in ml.NETWORKS:
            raise KeyError(ref)
        ev = self.mls.evidence(key)
        best = min(ev.baselines, key=lambda b: b.mae)
        inputs = [_a("Test days", ev.test_days, "days", "rule", "The most recent days, held out from training."),
                  _a("Test points", ev.points, "points", "rule", "Half-hourly points of the network total in the test window."),
                  _a("Model error", ev.model.mae, ev.unit, "forecast", "Mean absolute error of the model on the test window."),
                  _a(f"Best simple method ({best.name})", best.mae, ev.unit, "forecast", "Mean absolute error of the best naive baseline on the same points."),
                  _a("Weather feed error assumed", ev.weather_error_c, "°C", "assumed", "Deliberate error added to the weather the model is given, so the accuracy is not flattered.")]
        return WhyRecord(ref=ref, title=f"Forecast error, {key}", value=ev.model.mape, unit="% (MAPE)", evidence="forecast",
                         formula="mean of |forecast − actual| ÷ actual × 100 over the held-out test window", steps=[f"skill = 1 − {ev.model.mae:.2f} ÷ {best.mae:.2f} = {ev.skill:.2f}"],
                         inputs=inputs, notes=["Measured on simulated data."], links=[{"label": "Open the forecast page", "href": "#/forecast"}], computed_at=self._now())
