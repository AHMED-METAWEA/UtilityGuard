"""Fault runs on the twin engine, sampled at one tick = one minute (a typical SCADA scan rate).

The live demo ticks every 2 s to move fast; the benchmark uses minute ticks so ramps and delays are in realistic units.
The expectation is a second engine with no fault whose demand is off by a slowly varying error (AR(1), about 3%),
standing for a demand forecast that is never perfect.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np

from app.sim.engine import Engine, Event
from app.sim.topology import ZONE_SUBSTATION

TICKS = 180
ONSET = 60
TICK_MINUTES = 1
MISMATCH_SD = 0.033  # stationary sd of the expectation's demand error
MISMATCH_RHO = 0.97


@dataclass(frozen=True)
class Fault:
    kind: str  # pipe_burst | pump_failure | substation_trip | cooling_fault | stuck_sensor
    target: str
    severity: float = 1.0

    @property
    def label(self) -> str:
        return f"{self.kind}:{self.target}:{self.severity:g}"


@dataclass
class Run:
    fault: Fault | None
    sensors: list[str]
    readings: np.ndarray  # [ticks, sensors], nan where a sensor is disabled
    expected: np.ndarray  # [ticks, sensors]
    onset: int
    start: datetime
    zone: str | None = None
    facts: dict = field(default_factory=dict)  # what the cost model needs, read from the truth engine after the onset

    @property
    def residual(self) -> np.ndarray:
        return self.readings - self.expected


class Bench:
    """Two reusable engines plus the sensor order."""

    def __init__(self, seed: int = 42):
        self.seed = seed
        self.truth, self.shadow = Engine(seed), Engine(seed)
        self.g = self.truth.g
        self.sensors = [s.id for s in self.g.topology.sensors]
        self.index = {s: i for i, s in enumerate(self.sensors)}
        self.zone_of_pipe = {p: z for z, p in self.g.zone_pipe.items()}
        self.zone_of_pump = {p: z for z, p in self.g.zone_pump.items()}

    def zone_of(self, f: Fault) -> str | None:
        if f.kind == "pipe_burst":
            return self.zone_of_pipe.get(f.target)
        if f.kind == "pump_failure":
            return self.zone_of_pump.get(f.target)
        if f.kind == "cooling_fault":
            return self.g.asset_index[f.target].zone_id
        if f.kind == "substation_trip":
            return next((z for z, s in ZONE_SUBSTATION.items() if s == f.target), None)
        if f.kind == "stuck_sensor":
            asset = self.g.sensor_index[f.target].asset_id
            return self.g.asset_index[asset].zone_id
        return None

    def run(self, fault: Fault | None, seed: int, start: datetime, ticks: int = TICKS, onset: int = ONSET,
            disabled: frozenset[str] = frozenset(), mismatch: bool = True, truth_scale: float = 1.0) -> Run:
        t, s = self.truth, self.shadow
        t.reset(), s.reset()
        t.rng = np.random.default_rng(seed)
        s.rng = np.random.default_rng(seed + 7919)
        noise = np.random.default_rng(seed + 104729)
        err = 0.0
        n = len(self.sensors)
        readings, expected = np.full((ticks, n), np.nan), np.full((ticks, n), np.nan)
        facts: dict = {}
        for k in range(ticks):
            ts = start + timedelta(minutes=TICK_MINUTES * k)
            if fault and k == onset:
                t.add_event(Event(fault.kind, fault.target, fault.severity, run_id="lab"))
            err = MISMATCH_RHO * err + noise.normal(0, MISMATCH_SD * np.sqrt(1 - MISMATCH_RHO ** 2)) if mismatch else 0.0
            s.demand_scale = 1.0 + err
            t.demand_scale = truth_scale
            _, _, rt = t.step(ts)
            _, truth_s, _ = s.step(ts)
            for i, sid in enumerate(self.sensors):
                v = rt[sid]
                readings[k, i] = np.nan if (sid in disabled or v is None) else v
                expected[k, i] = truth_s[sid]
            if fault and k == onset + 30:
                facts = self._facts(fault)
        return Run(fault, self.sensors, readings, expected, onset, start, self.zone_of(fault) if fault else None, facts)

    def _facts(self, f: Fault) -> dict:
        """The quantities the cost model needs for this fault, read from the truth engine 30 ticks after the onset."""
        t = self.truth
        z = self.zone_of(f)
        out: dict = {"zone": z}
        if f.kind == "pipe_burst":
            out["leak_m3h"] = float(t.leak_m3h.get(f.target, 0.0))
        elif f.kind == "cooling_fault":
            from app.cost import TONS_PER_MW
            plant = f.target
            need = sum(t.zone_demand[zz][2] for zz in self._served(plant))
            out["cooling_deficit_tons"] = float(max(need - t._plant_delivered.get(plant, 0.0), 0.0) * TONS_PER_MW)
            out["zones"] = self._served(plant)
        elif f.kind == "substation_trip":
            zones = [zz for zz, s in ZONE_SUBSTATION.items() if s == f.target]
            out["zones"] = zones
            out["unserved_mw"] = float(sum(t.zone_demand[zz][1] for zz in zones) * 0.45)
            out["pumps_stopped"] = len([p for p in self.g.zone_pump.values() if not t.pump_on.get(p, True)])
        return out

    def _served(self, plant: str) -> list[str]:
        from app.sim.topology import COOLING_PLANTS
        return list(COOLING_PLANTS[plant][2])


def random_start(rng: np.random.Generator) -> datetime:
    """A start time on a random day and hour of the calibration season, so faults meet different demand states."""
    base = datetime(2026, 10, 5, tzinfo=timezone.utc)
    return base + timedelta(days=int(rng.integers(0, 20)), hours=int(rng.integers(0, 24)), minutes=int(rng.integers(0, 60)))
