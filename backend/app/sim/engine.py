"""Deterministic, seeded simulation of the water, electricity and cooling networks.

`step(ts)` advances one tick and returns the true physical values plus the sensor
readings (which may differ from truth when a sensor is stuck or drifting).
Physics is simplified but causal: demand -> flows -> pressures, power -> pumps and
chillers, heat -> cooling load -> electrical load.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np

from app.sim.topology import COOLING_PLANTS, SPINE, TRUNK_ORDER, ZONE_BASE, ZONE_SUBSTATION, SUBSTATION_BACKUP, build

CAIRO = timezone(timedelta(hours=3))
TICK_SECONDS = 2.0


def demand_profile(zone_type: str, hour: float, weekday: int, holiday: bool = False) -> float:
    """Shape of demand over a day, ~1.0 average. Friday/Saturday is the Egyptian weekend."""
    weekend = weekday in (4, 5) or holiday
    g = lambda mu, sd: math.exp(-((hour - mu) ** 2) / (2 * sd * sd))  # noqa: E731
    if zone_type in ("government", "business"):
        work = 0.0 if weekend else 1.0
        return 0.45 + work * (0.95 * g(11.5, 3.0)) + (0.15 if zone_type == "business" else 0.05) * g(19, 2.5)
    if zone_type == "residential":
        boost = 1.15 if weekend else 1.0
        return boost * (0.45 + 0.6 * g(7.5, 1.6) + 0.8 * g(20.5, 2.2) + 0.15 * g(14, 3))
    return 0.75 + 0.35 * g(12, 4)  # services (hospitals) are flat-ish


def ambient_temp(hour: float, day_of_year: int) -> float:
    seasonal = 27 + 8 * math.sin((day_of_year - 105) / 365 * 2 * math.pi)
    return seasonal + 6 * math.sin((hour - 9) / 24 * 2 * math.pi)


@dataclass
class Event:
    kind: str  # pipe_burst, stuck_sensor, substation_trip, pump_failure, cooling_fault, heat_wave, holiday
    target: str | None
    severity: float = 1.0
    started_tick: int = 0
    run_id: str = ""
    params: dict = field(default_factory=dict)

    def ramp(self, tick: int, ticks: int = 20) -> float:
        return min(1.0, (tick - self.started_tick + 1) / ticks)


class Engine:
    def __init__(self, seed: int = 42):
        self.seed = seed
        self.g = build()
        self.reset()
        calibrate(self)

    def reset(self) -> None:
        self.rng = np.random.default_rng(self.seed)
        self.tick = 0
        self.events: list[Event] = []
        self.state: dict[str, float] = {}  # smoothed true values per sensor
        self.stuck: dict[str, float] = {}
        self.truth: dict[str, float] = {}
        self.leak_m3h: dict[str, float] = {}
        self.pump_on: dict[str, bool] = {}
        self.sub_on: dict[str, bool] = {}
        self.zone_demand: dict[str, tuple[float, float, float]] = {}
        self.ambient = 30.0

    # ---------- events ----------
    def add_event(self, ev: Event) -> None:
        ev.started_tick = self.tick
        self.events.append(ev)

    def clear_events(self, run_id: str | None = None) -> None:
        self.events = [e for e in self.events if run_id is not None and e.run_id != run_id]
        live_stuck = {e.target for e in self.events if e.kind == "stuck_sensor"}
        self.stuck = {k: v for k, v in self.stuck.items() if k in live_stuck}

    def _active(self, kind: str) -> list[Event]:
        return [e for e in self.events if e.kind == kind]

    # ---------- physics ----------
    def _targets(self, ts: datetime) -> dict[str, float]:
        g = self.g
        local = ts.astimezone(CAIRO)
        hour = local.hour + local.minute / 60 + local.second / 3600
        heat = max([e.severity for e in self._active("heat_wave")], default=0.0)
        holiday = self._active("holiday")
        day_rng = np.random.default_rng([self.seed, local.toordinal()])
        day_temp, day_demand = day_rng.normal(0, 2.2), day_rng.normal(1.0, 0.035)
        self.ambient = ambient_temp(hour, local.timetuple().tm_yday) + day_temp
        if heat:
            self.ambient = max(self.ambient, heat - 4 + 4 * math.sin((hour - 9) / 24 * 2 * math.pi))
        temp_f = 1 + 0.012 * (self.ambient - 30)
        hol_factor = holiday[0].severity if holiday else 1.0

        tripped = {e.target for e in self._active("substation_trip")}
        self.sub_on = {s: s not in tripped for s in SUBSTATION_BACKUP}
        failed_pumps = {e.target: e for e in self._active("pump_failure")}

        t: dict[str, float] = {}
        # zone demand
        for z in g.zone_index.values():
            w, e, c = ZONE_BASE[z.id]
            p = demand_profile(z.type, hour, local.weekday(), bool(holiday))
            if holiday and z.type == "residential":
                p *= hol_factor
            p *= day_demand
            self.zone_demand[z.id] = (w * p * (0.6 + 0.4 * temp_f),
                                      e * (0.55 + 0.45 * p) * (0.8 + 0.2 * temp_f),
                                      c * (0.4 + 0.6 * p) * temp_f ** 3)

        # ---------- water ----------
        self.leak_m3h = {}
        for ev in self._active("pipe_burst"):
            self.leak_m3h[ev.target] = 300 * ev.severity * ev.ramp(self.tick, 8)

        for pump in g.zone_pump.values():
            zone = g.asset_index[pump].zone_id
            on = self.sub_on[ZONE_SUBSTATION[zone]]
            if pump in failed_pumps:
                on = on and failed_pumps[pump].ramp(self.tick, 30) < 1.0
            self.pump_on[pump] = on

        downstream = {z: [] for z in g.zone_pipe}
        for z, up in g.upstream_zone.items():
            if up:
                downstream[up].append(z)

        def total_flow(z):
            return self.zone_demand[z][0] + self.leak_m3h.get(g.zone_pipe[z], 0) + sum(total_flow(d) for d in downstream[z])

        res_pressure = 4.7
        zone_pressure: dict[str, float] = {}
        order = TRUNK_ORDER + [z for z in SPINE if z not in TRUNK_ORDER]
        # resolve in dependency order
        resolved: list[str] = []
        while len(resolved) < len(order):
            for z in order:
                if z in resolved:
                    continue
                up = g.upstream_zone[z]
                if up and up not in resolved:
                    continue
                inlet = res_pressure if up is None else zone_pressure[up]
                pipe, pump = g.zone_pipe[z], g.zone_pump[z]
                q_in = total_flow(z)
                leak = self.leak_m3h.get(pipe, 0)
                pipe_p = inlet - 0.35 - 0.00004 * q_in - 0.0095 * leak
                boost = 0.5 if self.pump_on[pump] else -1.5
                if pump in failed_pumps and self.pump_on[pump]:
                    boost -= 0.6 * failed_pumps[pump].ramp(self.tick, 30)
                pump_p = max(0.6, min(inlet, pipe_p) + boost)
                zone_pressure[z] = min(pump_p, 4.6) if up is not None else pump_p
                pump_flow = (self.zone_demand[z][0] + sum(total_flow(d) for d in downstream[z])) if self.pump_on[pump] else 0.15 * self.zone_demand[z][0]
                if leak:
                    pump_flow *= 1 + 0.12 * min(1, leak / 140)
                t[f"{pipe}-flow-in"] = q_in
                t[f"{pipe}-flow-out"] = q_in - leak
                t[f"{pipe}-pressure"] = max(0.4, pipe_p)
                t[f"{pump}-pressure"] = pump_p
                t[f"{pump}-flow"] = pump_flow
                resolved.append(z)
        total_w = sum(t[f"{g.zone_pipe[z]}-flow-in"] for z in TRUNK_ORDER[:1])
        t["water-res-01-flow"] = total_w
        t["water-res-01-pressure"] = res_pressure - 0.00006 * total_w

        # ---------- cooling ----------
        plant_elec: dict[str, float] = {}
        for pid, (_, home, served) in COOLING_PLANTS.items():
            on = self.sub_on[ZONE_SUBSTATION[home]]
            fault = next((e for e in self._active("cooling_fault") if e.target == pid), None)
            capacity = 1.0 if on else 0.0
            if fault:
                capacity *= 1 - 0.7 * fault.severity * fault.ramp(self.tick, 25)
            load = sum(self.zone_demand[z][2] for z in served)
            delivered = load * capacity
            supply = 6.0 + (1 - capacity) * 7.5 + max(0, self.ambient - 40) * 0.05
            t[f"{pid}-supply-temp"] = supply
            t[f"{pid}-flow"] = 120 * delivered if on else 0.0
            t[f"{pid}-load"] = delivered
            plant_elec[pid] = delivered * 0.28  # chiller electrical draw, MW_e per MW_th (COP ~3.6)
            for z in served:
                lid = g.zone_loop[z]
                share = self.zone_demand[z][2] / max(load, 1e-6)
                t[f"{lid}-flow"] = 120 * delivered * share if on else 0.0
                t[f"{lid}-return-temp"] = supply + 5.5 + 1.2 * share + (1 - capacity) * 2.0

        # ---------- electricity ----------
        sub_load = {s: 0.0 for s in SUBSTATION_BACKUP}
        for i, (z, sub) in enumerate(ZONE_SUBSTATION.items(), 1):
            fid, tid = f"elec-feeder-{i:02d}", f"elec-tx-{i:02d}"
            load = self.zone_demand[z][1]
            pump = g.zone_pump[z]
            if self.pump_on[pump]:
                load += t[f"{pump}-flow"] * 0.0006  # ~0.6 kW per m3/h
            for pid, (_, home, _s) in COOLING_PLANTS.items():
                if home == z:
                    load += plant_elec[pid]
            feeding = sub if self.sub_on[sub] else SUBSTATION_BACKUP[sub]
            if not self.sub_on[sub]:
                load *= 0.55  # backup carries priority loads only (load shedding)
            if not self.sub_on[feeding]:
                load = 0.0
            sub_load[feeding] += load
            t[f"{fid}-load"] = load
            t[f"{tid}-temperature"] = 38 + 0.6 * load + 0.4 * (self.ambient - 30)
        for s, load in sub_load.items():
            t[f"{s}-load"] = load if self.sub_on[s] else 0.0
            t[f"{s}-voltage"] = (22.4 - 0.012 * load) if self.sub_on[s] else 0.0
        return t

    def step(self, ts: datetime | None = None) -> tuple[datetime, dict[str, float], dict[str, float | None]]:
        ts = ts or datetime.now(timezone.utc)
        targets = self._targets(ts)
        readings: dict[str, float | None] = {}
        for s in self.g.topology.sensors:
            target = targets[s.id]
            prev = self.state.get(s.id, target)
            # first-order lag gives realistic ramps instead of jumps
            v = prev + 0.45 * (target - prev)
            self.state[s.id] = v
            noisy = v + self.rng.normal(0, 0.003 * max(abs(v), 1.0))
            self.truth[s.id] = v
            readings[s.id] = noisy
        for ev in self._active("stuck_sensor"):
            readings[ev.target] = self.stuck.setdefault(ev.target, readings[ev.target])
        self.tick += 1
        return ts, self.truth, readings

    def history(self, start: datetime, hours: int, step_minutes: int = 15) -> list[tuple[datetime, dict[str, float]]]:
        """Fast-forward on a coarse grid for model training. Uses a separate RNG stream."""
        saved = (self.rng, self.state, self.tick)
        self.rng, self.state = np.random.default_rng(self.seed + 1), {}
        out = []
        for k in range(hours * 60 // step_minutes):
            ts = start + timedelta(minutes=k * step_minutes)
            self.state = {}
            _, _, r = self.step(ts)
            row = dict(r)
            row["weather:ambient"] = self.ambient
            for z, (w, e, c) in self.zone_demand.items():
                noise = self.rng.normal(1.0, 0.012, 3)
                row[f"demand:water:{z}"], row[f"demand:electricity:{z}"], row[f"demand:cooling:{z}"] = w * noise[0], e * noise[1], c * noise[2]
            out.append((ts, row))
        self.rng, self.state, self.tick = saved
        return out


CALIBRATION_START = datetime(2026, 8, 1, tzinfo=timezone.utc)


def calibrate(engine: Engine, days: int = 14) -> None:
    """Set each sensor's normal range from the envelope of normal operation (idempotent)."""
    if getattr(engine.g, "calibrated", False):
        return
    hist = engine.history(CALIBRATION_START, days * 24, step_minutes=30)
    vals = np.array([[r[s.id] for s in engine.g.topology.sensors] for _, r in hist])
    lo, hi = np.percentile(vals, 0.5, axis=0), np.percentile(vals, 99.5, axis=0)
    for s, a, b in zip(engine.g.topology.sensors, lo, hi):
        pad = max(0.08 * (b - a), 0.04 * abs(b), 0.05)
        digits = 2 if abs(b) < 50 else 0
        s.normal_range.min = float(round(max(0.0, float(a - pad)), digits))
        s.normal_range.max = float(round(float(b + pad), digits))
    engine.g.calibrated = True
    engine.reset()
