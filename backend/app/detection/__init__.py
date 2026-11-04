"""Data quality, anomaly and fault detection, plus alert grouping (spec 6).

Each tick the detectors read the runtime's sensor history and emit `Detection`s.
The grouper turns them into alerts and incidents: one root cause = one incident,
and repeated detections update the same alert id instead of creating new ones.
"""

from __future__ import annotations

import itertools
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

import numpy as np
from sklearn.ensemble import IsolationForest

from app.cost import TONS_PER_MW, IncidentFacts
from app.i18n import ZONE_AR, asset_ar, sensor_ar, unit_ar
from app.schemas import Alert, Incident, Reason
from app.sim.engine import CALIBRATION_START
from app.sim.topology import COOLING_PLANTS, SUBSTATION_BACKUP, ZONE_SUBSTATION

if TYPE_CHECKING:
    from app.runtime import Runtime

log = logging.getLogger("utilityguard.detection")

HEAT_CAUSE_C = 38.0  # above this air temperature, overloads and cooling at capacity are one heat event
SETTLE_AFTER_RESTORE = 15  # ticks after a substation returns during which pump and load readings are still recovering
RESOLVE_AFTER = 6  # ticks without the condition before an alert resolves
CONFIRM_TICKS = 3  # ticks a condition must persist before it becomes an alert
REOPEN_TICKS = 150  # a fault that comes back within this many ticks (5 minutes) of its alert closing is the same incident, not a new one
COOLING_RELEASE_C = 0.2  # an open cooling alert closes only when the supply temperature is back within this margin of its limit (it opens at 0.6 above)
LEAK_RELEASE = 0.5  # an open leak alert closes only when the flow mismatch falls below this share of the creeping-leak threshold
SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
# when alerts of several kinds land in one incident, the one whose loss is largest owns it (an overload that ends in a trip becomes a trip)
KIND_PRIORITY = {"substation_trip": 3, "cooling_fault": 2, "pump_failure": 2, "leak": 2, "energy_waste": 1, "sensor_fault": 0}
CREEP_MISMATCH = 16.0  # m3/h of flow in minus flow out, steady over 20 ticks; sensor noise on that difference is about 1 m3/h
CREEP_DROP = 0.08  # bar the pipe pressure has fallen over the last 60 ticks, which tells a real leak from a drifting flow meter


@dataclass
class Detection:
    key: str  # stable dedup key: kind + root asset
    kind: str
    network: str
    zone_id: str
    asset_ids: list[str]
    severity: str
    title: str
    summary: str
    confidence: float
    facts: IncidentFacts
    root: str
    cause: str = ""  # shared external cause, e.g. "heat": alerts with the same cause belong to one incident even on different networks
    title_ar: str = ""  # the title and the summary in Arabic, written beside the English ones
    summary_ar: str = ""


@dataclass
class _Open:
    alert: Alert
    last_seen: int
    facts: IncidentFacts
    kind: str
    root: str


@dataclass
class DetectionState:
    open: dict[str, _Open] = field(default_factory=dict)
    incident_roots: dict[str, set[str]] = field(default_factory=dict)  # incident id -> assets in its blast radius
    incident_kind: dict[str, str] = field(default_factory=dict)
    incident_cause: dict[str, str] = field(default_factory=dict)
    alert_ids: itertools.count = field(default_factory=lambda: itertools.count(1))
    inc_ids: itertools.count = field(default_factory=lambda: itertools.count(1))
    stuck_since: dict[str, int] = field(default_factory=dict)
    pending: dict[str, int] = field(default_factory=dict)
    sub_was_on: dict[str, bool] = field(default_factory=dict)
    restored_at: dict[str, int] = field(default_factory=dict)  # substation -> tick it came back; readings take a moment to settle
    healthy_kv: dict[str, float] = field(default_factory=dict)  # substation -> its last voltage while it was healthy, so "fell from" stays true after the window slides past the trip
    closed: dict[str, tuple[str, int]] = field(default_factory=dict)  # alert key -> (incident id, tick) of the alert that closed last


def _mean(h: list[float], n: int) -> float:
    return float(np.nanmean(h[-n:])) if h else float("nan")


