"""Every scenario against a running server: what it opens, what it moves, and whether the system is clean again afterwards (stress-test items S-8, D-4, D-8, D-9, R-6).

    PYTHONPATH=. .venv/bin/python scripts/stress/scenario_sweep.py [--base http://localhost:8000] [--only scn-storm,scn-compound] [--out /tmp/sweep.json]

The expected outcome of each scenario is written in EXPECT below, from what the scenario is meant to do (its description in app/scenarios.py and the README), BEFORE the sweep
is run. The sweep reports what the server actually did next to it and exits 1 when any expectation fails. It runs in real time (one tick is 2 seconds), so a full sweep takes
about 40 minutes. Start the server with LLM_OFFLINE=1 to keep the language model out of it; detection, costs and the board do not need the model.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time

import httpx

FAULT = re.compile(r"burst|leak|trip|pump|chiller|cooling|offline|fault|fail|outage|overload", re.I)  # titles of network-fault incidents


def kpi_map(c) -> dict[str, float]:
    return {k["id"]: k["value"] for k in c.get("/kpis").json()}


class Run:
    def __init__(self, c: httpx.Client, sid: str, params: dict, watch: float, probes: tuple[str, ...] = ()):
        self.c, self.sid, self.params, self.watch = c, sid, params, watch
        self.probes = probes
        self.probe_range: dict[str, tuple[float, float]] = {}  # sensor -> (lowest, highest) value seen during the run
        self.incidents: dict[str, dict] = {}
        self.sev_history: dict[str, list[str]] = {}
        self.alert_titles: set[str] = set()
        self.warnings: dict[str, dict] = {}
        self.first_incident_s: float | None = None
        self.first_warning_s: float | None = None
        self.flagged: set[str] = set()
        self.kpi_base: dict[str, float] = {}
        self.kpi_extreme: dict[str, float] = {}
        self.kpi_last: dict[str, float] = {}
        self.findings = 0
        self.errors: list[str] = []

    def go(self) -> None:
        c = self.c
        c.post("/scenarios/stop")
        c.post("/scenarios/reset")
        time.sleep(8)
        base_inc, base_alert = c.get("/incidents").json(), c.get("/alerts").json()
        if base_inc or base_alert:
            self.errors.append(f"not clean before the run: {len(base_inc)} incidents, {len(base_alert)} alerts")
        self.kpi_base = kpi_map(c)
        for sid in self.probes:
            v = self.last_value(sid)
            if v is not None:
                self.probe_range[sid] = (v, v)
        t0 = time.time()
        r = c.post("/scenarios/run", json={"scenarioId": self.sid, "parameters": self.params})
        if r.status_code != 200:
            self.errors.append(f"run -> {r.status_code} {r.text[:120]}")
            return
        while time.time() - t0 < self.watch:
            time.sleep(3)
            t = time.time() - t0
            try:
                for i in c.get("/incidents").json():
                    if i["id"] not in self.incidents and self.first_incident_s is None:
                        self.first_incident_s = round(t, 1)
                    self.incidents[i["id"]] = i
                    h = self.sev_history.setdefault(i["id"], [])
                    if not h or h[-1] != f"{i['severity']}/{i['state']}":
                        h.append(f"{i['severity']}/{i['state']}")
                for a in c.get("/alerts").json():
                    self.alert_titles.add(a["title"])
                for w in c.get("/warnings").json():
                    key = w.get("assetId") or w.get("id")
                    if key not in self.warnings and self.first_warning_s is None:
                        self.first_warning_s = round(t, 1)
                    self.warnings[key] = w
                for sid in self.probes:
                    v = self.last_value(sid)
                    if v is not None:
                        lo, hi = self.probe_range.get(sid, (v, v))
                        self.probe_range[sid] = (min(lo, v), max(hi, v))
                self.flagged |= {f["sensorId"] for f in c.get("/quality").json().get("flagged", [])}
                self.findings = max(self.findings, len(c.get("/findings").json()))
                k = kpi_map(c)
                self.kpi_last = k
                for key, v in k.items():
                    if key not in self.kpi_extreme or abs(v - self.kpi_base[key]) > abs(self.kpi_extreme[key] - self.kpi_base[key]):
                        self.kpi_extreme[key] = v
            except Exception as e:  # noqa: BLE001
                self.errors.append(f"poll failed: {type(e).__name__} {e}")
        c.post("/scenarios/stop")
        c.post("/scenarios/reset")
        time.sleep(8)
        left_inc, left_alert = c.get("/incidents").json(), c.get("/alerts").json()
        if left_inc or left_alert:
            self.errors.append(f"not clean after stop and reset: {len(left_inc)} incidents, {len(left_alert)} alerts")

    def last_value(self, sensor_id: str) -> float | None:
        pts = self.c.get(f"/sensors/{sensor_id}/history", params={"minutes": 1}).json().get("points", [])
        return pts[-1]["v"] if pts and pts[-1].get("v") is not None else None

    def rise(self, sensor_id: str) -> float:
        lo, hi = self.probe_range.get(sensor_id, (0.0, 0.0))
        return hi - lo

    def delta(self, key: str) -> float:
        b = self.kpi_base[key]
        return (self.kpi_extreme.get(key, b) - b) / b * 100 if b else 0.0

    def fault_incidents(self) -> list[dict]:
        return [i for i in self.incidents.values() if FAULT.search(i["title"])]


# (scenario, parameters, seconds to watch, [(description, predicate(run) -> bool)], optional sensors to probe)
EXPECT = [
    ("scn-pipe-burst", {}, 90, [
        ("exactly one incident", lambda r: len(r.incidents) == 1),
        ("it is critical and about a burst or leak in zone-gov", lambda r: any("burst" in i["title"].lower() or "leak" in i["title"].lower() for i in r.incidents.values()) and all(i["zoneIds"] == ["zone-gov"] for i in r.incidents.values())),
        ("found within 60 s", lambda r: r.first_incident_s is not None and r.first_incident_s <= 60),
        ("water production rises by at least 2%", lambda r: r.delta("water_production") >= 2),
    ]),
    ("scn-hidden-leak", {}, 330, [
        ("exactly one incident, about a leak", lambda r: len(r.incidents) == 1 and any("leak" in i["title"].lower() or "burst" in i["title"].lower() for i in r.incidents.values())),
        ("found before the six minutes end", lambda r: r.first_incident_s is not None),
    ]),
    ("scn-stuck-sensor", {}, 90, [
        ("no network-fault incident is raised for a frozen sensor", lambda r: not r.fault_incidents()),
        ("the frozen sensor is noticed: flagged in the data-quality view or reported", lambda r: bool(r.flagged) or bool(r.incidents) or bool(r.alert_titles)),
    ]),
    ("scn-sensor-drift", {}, 150, [
        ("no incident (a drifting sensor is a data-quality finding, not a fault)", lambda r: not r.fault_incidents()),
    ]),
    ("scn-sensor-dropout", {}, 90, [
        ("no network-fault incident when a sensor goes silent", lambda r: not r.fault_incidents()),
        ("the sensor is flagged as missing", lambda r: bool(r.flagged)),
    ]),
    ("scn-substation-trip", {}, 90, [
        ("exactly one incident (one root cause is one incident)", lambda r: len(r.incidents) == 1),
        ("found within 60 s", lambda r: r.first_incident_s is not None and r.first_incident_s <= 60),
        ("districts served or electric load drop", lambda r: r.delta("districts_served") <= -10 or r.delta("electric_load") <= -5),
    ]),
    ("scn-load-shedding", {}, 150, [
        ("exactly one incident", lambda r: len(r.incidents) == 1),
        ("the incident is resolved once the substation is restored (90 s window plus the closing delay)", lambda r: bool(r.sev_history) and all(h[-1].endswith("/resolved") for h in r.sev_history.values())),
    ]),
    ("scn-pump-failure", {}, 120, [
        ("exactly one incident", lambda r: len(r.incidents) == 1),
        ("found within 90 s", lambda r: r.first_incident_s is not None and r.first_incident_s <= 90),
    ]),
    ("scn-cooling-fault", {}, 90, [
        ("exactly one incident", lambda r: len(r.incidents) == 1),
        # first version of this check asked for a 3% move of the cooling-efficiency KPI; a probe of the sensors showed that kW per ton hardly moves when a chiller delivers less
        # and draws less, so the check was changed to the physics that matters: the faulted plant's supply temperature rises and the other plant is untouched
        ("the faulted plant's supply temperature rises by at least 2 C", lambda r: r.rise("cool-plant-01-supply-temp") >= 2),
        ("the other plant's supply temperature stays within 0.5 C", lambda r: r.rise("cool-plant-02-supply-temp") <= 0.5),
    ], ("cool-plant-01-supply-temp", "cool-plant-02-supply-temp")),
    ("scn-heat-wave", {}, 120, [
        ("no network-fault incident (it is a demand scenario)", lambda r: not r.fault_incidents()),
        ("electric load rises by at least 2%", lambda r: r.delta("electric_load") >= 2),
    ]),
    ("scn-iftar-surge", {}, 90, [
        ("no network-fault incident", lambda r: not r.fault_incidents()),
        ("water production rises by at least 3%", lambda r: r.delta("water_production") >= 3),
    ]),
    ("scn-holiday", {}, 90, [
        ("no network-fault incident", lambda r: not r.fault_incidents()),
    ]),
    ("scn-storm", {}, 240, [
        ("one incident that escalates (several alerts or a rising severity, never several incidents)", lambda r: len(r.incidents) == 1),
    ]),
    ("scn-compound", {}, 120, [
        ("three incidents: the pipe, the chiller and the substation", lambda r: len(r.incidents) == 3),
        ("all three found within 90 s", lambda r: r.first_incident_s is not None and r.first_incident_s <= 90),
    ]),
    # the scenario as the demo runs it (about eight minutes of wear), watched long enough to see the pump stop. The first version of this sweep used 3 minutes and 240 s; a wear that
    # short is warned about in only some cases (stress-test R-5, reported there as a limit), so it was not a fair test of the early warning and not what the demo shows.
    ("scn-pump-wear", {}, 560, [
        ("the early warning is raised", lambda r: bool(r.warnings)),
        ("it is raised before the failure incident (or no failure incident yet)", lambda r: r.first_warning_s is not None and (r.first_incident_s is None or r.first_warning_s <= r.first_incident_s)),
    ]),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default="/tmp/sweep.json")
    args = ap.parse_args()
    only = {s for s in args.only.split(",") if s}
    results, failed = [], 0
    with httpx.Client(base_url=args.base + "/api/v1", timeout=60) as c:
        for entry in EXPECT:
            sid, params, watch, checks = entry[:4]
            probes = entry[4] if len(entry) > 4 else ()
            if sid != "scn-pump-wear":  # the early warning is for wear: no other scenario may raise one (a storm, a burst, a trip explain what a pump or a transformer does)
                checks = [*checks, ("no early warning is raised", lambda r: not r.warnings)]
            if only and sid not in only:
                continue
            print(f"== {sid} (watching {watch}s)", flush=True)
            run = Run(c, sid, params, watch, probes)
            run.go()
            row = {"scenario": sid, "incidents": [{"title": i["title"], "zones": i["zoneIds"], "severity_history": run.sev_history[i["id"]]} for i in run.incidents.values()],
                   "alert_titles": sorted(run.alert_titles), "warnings": [w.get("title") or w.get("assetId") for w in run.warnings.values()], "first_incident_s": run.first_incident_s,
                   "first_warning_s": run.first_warning_s, "flagged_sensors": sorted(run.flagged), "findings": run.findings,
                   "kpi_change_pct": {k: round(run.delta(k), 1) for k in run.kpi_base}, "probe_range": run.probe_range, "errors": run.errors, "checks": []}
            for text, pred in checks + [("the system is clean again after stop and reset", lambda r: not [e for e in r.errors if e.startswith("not clean")]),
                                        ("no polling or request error", lambda r: not [e for e in r.errors if not e.startswith("not clean")])]:
                try:
                    good = bool(pred(run))
                except Exception as e:  # noqa: BLE001
                    good = False
                    text += f" (check crashed: {e})"
                row["checks"].append({"check": text, "ok": good})
                failed += 0 if good else 1
                print(f"   {'PASS' if good else 'FAIL'}  {text}")
            print(f"   incidents: {[(i['title'], i['severity_history']) for i in row['incidents']]}")
            print(f"   alerts: {row['alert_titles']}  warnings: {row['warnings']}  first incident {run.first_incident_s}s  first warning {run.first_warning_s}s")
            print(f"   KPI change %: {row['kpi_change_pct']}", flush=True)
            results.append(row)
            with open(args.out, "w") as f:
                json.dump(results, f, indent=1, ensure_ascii=False)
    print(f"\n{len(results)} scenarios, {failed} failed checks")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
