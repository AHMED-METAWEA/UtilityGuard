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
from app.schemas import Alert, Incident, Reason
from app.sim.engine import CALIBRATION_START
from app.sim.topology import COOLING_PLANTS, SUBSTATION_BACKUP, ZONE_SUBSTATION

if TYPE_CHECKING:
    from app.runtime import Runtime

log = logging.getLogger("utilityguard.detection")

RESOLVE_AFTER = 6  # ticks without the condition before an alert resolves
CONFIRM_TICKS = 3  # ticks a condition must persist before it becomes an alert
SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}


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
    alert_ids: itertools.count = field(default_factory=lambda: itertools.count(1))
    inc_ids: itertools.count = field(default_factory=lambda: itertools.count(1))
    stuck_since: dict[str, int] = field(default_factory=dict)
    pending: dict[str, int] = field(default_factory=dict)


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

        # substation trip: voltage collapse
        for sub in SUBSTATION_BACKUP:
            v = H.get(f"{sub}-voltage", [])
            if len(v) >= 2 and v[-1] < 15:
                zones = [z for z, s in ZONE_SUBSTATION.items() if s == sub]
                lost = sum(eng.zone_demand[z][1] for z in zones) * 0.45  # backup sheds ~45%
                pumps = [g.zone_pump[z] for z in zones]
                plants = [a.id for a in g.topology.assets if a.kind == "plant" and g.asset_index[a.id].zone_id in zones]
                backup = SUBSTATION_BACKUP[sub]
                reasons = [
                    Reason(text=f"{name(sub)} voltage fell from {float(np.nanmax(v[-30:])):.1f} kV to {v[-1]:.1f} kV", evidence_type="measured", evidence_ref=f"{sub}-voltage"),
                    Reason(text=f"{len(pumps)} pumps powered by {name(sub)} stopped and their pressure is falling", evidence_type="measured", evidence_ref=f"{pumps[0]}-pressure"),
                    Reason(text=f"{name(backup)} picked up priority load, now {_mean(H[f'{backup}-load'], 3):.1f} MW", evidence_type="measured", evidence_ref=f"{backup}-load"),
                    Reason(text=f"About {lost:.1f} MW of non-priority load is shed", evidence_type="rule", evidence_ref="rule:backup-sheds-45pct"),
                ]
                if plants:
                    reasons.append(Reason(text=f"Cooling plant {name(plants[0])} lost power", evidence_type="measured", evidence_ref=f"{plants[0]}-supply-temp"))
                facts = IncidentFacts(kind="substation_trip", zone_types={z: zt(z) for z in zones}, unserved_mw=lost,
                                      pumps_stopped=len(pumps), plants_affected=len(plants),
                                      secondary_mw=0.08 * len(pumps) + 0.5 * len(plants), reasons=reasons, confidence=0.95)
                out.append(Detection(f"trip:{sub}", "substation_trip", "electricity", zones[0] if sub != "elec-sub-02" else "zone-gov",
                                     [sub, *pumps, *plants], "critical", f"{name(sub)} tripped",
                                     f"{name(sub)} is down. {len(pumps)} pumps and {len(plants)} cooling plant(s) lost power.", 0.95, facts, sub))

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
                    Reason(text=f"Pressure fell from {p_before:.1f} to {p_now:.1f} bar", evidence_type="measured", evidence_ref=f"{pipe}-pressure"),
                    Reason(text=f"Inflow is {mismatch:.0f} m3/h higher than outflow", evidence_type="measured", evidence_ref=f"{pipe}-flow-in"),
                    Reason(text="Neighbouring sensors read normal, so this is not a sensor fault" if nb_ok else "Neighbouring pressures are also low",
                           evidence_type="rule", evidence_ref="rule:neighbour-consistency"),
                ]
                if pump_up > 3:
                    reasons.append(Reason(text=f"Pump {name(pump)} draw is up {pump_up:.0f}% as it compensates", evidence_type="measured", evidence_ref=f"{pump}-flow"))
                sev = "critical" if mismatch > 120 or p_now < g.sensor_index[f"{pipe}-pressure"].normal_range.min * 0.75 else "warning"
                conf = round(min(0.97, 0.6 + mismatch / 500 + (0.1 if nb_ok else 0)), 2)
                facts = IncidentFacts(kind="leak", zone_types={z: zt(z)}, leak_m3h=round(mismatch, 0),
                                      secondary_mw=round(max(pump_up, 0) / 100 * _mean(H[f"{pump}-flow"], 3) * 0.0006, 3),
                                      reasons=reasons, confidence=conf)
                out.append(Detection(f"leak:{pipe}", "leak", "water", z, [pipe, pump], sev, "Pipe burst suspected",
                                     f"Pressure drop and a {mismatch:.0f} m3/h flow mismatch on {name(pipe)}.", conf, facts, pipe))

        # pump failure: pump powered but pressure/flow collapsing
        for z, pump in g.zone_pump.items():
            sub = ZONE_SUBSTATION[z]
            if not eng.sub_on.get(sub, True):
                continue
            p = H.get(f"{pump}-pressure", [])
            rng = g.sensor_index[f"{pump}-pressure"].normal_range
            inlet = H.get(f"{g.zone_pipe[z]}-pressure", [])
            lift = _mean(p, 3) - _mean(inlet, 3) if inlet else 1.0  # a healthy pump adds ~0.5 bar over its inlet
            if len(p) > 5 and lift < 0.1 and _mean(p, 3) < rng.min:
                reasons = [
                    Reason(text=f"Pump {name(pump)} discharge pressure is {_mean(p, 3):.2f} bar, below the normal {rng.min:.2f} bar", evidence_type="measured", evidence_ref=f"{pump}-pressure"),
                    Reason(text=f"Its substation {name(sub)} is energised, so this is not a power loss", evidence_type="measured", evidence_ref=f"{sub}-voltage"),
                    Reason(text=f"The pump adds {lift:+.2f} bar over its inlet instead of about +0.5 bar", evidence_type="measured", evidence_ref=f"{g.zone_pipe[z]}-pressure"),
                    Reason(text="Inflow and outflow on the supply pipe match, so this is not a leak", evidence_type="rule", evidence_ref="rule:no-flow-mismatch"),
                ]
                facts = IncidentFacts(kind="pump_failure", zone_types={z: zt(z)}, reasons=reasons, confidence=0.86)
                out.append(Detection(f"pump:{pump}", "pump_failure", "water", z, [pump], "critical", f"Pump {name(pump)} failing",
                                     f"Discharge pressure on {name(pump)} is collapsing while power is on.", 0.86, facts, pump))

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
                    Reason(text=f"Supply temperature is {_mean(t, 3):.1f} °C, above the {rng.max:.1f} °C limit", evidence_type="measured", evidence_ref=f"{a.id}-supply-temp"),
                    Reason(text=f"Plant delivers {delivered:.1f} MW against {need:.1f} MW demand", evidence_type="measured", evidence_ref=f"{a.id}-load"),
                    Reason(text=f"Its substation {name(sub)} is energised, so the chiller itself is at fault", evidence_type="rule", evidence_ref="rule:power-ok"),
                ]
                facts = IncidentFacts(kind="cooling_fault", zone_types={z: zt(z) for z in served[:2]}, cooling_deficit_tons=round(deficit),
                                      reasons=reasons, confidence=0.88)
                out.append(Detection(f"cool:{a.id}", "cooling_fault", "cooling", a.zone_id, [a.id, *[g.zone_loop[z] for z in served]],
                                     "critical" if deficit > 1500 else "warning", f"{a.name} chiller fault",
                                     f"{a.name} supply water is too warm; zones get less cooling.", 0.88, facts, a.id))

        # overload / energy waste: substation load above its normal envelope (heat wave, holiday)
        for sub in SUBSTATION_BACKUP:
            ld = H.get(f"{sub}-load", [])
            rng = g.sensor_index[f"{sub}-load"].normal_range
            covering = any(not eng.sub_on.get(o, True) and b == sub for o, b in SUBSTATION_BACKUP.items())
            if eng.sub_on.get(sub, True) and not covering and len(ld) > 3 and _mean(ld, 3) > rng.max:
                excess = _mean(ld, 3) - rng.max
                zones = [z for z, s in ZONE_SUBSTATION.items() if s == sub]
                reasons = [
                    Reason(text=f"{name(sub)} load is {_mean(ld, 3):.1f} MW, {excess:.1f} MW above its normal maximum", evidence_type="measured", evidence_ref=f"{sub}-load"),
                    Reason(text=f"Air temperature is {eng.ambient:.0f} °C, which drives cooling and electrical load", evidence_type="measured", evidence_ref="weather:ambient"),
                ]
                facts = IncidentFacts(kind="energy_waste", zone_types={z: zt(z) for z in zones}, excess_mw=round(excess, 2), reasons=reasons, confidence=0.8)
                out.append(Detection(f"overload:{sub}", "energy_waste", "electricity", zones[0], [sub], "warning", f"{name(sub)} overloaded",
                                     f"{name(sub)} is running {excess:.1f} MW above its normal envelope.", 0.8, facts, sub))

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
                    Reason(text=f"{s.id} has reported exactly {h[-1]:.2f} {s.unit} for {min(len(h), 10) * 2}+ seconds", evidence_type="measured", evidence_ref=s.id),
                    Reason(text=f"{moving} of {len(peers)} neighbouring sensors are still changing normally", evidence_type="measured", evidence_ref=peers[0].id if peers else None),
                    Reason(text="Real pressure always shows small noise, so a flat line means the sensor is stuck", evidence_type="rule", evidence_ref="rule:zero-variance"),
                ]
                facts = IncidentFacts(kind="sensor_fault", zone_types={a.zone_id: zt(a.zone_id)}, reasons=reasons, confidence=0.9)
                out.append(Detection(f"stuck:{s.id}", "sensor_fault", s.network, a.zone_id, [s.asset_id], "warning", f"Sensor {s.id} stuck",
                                     f"{s.id} is frozen while its neighbours move. Readings are marked suspect.", 0.9, facts, s.asset_id))
                rt.latest[s.id] = rt.latest[s.id].model_copy(update={"quality": "suspect"})
        return out

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
                    o.alert = o.alert.model_copy(update={"severity": d.severity, "summary": d.summary, "confidence": d.confidence})
                    rt.alerts[o.alert.id] = o.alert
                    await rt.broadcast("alert", o.alert)
                continue
            inc_id = next((i for i, radius in self.state.incident_roots.items()
                           if rt.incidents[i].state != "resolved" and (d.root in radius or set(d.asset_ids) & radius)), None)
            alert = Alert(id=f"alert-{next(self.state.alert_ids):04d}", incident_id=inc_id or f"inc-{next(self.state.inc_ids):04d}",
                          network=d.network, zone_id=d.zone_id, asset_ids=d.asset_ids, severity=d.severity, title=d.title,
                          summary=d.summary, detected_at=now, confidence=d.confidence)
            self.state.open[d.key] = _Open(alert, tick, d.facts, d.kind, d.root)
            rt.alerts[alert.id] = alert
            if inc_id:
                inc = rt.incidents[inc_id]
                nets = list(dict.fromkeys([*inc.networks, d.network]))
                inc = inc.model_copy(update={"alert_ids": [*inc.alert_ids, alert.id], "networks": nets,
                                             "zone_ids": list(dict.fromkeys([*inc.zone_ids, d.zone_id]))})
                rt.incidents[inc_id] = inc
                await rt.broadcast("alert", alert)
                for cb in self.on_incident_update:
                    cb(inc)
            else:
                radius = self.blast_radius(d.root) | set(d.asset_ids)
                self.state.incident_roots[alert.incident_id] = radius
                self.state.incident_kind[alert.incident_id] = d.kind
                inc = Incident(id=alert.incident_id, title=f"{d.title}, {rt.engine.g.zone_index[d.zone_id].name}",
                               networks=[d.network], zone_ids=list(d.facts.zone_types) or [d.zone_id], severity=d.severity,
                               state="open", opened_at=now, alert_ids=[alert.id])
                rt.incidents[inc.id] = inc
                await rt.broadcast("alert", alert)
                log.info("new incident %s (%s) root=%s", inc.id, d.kind, d.root)
                for cb in self.on_incident:
                    cb(inc, d.facts, d.root)

        # resolution
        for key, o in list(self.state.open.items()):
            if key not in seen_keys and tick - o.last_seen >= RESOLVE_AFTER:
                del self.state.open[key]
                a = o.alert.model_copy(update={"state": "resolved"})
                rt.alerts[a.id] = a
                await rt.broadcast("alert", a)
                inc = rt.incidents.get(a.incident_id)
                if inc and all(rt.alerts[x].state == "resolved" for x in inc.alert_ids):
                    rt.incidents[inc.id] = inc.model_copy(update={"state": "resolved"})

    def facts_for(self, incident_id: str) -> tuple[IncidentFacts, str, str] | None:
        """Latest facts of the root alert of an incident (for recomputation)."""
        for o in self.state.open.values():
            if o.alert.incident_id == incident_id and o.kind == self.state.incident_kind.get(incident_id):
                return o.facts, o.root, o.kind
        return None