class Detector:
    def __init__(self, rt: "Runtime"):
        self.rt = rt
        self.g = rt.engine.g
        self.state = DetectionState()
        self.on_incident: list[Callable[[Incident, IncidentFacts, str], None]] = []
        self.on_incident_update: list[Callable[[Incident], None]] = []
        self._neighbours = self._build_neighbours()
        self._iforest, self._if_sensors, self._if_ranges = self._train_iforest()
        self.anomaly_scores: deque[float] = deque(maxlen=300)

    def reset(self) -> None:
        self.state = DetectionState()

    def clone(self, rt: "Runtime") -> "Detector":
        d = Detector.__new__(Detector)
        d.__dict__.update(self.__dict__)
        d.rt, d.g, d.state = rt, rt.engine.g, DetectionState()
        d.on_incident, d.on_incident_update = [], []
        d.anomaly_scores = deque(maxlen=300)
        return d

    def _voltage_text_ar(self, name: str, sub: str, now: float) -> str:
        now = max(0.0, float(now))
        before = self.state.healthy_kv.get(sub)
        if before is None:
            top = self.g.sensor_index[f"{sub}-voltage"].normal_range.max
            return f"جهد {asset_ar(name)} يقرأ {now:.1f} كيلوفولت مقابل مدى طبيعي حتى {top:.1f} كيلوفولت"
        return f"جهد {asset_ar(name)} هبط من {before:.1f} إلى {now:.1f} كيلوفولت"

    def _voltage_text(self, name: str, sub: str, now: float) -> str:
        """What the voltage did, in words that stay true: it fell from the last healthy reading (not from the highest of a sliding window, which turns into
        the tripped reading a minute after the trip) to what it reads now, and a reading below zero is sensor noise around zero, so it is shown as zero."""
        now = max(0.0, float(now))
        before = self.state.healthy_kv.get(sub)
        if before is None:
            top = self.g.sensor_index[f"{sub}-voltage"].normal_range.max
            return f"{name} voltage reads {now:.1f} kV against a normal range up to {top:.1f} kV"
        return f"{name} voltage fell from {before:.1f} kV to {now:.1f} kV"

    def _networks(self, asset_ids: list[str]) -> list[str]:
        return [self.g.asset_index[a].network for a in asset_ids if a in self.g.asset_index]

    # ---------- graph helpers ----------
    def _build_neighbours(self) -> dict[str, set[str]]:
        nb: dict[str, set[str]] = {}
        for l in self.g.topology.links:
            nb.setdefault(l.from_asset, set()).add(l.to_asset)
            nb.setdefault(l.to_asset, set()).add(l.from_asset)
        return nb

    def blast_radius(self, root: str, hops: int = 3) -> set[str]:
        """Assets downstream of the root via feeds/powers/supplies/cools links."""
        down: dict[str, set[str]] = {}
        for l in self.g.topology.links:
            down.setdefault(l.from_asset, set()).add(l.to_asset)
        seen, frontier = {root}, {root}
        for _ in range(hops):
            frontier = {n for a in frontier for n in down.get(a, ())} - seen
            seen |= frontier
        return seen

    # ---------- ML anomaly model ----------
    def _train_iforest(self):
        eng = self.rt.engine
        hist = eng.history(CALIBRATION_START, 14 * 24, step_minutes=30)
        sensors = [s for s in self.g.topology.sensors]
        ranges = np.array([[s.normal_range.min, s.normal_range.max] for s in sensors])
        X = np.array([[r[s.id] for s in sensors] for _, r in hist])
        Xn = (X - ranges[:, 0]) / np.maximum(ranges[:, 1] - ranges[:, 0], 1e-6)
        model = IsolationForest(n_estimators=150, contamination=0.01, random_state=self.rt.seed).fit(Xn)
        return model, sensors, ranges

    def anomaly_score(self) -> float:
        x = np.array([[self.rt.latest[s.id].value or 0.0 for s in self._if_sensors]])
        xn = (x - self._if_ranges[:, 0]) / np.maximum(self._if_ranges[:, 1] - self._if_ranges[:, 0], 1e-6)
        return float(-self._iforest.score_samples(xn)[0])  # higher = more anomalous

    # ---------- detectors ----------
    def detect(self) -> list[Detection]:
        rt, g, eng = self.rt, self.g, self.rt.engine
        H = rt.history
        out: list[Detection] = []
        zt = lambda z: g.zone_index[z].type  # noqa: E731
        name = lambda a: g.asset_index[a].name  # noqa: E731
        nar = lambda a: asset_ar(g.asset_index[a].name)  # noqa: E731
        for sub in SUBSTATION_BACKUP:
            on = eng.sub_on.get(sub, True)
            if on and not self.state.sub_was_on.get(sub, True):
                self.state.restored_at[sub] = eng.tick
            self.state.sub_was_on[sub] = on
        settling = lambda sub: eng.tick - self.state.restored_at.get(sub, -10**6) < SETTLE_AFTER_RESTORE  # noqa: E731

        # substation trip: voltage collapse
        for sub in SUBSTATION_BACKUP:
            v = H.get(f"{sub}-voltage", [])
            if v and v[-1] >= 15:
                self.state.healthy_kv[sub] = float(v[-1])
            if len(v) >= 2 and v[-1] < 15:
                zones = [z for z, s in ZONE_SUBSTATION.items() if s == sub]
                lost = sum(eng.zone_demand[z][1] for z in zones) * 0.45  # backup sheds ~45%
                pumps = [g.zone_pump[z] for z in zones]
                plants = [a.id for a in g.topology.assets if a.kind == "plant" and g.asset_index[a.id].zone_id in zones]
                backup = SUBSTATION_BACKUP[sub]
                reasons = [
                    Reason(text=self._voltage_text(name(sub), sub, v[-1]), text_ar=self._voltage_text_ar(name(sub), sub, v[-1]), evidence_type="measured", evidence_ref=f"{sub}-voltage"),
                    Reason(text=f"{len(pumps)} pumps powered by {name(sub)} stopped and their pressure is falling",
                           text_ar=f"توقفت {len(pumps)} مضخة تغذيها {nar(sub)} وضغطها يهبط", evidence_type="measured", evidence_ref=f"{pumps[0]}-pressure"),
                    Reason(text=f"{name(backup)} picked up priority load, now {_mean(H[f'{backup}-load'], 3):.1f} MW",
                           text_ar=f"{nar(backup)} تحمّلت الأحمال ذات الأولوية، وحملها الآن {_mean(H[f'{backup}-load'], 3):.1f} ميجاوات", evidence_type="measured", evidence_ref=f"{backup}-load"),
                    Reason(text=f"About {lost:.1f} MW of non-priority load is shed", text_ar=f"تم فصل حوالي {lost:.1f} ميجاوات من الأحمال غير ذات الأولوية", evidence_type="rule", evidence_ref="rule:backup-sheds-45pct"),
                ]
                if plants:
                    reasons.append(Reason(text=f"Cooling plant {name(plants[0])} lost power", text_ar=f"{nar(plants[0])} فقدت الكهرباء", evidence_type="measured", evidence_ref=f"{plants[0]}-supply-temp"))
                facts = IncidentFacts(kind="substation_trip", zone_types={z: zt(z) for z in zones}, unserved_mw=lost,
                                      pumps_stopped=len(pumps), plants_affected=len(plants),
                                      secondary_mw=0.08 * len(pumps) + 0.5 * len(plants), reasons=reasons, confidence=0.95)
                out.append(Detection(f"trip:{sub}", "substation_trip", "electricity", zones[0] if sub != "elec-sub-02" else "zone-gov",
                                     [sub, *pumps, *plants], "critical", f"{name(sub)} tripped",
                                     f"{name(sub)} is down. {len(pumps)} pumps and {len(plants)} cooling plant(s) lost power.", 0.95, facts, sub,
                                     title_ar=f"فصل {nar(sub)}", summary_ar=f"{nar(sub)} خارج الخدمة. فقدت الكهرباء {len(pumps)} مضخة و{len(plants)} محطة تبريد."))

        # leak: flow mismatch on a pipe + pressure drop
        for z, pipe in g.zone_pipe.items():
            fin, fout, p = H.get(f"{pipe}-flow-in", []), H.get(f"{pipe}-flow-out", []), H.get(f"{pipe}-pressure", [])
            if len(fin) < 6:
                continue
            mismatch = _mean(fin, 4) - _mean(fout, 4)
            p_now, p_before = _mean(p, 2), float(np.nanmax(p[-60:-5])) if len(p) > 8 else _mean(p, 2)
            if mismatch > 55 and p_now < p_before - 0.4:
                pump = g.zone_pump[z]
                nb_ok = all(rt.latest[f"{g.zone_pipe[d]}-pressure"].value >= g.sensor_index[f"{g.zone_pipe[d]}-pressure"].normal_range.min * 0.85
                            for d, up in g.upstream_zone.items() if up == g.upstream_zone[z] and d != z) if g.upstream_zone[z] else True
                pump_up = (_mean(H[f"{pump}-flow"], 3) / max(float(np.nanmean(H[f"{pump}-flow"][-60:-10] or [1])), 1) - 1) * 100 if len(H[f"{pump}-flow"]) > 12 else 0
                reasons = [
                    Reason(text=f"Pressure fell from {p_before:.1f} to {p_now:.1f} bar", text_ar=f"الضغط هبط من {p_before:.1f} إلى {p_now:.1f} بار", evidence_type="measured", evidence_ref=f"{pipe}-pressure"),
                    Reason(text=f"Inflow is {mismatch:.0f} m3/h higher than outflow", text_ar=f"التدفق الداخل أعلى من الخارج بمقدار {mismatch:.0f} م³/ساعة", evidence_type="measured", evidence_ref=f"{pipe}-flow-in"),
                    Reason(text="Neighbouring sensors read normal, so this is not a sensor fault" if nb_ok else "Neighbouring pressures are also low",
                           text_ar="الحساسات المجاورة تقرأ طبيعي، فالمشكلة ليست عطل حساس" if nb_ok else "الضغط منخفض في الحساسات المجاورة أيضًا",
                           evidence_type="rule", evidence_ref="rule:neighbour-consistency"),
                ]
                if pump_up > 3:
                    reasons.append(Reason(text=f"Pump {name(pump)} draw is up {pump_up:.0f}% as it compensates", text_ar=f"سحب {nar(pump)} زاد {pump_up:.0f}% وهي تعوّض", evidence_type="measured", evidence_ref=f"{pump}-flow"))
                sev = "critical" if mismatch > 120 or p_now < g.sensor_index[f"{pipe}-pressure"].normal_range.min * 0.75 else "warning"
                conf = round(min(0.97, 0.6 + mismatch / 500 + (0.1 if nb_ok else 0)), 2)
                facts = IncidentFacts(kind="leak", zone_types={z: zt(z)}, leak_m3h=round(mismatch, 0),
                                      secondary_mw=round(max(pump_up, 0) / 100 * _mean(H[f"{pump}-flow"], 3) * 0.0006, 3),
                                      reasons=reasons, confidence=conf)
                out.append(Detection(f"leak:{pipe}", "leak", "water", z, [pipe, pump], sev, "Pipe burst suspected",
                                     f"Pressure drop and a {mismatch:.0f} m3/h flow mismatch on {name(pipe)}.", conf, facts, pipe,
                                     title_ar="اشتباه في كسر ماسورة", summary_ar=f"هبوط في الضغط وفرق تدفق {mismatch:.0f} م³/ساعة على {nar(pipe)}."))

        # creeping leak: too small for the burst rule, but steady. Real water leaves the pipe and pressure sags; a drifting meter changes no pressure.
        burst_keys = {d.key for d in out}
        for z, pipe in g.zone_pipe.items():
            if f"leak:{pipe}" in burst_keys:
                continue
            fin, fout, p = H.get(f"{pipe}-flow-in", []), H.get(f"{pipe}-flow-out", []), H.get(f"{pipe}-pressure", [])
            if len(fin) < 80 or len(p) < 80:
                continue
            mismatch = _mean(fin, 20) - _mean(fout, 20)
            drop = float(np.nanmean(p[-80:-50])) - _mean(p, 10)
            open_already = f"leak:{pipe}" in self.state.open
            if mismatch >= CREEP_MISMATCH and (drop >= CREEP_DROP or open_already):
                pump = g.zone_pump[z]
                reasons = [
                    Reason(text=f"Inflow is {mismatch:.0f} m3/h higher than outflow, steady for 40 seconds", text_ar=f"التدفق الداخل أعلى من الخارج بمقدار {mismatch:.0f} م³/ساعة، وثابت لمدة 40 ثانية", evidence_type="measured", evidence_ref=f"{pipe}-flow-in"),
                    Reason(text=f"Pressure on the pipe fell {max(drop, 0):.2f} bar over the last two minutes", text_ar=f"ضغط الماسورة هبط {max(drop, 0):.2f} بار خلال آخر دقيقتين", evidence_type="measured", evidence_ref=f"{pipe}-pressure"),
                    Reason(text="Pressure fell as well, so this is water leaving the pipe and not a drifting flow meter", text_ar="الضغط هبط أيضًا، فالمياه تخرج من الماسورة وليس عداد تدفق منحرف", evidence_type="rule", evidence_ref="rule:pressure-confirms-flow"),
                ]
                conf = round(min(0.9, 0.55 + mismatch / 400), 2)
                facts = IncidentFacts(kind="leak", zone_types={z: zt(z)}, leak_m3h=round(mismatch, 0), reasons=reasons, confidence=conf)
                out.append(Detection(f"leak:{pipe}", "leak", "water", z, [pipe, pump], "warning", "Slow leak suspected",
                                     f"A steady {mismatch:.0f} m3/h flow mismatch with falling pressure on {name(pipe)}.", conf, facts, pipe,
                                     title_ar="اشتباه في تسريب بطيء", summary_ar=f"فرق تدفق ثابت {mismatch:.0f} م³/ساعة مع هبوط في الضغط على {nar(pipe)}."))

        # pump failure: pump powered but pressure/flow collapsing
        for z, pump in g.zone_pump.items():
            sub = ZONE_SUBSTATION[z]
            if not eng.sub_on.get(sub, True) or settling(sub):
                continue
            p = H.get(f"{pump}-pressure", [])
            rng = g.sensor_index[f"{pump}-pressure"].normal_range
            inlet = H.get(f"{g.zone_pipe[z]}-pressure", [])
            lift = _mean(p, 3) - _mean(inlet, 3) if inlet else 1.0  # a healthy pump adds ~0.5 bar over its inlet
            if len(p) > 5 and lift < 0.1 and _mean(p, 3) < rng.min:
                reasons = [
                    Reason(text=f"Pump {name(pump)} discharge pressure is {_mean(p, 3):.2f} bar, below the normal {rng.min:.2f} bar",
                           text_ar=f"ضغط تفريغ {nar(pump)} هو {_mean(p, 3):.2f} بار، أقل من الطبيعي {rng.min:.2f} بار", evidence_type="measured", evidence_ref=f"{pump}-pressure"),
                    Reason(text=f"Its substation {name(sub)} is energised, so this is not a power loss", text_ar=f"محطتها {nar(sub)} تعمل بالكهرباء، فالمشكلة ليست فقدان كهرباء", evidence_type="measured", evidence_ref=f"{sub}-voltage"),
                    Reason(text=f"The pump adds {lift:+.2f} bar over its inlet instead of about +0.5 bar", text_ar=f"المضخة تضيف {lift:+.2f} بار فوق مدخلها بدل حوالي +0.5 بار", evidence_type="measured", evidence_ref=f"{g.zone_pipe[z]}-pressure"),
                    Reason(text="Inflow and outflow on the supply pipe match, so this is not a leak", text_ar="التدفق الداخل والخارج على ماسورة التغذية متطابقان، فالمشكلة ليست تسريبًا", evidence_type="rule", evidence_ref="rule:no-flow-mismatch"),
                ]
                facts = IncidentFacts(kind="pump_failure", zone_types={z: zt(z)}, reasons=reasons, confidence=0.86)
                out.append(Detection(f"pump:{pump}", "pump_failure", "water", z, [pump], "critical", f"Pump {name(pump)} failing",
                                     f"Discharge pressure on {name(pump)} is collapsing while power is on.", 0.86, facts, pump,
                                     title_ar=f"عطل في {nar(pump)}", summary_ar=f"ضغط التفريغ على {nar(pump)} ينهار رغم وجود الكهرباء."))

        # cooling fault: supply temperature high while the plant has power
        for a in g.topology.assets:
            if a.kind != "plant":
                continue
            t = H.get(f"{a.id}-supply-temp", [])
            rng = g.sensor_index[f"{a.id}-supply-temp"].normal_range
            sub = ZONE_SUBSTATION[a.zone_id]
            if len(t) > 3 and _mean(t, 3) > rng.max + 0.6 and eng.sub_on.get(sub, True):
                served = COOLING_PLANTS[a.id][2]
                need = sum(eng.zone_demand[z][2] for z in served)
                delivered = _mean(H[f"{a.id}-load"], 3)
                deficit = max(0.0, need - delivered) * TONS_PER_MW
                reasons = [
                    Reason(text=f"Supply temperature is {_mean(t, 3):.1f} °C, above the {rng.max:.1f} °C limit", text_ar=f"درجة حرارة مياه التغذية {_mean(t, 3):.1f} °م، أعلى من الحد {rng.max:.1f} °م", evidence_type="measured", evidence_ref=f"{a.id}-supply-temp"),
                    Reason(text=f"Plant delivers {delivered:.1f} MW against {need:.1f} MW demand", text_ar=f"المحطة تسلّم {delivered:.1f} ميجاوات مقابل طلب {need:.1f} ميجاوات", evidence_type="measured", evidence_ref=f"{a.id}-load"),
                    Reason(text=f"Its substation {name(sub)} is energised, so the chiller itself is at fault", text_ar=f"محطتها {nar(sub)} تعمل بالكهرباء، فالعطل في المبرّد نفسه", evidence_type="rule", evidence_ref="rule:power-ok"),
                ]
                load_max = g.sensor_index[f"{a.id}-load"].normal_range.max
                at_limit = delivered >= 0.85 * load_max and need > delivered * 1.03
                if at_limit:  # delivering all it can while demand is higher: a capacity problem, not a broken chiller
                    reasons[2] = Reason(text=f"{name(sub)} is energised and the plant is running at its limit, so demand is above capacity rather than a chiller failing",
                                        text_ar=f"{nar(sub)} تعمل بالكهرباء والمحطة تعمل عند حدّها الأقصى، فالطلب أعلى من القدرة وليس عطلًا في المبرّد",
                                        evidence_type="rule", evidence_ref="rule:at-capacity")
                facts = IncidentFacts(kind="cooling_fault", zone_types={z: zt(z) for z in served[:2]}, cooling_deficit_tons=round(deficit),
                                      reasons=reasons, confidence=0.88)
                out.append(Detection(f"cool:{a.id}", "cooling_fault", "cooling", a.zone_id, [a.id, *[g.zone_loop[z] for z in served]],
                                     "critical" if deficit > 1500 else "warning", f"{a.name} cooling at capacity" if at_limit else f"{a.name} chiller fault",
                                     f"{a.name} supply water is too warm; zones get less cooling.", 0.88, facts, a.id,
                                     cause="heat" if at_limit and eng.ambient >= HEAT_CAUSE_C else "",
                                     title_ar=f"{asset_ar(a.name)} تعمل عند الحد الأقصى" if at_limit else f"عطل مبرّد في {asset_ar(a.name)}",
                                     summary_ar=f"مياه التغذية في {asset_ar(a.name)} أدفأ من اللازم؛ المناطق تحصل على تبريد أقل."))

        # overload / energy waste: substation load above its normal envelope (heat wave, holiday)
        for sub in SUBSTATION_BACKUP:
            ld = H.get(f"{sub}-load", [])
            rng = g.sensor_index[f"{sub}-load"].normal_range
            covering = any((not eng.sub_on.get(o, True) or settling(o)) and b == sub for o, b in SUBSTATION_BACKUP.items())
            if eng.sub_on.get(sub, True) and not covering and not settling(sub) and len(ld) > 3 and _mean(ld, 3) > rng.max:
                excess = _mean(ld, 3) - rng.max
                zones = [z for z, s in ZONE_SUBSTATION.items() if s == sub]
                reasons = [
                    Reason(text=f"{name(sub)} load is {_mean(ld, 3):.1f} MW, {excess:.1f} MW above its normal maximum", text_ar=f"حمل {nar(sub)} هو {_mean(ld, 3):.1f} ميجاوات، أعلى من حدّه الطبيعي بمقدار {excess:.1f} ميجاوات", evidence_type="measured", evidence_ref=f"{sub}-load"),
                    Reason(text=f"Air temperature is {eng.ambient:.0f} °C, which drives cooling and electrical load", text_ar=f"درجة حرارة الهواء {eng.ambient:.0f} °م، وهي ترفع حمل التبريد والكهرباء", evidence_type="measured", evidence_ref="weather:ambient"),
                ]
                facts = IncidentFacts(kind="energy_waste", zone_types={z: zt(z) for z in zones}, excess_mw=round(excess, 2), reasons=reasons, confidence=0.8)
                out.append(Detection(f"overload:{sub}", "energy_waste", "electricity", zones[0], [sub], "warning", f"{name(sub)} overloaded",
                                     f"{name(sub)} is running {excess:.1f} MW above its normal envelope.", 0.8, facts, sub,
                                     cause="heat" if eng.ambient >= HEAT_CAUSE_C else "",
                                     title_ar=f"حمل زائد على {nar(sub)}", summary_ar=f"{nar(sub)} تعمل أعلى من نطاقها الطبيعي بمقدار {excess:.1f} ميجاوات."))

        # stuck sensor: flat value while its neighbours on the same network keep moving
        st = self.state.stuck_since
        for s in g.topology.sensors:
            h = H.get(s.id, [])
            if len(h) < 12:
                continue
            flat = float(np.nanstd(h[-10:])) < 1e-9
            if flat:
                st.setdefault(s.id, eng.tick)
            else:
                st.pop(s.id, None)
            if flat and eng.tick - st[s.id] >= 0:
                peers = [x for x in g.topology.sensors if x.asset_id in self._neighbours.get(s.asset_id, set()) and x.network == s.network]
                moving = sum(float(np.nanstd(H[x.id][-10:])) > 1e-6 for x in peers)
                a = g.asset_index[s.asset_id]
                reasons = [
                    Reason(text=f"{s.id} has reported exactly {h[-1]:.2f} {s.unit} for {min(len(h), 10) * 2}+ seconds",
                           text_ar=f"{sensor_ar(s.id, s.asset_id, a.name)} يقرأ بالضبط {h[-1]:.2f} {unit_ar(s.unit)} منذ {min(len(h), 10) * 2}+ ثانية", evidence_type="measured", evidence_ref=s.id),
                    Reason(text=f"{moving} of {len(peers)} neighbouring sensors are still changing normally", text_ar=f"{moving} من {len(peers)} حساسات مجاورة لا تزال تتغير بشكل طبيعي", evidence_type="measured", evidence_ref=peers[0].id if peers else None),
                    Reason(text="Real pressure always shows small noise, so a flat line means the sensor is stuck", text_ar="الضغط الحقيقي تظهر عليه دائمًا ضوضاء صغيرة، فالخط المستقيم يعني أن الحساس عالق", evidence_type="rule", evidence_ref="rule:zero-variance"),
                ]
                facts = IncidentFacts(kind="sensor_fault", zone_types={a.zone_id: zt(a.zone_id)}, reasons=reasons, confidence=0.9)
                out.append(Detection(f"stuck:{s.id}", "sensor_fault", s.network, a.zone_id, [s.asset_id], "warning", f"Sensor {s.id} stuck",
                                     f"{s.id} is frozen while its neighbours move. Readings are marked suspect.", 0.9, facts, s.asset_id,
                                     title_ar=f"حساس عالق: {sensor_ar(s.id, s.asset_id, a.name)}", summary_ar=f"{sensor_ar(s.id, s.asset_id, a.name)} متجمد بينما جيرانه يتحركون. تم وسم قراءاته كمشكوك فيها."))
                rt.latest[s.id] = rt.latest[s.id].model_copy(update={"quality": "suspect"})
        return out

    # ---------- when an open alert may close ----------
    def _holds(self, o: "_Open") -> bool:
        """An alert opens on a firm condition and closes only when the evidence is clearly gone. Closing on the opening threshold lets a fault
        that sits near it open and close the alert over and over (a weak chiller fault opened a dozen incidents), and lets a fault whose
        symptom is masked by another one (a pump with no power moves no water, so a leak on its pipe shows no mismatch) look repaired."""
        rt, g, eng, H = self.rt, self.g, self.rt.engine, self.rt.history
        settling = lambda sub: eng.tick - self.state.restored_at.get(sub, -10**6) < SETTLE_AFTER_RESTORE  # noqa: E731
        if o.kind == "leak":
            pipe = o.root
            zone = g.asset_index[pipe].zone_id
            sub = ZONE_SUBSTATION[zone]
            if not eng.sub_on.get(sub, True) or settling(sub):
                return True  # the pump that would show the leak has no power: it cannot be seen, so it is not cleared
            fin, fout = H.get(f"{pipe}-flow-in", []), H.get(f"{pipe}-flow-out", [])
            return len(fin) >= 20 and _mean(fin, 20) - _mean(fout, 20) >= LEAK_RELEASE * CREEP_MISMATCH
        if o.kind == "cooling_fault":
            t = H.get(f"{o.root}-supply-temp", [])
            rng = g.sensor_index[f"{o.root}-supply-temp"].normal_range
            return len(t) > 3 and _mean(t, 3) > rng.max + COOLING_RELEASE_C and eng.sub_on.get(ZONE_SUBSTATION[g.asset_index[o.root].zone_id], True)
        if o.kind == "pump_failure":
            p = H.get(f"{o.root}-pressure", [])
            rng = g.sensor_index[f"{o.root}-pressure"].normal_range
            sub = ZONE_SUBSTATION[g.asset_index[o.root].zone_id]
            return len(p) > 5 and _mean(p, 3) < rng.min * 1.03 and eng.sub_on.get(sub, True) and not settling(sub)
        if o.kind == "energy_waste":
            ld = H.get(f"{o.root}-load", [])
            rng = g.sensor_index[f"{o.root}-load"].normal_range
            # the opening rule leaves out a substation that is covering for a tripped one, and one that has just come back: the trip explains that load
            covering = any((not eng.sub_on.get(other, True) or settling(other)) and backup == o.root for other, backup in SUBSTATION_BACKUP.items())
            return len(ld) > 3 and _mean(ld, 3) > rng.max * 0.98 and eng.sub_on.get(o.root, True) and not covering and not settling(o.root)
        return False

    # ---------- grouping ----------
    async def tick(self, rt: "Runtime") -> None:
        self.anomaly_scores.append(self.anomaly_score())
        dets = self.detect()
        tick, now = rt.engine.tick, rt.now
        seen_keys = set()
        # root causes first so dependants attach to them
        dets.sort(key=lambda d: (d.kind != "substation_trip", -SEVERITY_RANK[d.severity]))
        live = {d.key for d in dets}
        self.state.pending = {k: self.state.pending.get(k, 0) + 1 for k in live}
        for d in dets:
            seen_keys.add(d.key)
            o = self.state.open.get(d.key)
            if not o and self.state.pending[d.key] < CONFIRM_TICKS:
                continue
            if o:
                o.last_seen, o.facts = tick, d.facts
                if o.alert.severity != d.severity and SEVERITY_RANK[d.severity] > SEVERITY_RANK[o.alert.severity]:
                    o.alert = o.alert.model_copy(update={"severity": d.severity, "summary": d.summary, "summary_ar": d.summary_ar or None, "confidence": d.confidence})
                    rt.alerts[o.alert.id] = o.alert
                    await rt.broadcast("alert", o.alert)
                continue
            inc_id = next((i for i, radius in self.state.incident_roots.items()
                           if rt.incidents[i].state != "resolved" and (d.root in radius or set(d.asset_ids) & radius)), None)
            if inc_id is None and d.cause:
                inc_id = next((i for i, c in self.state.incident_cause.items() if c == d.cause and rt.incidents[i].state != "resolved"), None)
            back = self.state.closed.get(d.key)
            if inc_id is None and back and tick - back[1] <= REOPEN_TICKS and back[0] in rt.incidents and back[0] in self.state.incident_roots:
                inc_id = back[0]  # the same fault coming back: one incident, not a second one beside the first
                if rt.incidents[inc_id].state == "resolved":
                    rt.incidents[inc_id] = rt.incidents[inc_id].model_copy(update={"state": "open"})
                    log.info("incident %s reopened by %s", inc_id, d.key)
            alert = Alert(id=f"alert-{next(self.state.alert_ids):04d}", incident_id=inc_id or f"inc-{next(self.state.inc_ids):04d}",
                          network=d.network, zone_id=d.zone_id, asset_ids=d.asset_ids, severity=d.severity, title=d.title,
                          summary=d.summary, detected_at=now, confidence=d.confidence, title_ar=d.title_ar or None, summary_ar=d.summary_ar or None)
            self.state.open[d.key] = _Open(alert, tick, d.facts, d.kind, d.root)
            rt.alerts[alert.id] = alert
            if inc_id:
                inc = rt.incidents[inc_id]
                nets = list(dict.fromkeys([*inc.networks, d.network, *self._networks(d.asset_ids)]))
                inc = inc.model_copy(update={"alert_ids": [*inc.alert_ids, alert.id], "networks": nets,
                                             "zone_ids": list(dict.fromkeys([*inc.zone_ids, d.zone_id]))})
                if SEVERITY_RANK[d.severity] > SEVERITY_RANK[inc.severity]:
                    inc = inc.model_copy(update={"severity": d.severity})
                escalates = KIND_PRIORITY[d.kind] > KIND_PRIORITY.get(self.state.incident_kind.get(inc_id, ""), -1)
                if escalates:  # the incident grew into something bigger: it now belongs to the larger root cause
                    inc = inc.model_copy(update={"title": f"{d.title}, {rt.engine.g.zone_index[d.zone_id].name}", "reanalysing": True,
                                                 "title_ar": f"{d.title_ar}، {ZONE_AR.get(d.zone_id, d.zone_id)}" if d.title_ar else None})
                    self.state.incident_kind[inc_id] = d.kind
                    log.info("incident %s escalated to %s root=%s", inc_id, d.kind, d.root)
                rt.incidents[inc_id] = inc
                self.state.incident_roots[inc_id] |= self.blast_radius(d.root) | set(d.asset_ids)  # a growing incident widens its reach
                await rt.broadcast("alert", alert)
                await rt.broadcast("incident", inc)
                for cb in self.on_incident_update:
                    cb(inc)
                if escalates:  # and it gets its own analysis
                    for cb in self.on_incident:
                        cb(inc, d.facts, d.root)
            else:
                radius = self.blast_radius(d.root) | set(d.asset_ids)
                self.state.incident_roots[alert.incident_id] = radius
                self.state.incident_kind[alert.incident_id] = d.kind
                if d.cause:
                    self.state.incident_cause[alert.incident_id] = d.cause
                inc = Incident(id=alert.incident_id, title=f"{d.title}, {rt.engine.g.zone_index[d.zone_id].name}",
                               title_ar=f"{d.title_ar}، {ZONE_AR.get(d.zone_id, d.zone_id)}" if d.title_ar else None,
                               networks=list(dict.fromkeys([d.network, *self._networks(d.asset_ids)])), zone_ids=list(d.facts.zone_types) or [d.zone_id], severity=d.severity,
                               state="open", opened_at=now, alert_ids=[alert.id])
                rt.incidents[inc.id] = inc
                await rt.broadcast("alert", alert)
                await rt.broadcast("incident", inc)
                log.info("new incident %s (%s) root=%s", inc.id, d.kind, d.root)
                for cb in self.on_incident:
                    cb(inc, d.facts, d.root)

        # resolution
        for key, o in list(self.state.open.items()):
            if key not in seen_keys and self._holds(o):
                o.last_seen = tick  # the evidence is still there, only the opening condition has lapsed
                continue
            if key not in seen_keys and tick - o.last_seen >= RESOLVE_AFTER:
                del self.state.open[key]
                self.state.closed[key] = (o.alert.incident_id, tick)
                a = o.alert.model_copy(update={"state": "resolved"})
                rt.alerts[a.id] = a
                await rt.broadcast("alert", a)
                inc = rt.incidents.get(a.incident_id)
                if inc and all(rt.alerts[x].state == "resolved" for x in inc.alert_ids):
                    rt.incidents[inc.id] = inc.model_copy(update={"state": "resolved"})
                    await rt.broadcast("incident", rt.incidents[inc.id])

    def facts_for(self, incident_id: str) -> tuple[IncidentFacts, str, str] | None:
        """Latest facts of the root alert of an incident (for recomputation)."""
        for o in self.state.open.values():
            if o.alert.incident_id == incident_id and o.kind == self.state.incident_kind.get(incident_id):
                return o.facts, o.root, o.kind
        return None

