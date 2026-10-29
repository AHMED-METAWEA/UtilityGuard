"""Scenario catalog and the mapping from a scenario to simulation events."""

from __future__ import annotations

from app.schemas import Scenario, ScenarioParam
from app.sim.engine import Event
from app.sim.topology import build

_g = build()
_pipes = [a.id for a in _g.topology.assets if a.kind == "pipe"]
_pumps = [a.id for a in _g.topology.assets if a.kind == "pump"]
_subs = [a.id for a in _g.topology.assets if a.kind == "substation"]
_plants = [a.id for a in _g.topology.assets if a.kind == "plant"]
_sensors = [s.id for s in _g.topology.sensors if s.network == "water" and s.kind == "pressure"]
_all_sensors = [s.id for s in _g.topology.sensors]

CATALOG = [
    Scenario(id="scn-pipe-burst", name="Pipe burst", description="A water main bursts and loses water.", network="water",
             parameters=[ScenarioParam(name="targetAsset", type="asset", default=_g.zone_pipe["zone-gov"], options=_pipes),
                         ScenarioParam(name="severity", type="number", default=0.6, min=0.1, max=1)]),
    Scenario(id="scn-stuck-sensor", name="Stuck sensor", description="A pressure sensor freezes on one value.", network="water",
             parameters=[ScenarioParam(name="targetSensor", type="select", default=f"{_g.zone_pipe['zone-cbd']}-pressure", options=_sensors)]),
    Scenario(id="scn-sensor-drift", name="Sensor drift", description="A sensor's readings slowly walk away from the truth. Shows up in data quality, not as a fault.", network="multi",
             parameters=[ScenarioParam(name="targetSensor", type="select", default=f"{_g.zone_pipe['zone-cbd']}-pressure", options=_all_sensors),
                         ScenarioParam(name="severity", type="number", default=0.5, min=0.1, max=1)]),
    Scenario(id="scn-sensor-dropout", name="Sensor dropout", description="A sensor stops reporting. The twin keeps going on its model.", network="multi",
             parameters=[ScenarioParam(name="targetSensor", type="select", default=f"{_g.zone_pipe['zone-gov']}-flow-in", options=_all_sensors)]),
    Scenario(id="scn-substation-trip", name="Substation trip", description="A substation trips. Pumps and cooling plants it powers stop.", network="multi",
             parameters=[ScenarioParam(name="targetAsset", type="asset", default="elec-sub-02", options=_subs)]),
    Scenario(id="scn-pump-failure", name="Pump failure", description="A pump degrades over a minute and stops.", network="water",
             parameters=[ScenarioParam(name="targetAsset", type="asset", default=_g.zone_pump["zone-cbd"], options=_pumps)]),
    Scenario(id="scn-cooling-fault", name="Cooling plant fault", description="A chiller fails and supply temperature rises.", network="cooling",
             parameters=[ScenarioParam(name="targetAsset", type="asset", default="cool-plant-01", options=_plants),
                         ScenarioParam(name="severity", type="number", default=0.7, min=0.2, max=1)]),
    Scenario(id="scn-heat-wave", name="Heat wave", description="Air temperature rises; cooling and electrical load climb.", network="multi",
             parameters=[ScenarioParam(name="temperature", type="number", default=46, min=38, max=50)]),
    Scenario(id="scn-iftar-surge", name="Ramadan iftar surge", description="Residential districts open the fast together: the evening peak in water and power arrives at once.", network="multi",
             parameters=[ScenarioParam(name="uplift", type="number", default=0.45, min=0.1, max=0.9)]),
    Scenario(id="scn-hidden-leak", name="Hidden slow leak", description="A joint starts weeping and grows over minutes, too small for a pressure alarm. Only the flow balance gives it away.", network="water",
             parameters=[ScenarioParam(name="targetAsset", type="asset", default=_g.zone_pipe["zone-cult"], options=_pipes),
                         ScenarioParam(name="severity", type="number", default=0.2, min=0.08, max=0.3)]),
    Scenario(id="scn-load-shedding", name="Rolling load shedding", description="The grid operator cuts a substation for a planned window, then restores it. Pumps and plants on it stop and restart.", network="multi",
             parameters=[ScenarioParam(name="targetAsset", type="asset", default="elec-sub-04", options=_subs),
                         ScenarioParam(name="durationSeconds", type="number", default=90, min=30, max=240)]),
    Scenario(id="scn-storm", name="Cascade storm", description="A heat wave hits the working-day peak. Cooling reaches its limit, substations overload and trip one after another, and the pumps and plants they feed stop.", network="multi",
             parameters=[ScenarioParam(name="temperature", type="number", default=47, min=44, max=50)]),
    Scenario(id="scn-holiday", name="Holiday demand", description="Government zones go quiet and residential demand rises.", network="multi",
             parameters=[ScenarioParam(name="factor", type="number", default=1.3, min=1, max=1.8)]),
]
BY_ID = {s.id: s for s in CATALOG}


def events_for(scenario_id: str, params: dict, run_id: str) -> list[Event]:
    sc = BY_ID[scenario_id]
    p = {x.name: params.get(x.name, x.default) for x in sc.parameters}
    if scenario_id == "scn-iftar-surge":
        return [Event(kind="iftar_surge", target=None, severity=float(p["uplift"]), run_id=run_id, params=p)]
    if scenario_id == "scn-hidden-leak":  # same physics as a burst, but it grows over 4 minutes instead of 16 seconds
        return [Event(kind="pipe_burst", target=p["targetAsset"], severity=float(p["severity"]), run_id=run_id, params={**p, "ramp_ticks": 120})]
    if scenario_id == "scn-load-shedding":
        return [Event(kind="substation_trip", target=p["targetAsset"], severity=1.0, run_id=run_id, params={**p, "cause": "load_shedding"},
                      duration=int(float(p["durationSeconds"]) / 2))]
    if scenario_id == "scn-storm":  # one run: a slow heat build plus the protection relays that make it cascade
        return [Event(kind="heat_wave", target=None, severity=float(p["temperature"]), run_id=run_id, params={**p, "ramp_ticks": 40}),
                Event(kind="storm", target=None, severity=1.0, run_id=run_id, params=p)]
    kind = {"scn-pipe-burst": "pipe_burst", "scn-stuck-sensor": "stuck_sensor", "scn-substation-trip": "substation_trip",
            "scn-pump-failure": "pump_failure", "scn-cooling-fault": "cooling_fault", "scn-heat-wave": "heat_wave",
            "scn-holiday": "holiday", "scn-sensor-drift": "sensor_drift", "scn-sensor-dropout": "sensor_dropout"}[scenario_id]
    target = p.get("targetAsset") or p.get("targetSensor")
    severity = float(p.get("severity") or p.get("temperature") or p.get("factor") or 1.0)
    return [Event(kind=kind, target=target, severity=severity, run_id=run_id, params=p)]
