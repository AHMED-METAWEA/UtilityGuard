"""Benchmark the four detectors on held-out injected faults and price the delay with the cost model.

Training and test differ in leak sizes, start times, noise seeds and, for the supervised model, some locations. Delays are
cached as plain numbers; costs are computed at request time from the current assumptions, so a price change moves them.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from app.cost import Assumptions, IncidentFacts, _summary
from app.lab.detectors import D1, D2, D3, D4, Scale, scan
from app.lab.harness import MISMATCH_SD, ONSET, TICK_MINUTES, TICKS, Bench, Fault, Run, random_start
from app.lab import quality as Q
from app.lab.localize import Localizer
from app.schemas.lab import (
    BenchmarkConfig, DetectorFaultScore, DetectorScore, FaultMix, LabBenchmark, LabLocalization, LeakPoint, LeakResult,
    LabQuality, LocalizationBySize, QualityKind, SensorValue, SensorValueRow, TrialDetector, TrialResult,
)

log = logging.getLogger("utilityguard.lab")
CACHE = Path(__file__).resolve().parents[2] / "data" / "lab_benchmark.json"
VERSION = 4
WINDOW = TICKS - ONSET  # minutes a detector has to notice a fault in a run
TRAIN_SEV = [0.08, 0.14, 0.25, 0.5, 0.8, 1.0]
TEST_SEV = [0.06, 0.1, 0.18, 0.3, 0.45, 0.65, 0.9]
HOLDOUT_ZONES = ["zone-cult", "zone-pres", "zone-res-ne"]
NAMES = {"d1": D1, "d2": D2, "d3": D3, "d4": D4}
KIND_LABEL = {"pipe_burst": "Pipe burst", "pump_failure": "Pump failure", "substation_trip": "Substation trip",
              "cooling_fault": "Cooling plant fault"}


def _fault_list(b: Bench, burst_sev: list[float], cool_sev: list[float], zones: set[str] | None, exclude: bool) -> list[Fault]:
    """Faults at locations inside (exclude=False) or outside (exclude=True) the given set of zones."""
    ok = (lambda z: z not in zones) if exclude else (lambda z: zones is None or z in zones)
    out: list[Fault] = []
    for z, pipe in b.g.zone_pipe.items():
        if ok(z):
            out += [Fault("pipe_burst", pipe, s) for s in burst_sev]
    for z, pump in b.g.zone_pump.items():
        if ok(z):
            out.append(Fault("pump_failure", pump))
    out += [Fault("substation_trip", s) for s in sorted({s for s in __import__("app.sim.topology", fromlist=["x"]).ZONE_SUBSTATION.values()})]
    out += [Fault("cooling_fault", p, s) for p in ("cool-plant-01", "cool-plant-02") for s in cool_sev]
    return out


@dataclass
class Fit:
    """Everything trained for the lab: the engines, the scaling, the four detectors and the localizer."""
    bench: Bench
    scale: Scale
    det: dict
    loc: Localizer
    rng: np.random.Generator
    clean_test: list[Run]
    test: list[Run]
    train_runs: int


def fit(seed: int = 42) -> Fit:
    b = Bench(seed)
    rng = np.random.default_rng(seed + 500)
    holdout = set(HOLDOUT_ZONES)

    water_groups = [[x for x in b.sensors if x.startswith(a.id + "-")] for a in b.g.topology.assets if a.network == "water"]
    water_groups = [g for g in water_groups if g]

    def runs_for(faults: list[Fault], starts: int, base: int, dropout: float = 0.0) -> list[Run]:
        """With dropout > 0 a share of the runs has one water asset's sensors offline, so a model learns to cope with gaps."""
        out = []
        for i, f in enumerate(faults):
            for j in range(starts):
                off = frozenset(water_groups[int(rng.integers(len(water_groups)))]) if rng.random() < dropout else frozenset()
                out.append(b.run(f, base + i * 31 + j, random_start(rng), disabled=off))
        return out

    clean = lambda n, base: [b.run(None, base + i, random_start(rng)) for i in range(n)]  # noqa: E731
    clean_fit, clean_cal, clean_test = clean(30, 1000), clean(20, 2000), clean(30, 3000)
    train = runs_for(_fault_list(b, TRAIN_SEV, [0.4, 0.9], holdout, exclude=True), 3, 10000, dropout=0.35)
    test = runs_for(_fault_list(b, TEST_SEV, [0.3, 0.65], None, exclude=False), 2, 50000)
    scale = Scale.fit(clean_fit, b)
    det = {"d1": D1(scale), "d2": D2(scale, clean_cal), "d3": D3(scale, clean_fit, clean_cal, seed), "d4": D4(scale, train, clean_fit, clean_cal, seed)}
    loc = Localizer.build(b, clean_fit, seed)
    return Fit(b, scale, det, loc, rng, clean_test, test, len(train) + len(clean_fit))


def build(seed: int = 42, models: Fit | None = None) -> dict:
    t0 = time.time()
    m = models or fit(seed)
    b, det, loc, rng, clean_test, test = m.bench, m.det, m.loc, m.rng, m.clean_test, m.test
    holdout = set(HOLDOUT_ZONES)
    rows = []
    fa = {k: 0 for k in det}
    hours = 0.0
    for r in clean_test:
        hours += TICKS * TICK_MINUTES / 60
        for k, d in det.items():
            fa[k] += int(scan(d.flag(r), None)[0])
    for r in test:
        f = r.fault
        hours += ONSET * TICK_MINUTES / 60  # the quiet stretch before the onset counts as clean time too
        res = {}
        for k, d in det.items():
            early, after = scan(d.flag(r), r.onset)
            fa[k] += int(early)
            res[k] = None if after is None else (after - r.onset) * TICK_MINUTES
        rows.append(dict(kind=f.kind, target=f.target, severity=f.severity, zone=r.zone, seen=r.zone not in holdout or f.kind in ("substation_trip", "cooling_fault"),
                         facts=r.facts, delays=res))
    loc_acc = loc.accuracy([18, 30, 54, 90, 150, 270], 2, seed + 9)["rows"]
    quality = Q.evaluate(b, m.scale.sigma, clean_test, rng)
    out = dict(version=VERSION, seed=seed, rows=rows, localization=loc_acc, quality=quality, sensor_value=sensor_value(b, det["d4"], loc, rng), false_alarms=fa, clean_hours=hours, train_runs=m.train_runs,
               test_runs=len(test), holdout_zones=HOLDOUT_ZONES, built_seconds=round(time.time() - t0, 1),
               zone_types={z.id: z.type for z in b.g.topology.zones})
    log.info("lab benchmark built in %.1fs (%d test runs)", time.time() - t0, len(test))
    return out


SV_LEAK = 60.0  # m3/h, a leak that matters but is not obvious
SV_STARTS = 2


def sensor_value(b: Bench, d4, loc: Localizer, rng: np.random.Generator) -> dict:
    """Take one water asset's sensors offline at a time and see what happens to finding and locating a burst."""
    cases = [(pipe, random_start(rng), 70000 + i * 13 + j) for i, pipe in enumerate(b.g.zone_pipe.values()) for j in range(SV_STARTS)]

    def trial(disabled: frozenset[str]) -> tuple[list[int | None], float]:
        delays, hits = [], 0
        for pipe, start, seed in cases:
            r = b.run(Fault("pipe_burst", pipe, SV_LEAK / 300.0), seed, start, disabled=disabled)
            _, after = scan(d4.flag(r), r.onset)
            delays.append(None if after is None else after - r.onset)
            z = np.nanmean(r.residual[ONSET + 25:ONSET + 40], axis=0) / loc.scale.sigma
            z[[b.index[x] for x in disabled]] = np.nan
            top = loc.localize(z).candidates[0]
            hits += top.asset_id == pipe
        return delays, hits / len(cases)

    base_delays, base_top1 = trial(frozenset())
    water = [a for a in b.g.topology.assets if a.network == "water"]
    rows = []
    for a in water:
        sensors = [s for s in b.sensors if s.startswith(a.id + "-")]
        if not sensors:
            continue
        delays, top1 = trial(frozenset(sensors))
        rows.append(dict(asset_id=a.id, name=a.name, kind=a.kind, sensors=sensors, delays=delays, top1=top1))
    return dict(leak_m3h=SV_LEAK, runs=len(cases), base_delays=base_delays, base_top1=base_top1, rows=rows,
                zones=[b.zone_of_pipe[c[0]] for c in cases])


def facts_for(row: dict, zone_types: dict[str, str]) -> IncidentFacts:
    f, kind, zone = row["facts"], row["kind"], row["zone"]
    zones = f.get("zones") or ([zone] if zone else [])
    return IncidentFacts(kind=kind, zone_types={z: zone_types[z] for z in zones}, leak_m3h=f.get("leak_m3h", 0.0),
                         unserved_mw=f.get("unserved_mw", 0.0), cooling_deficit_tons=f.get("cooling_deficit_tons", 0.0),
                         pumps_stopped=f.get("pumps_stopped", 1 if kind == "pump_failure" else 0),
                         plants_affected=2 if kind == "substation_trip" and f.get("unserved_mw") else (1 if kind == "cooling_fault" else 0))


def hourly_cost(row: dict, zone_types: dict[str, str], A: Assumptions, cache: dict) -> float:
    key = (row["kind"], row["zone"], round(row["facts"].get("leak_m3h", 0)), round(row["facts"].get("unserved_mw", 0), 2),
           round(row["facts"].get("cooling_deficit_tons", 0)))
    if key not in cache:
        cache[key] = float(_summary(facts_for(row, zone_types), A)["c_delay"])
    return cache[key]


def _median(x: list[float]) -> float | None:
    return float(np.median(x)) if x else None


def summarize(data: dict, A: Assumptions) -> LabBenchmark:
    rows, zt = data["rows"], data["zone_types"]
    cache: dict = {}
    for r in rows:
        r["hourly"] = hourly_cost(r, zt, A, cache)
    scores = []
    meta = {k: v for k, v in NAMES.items()}
    for k, cls in meta.items():
        delays = [r["delays"][k] for r in rows]
        found = [d for d in delays if d is not None]
        cost = lambda r, k=k: r["hourly"] * (r["delays"][k] if r["delays"][k] is not None else WINDOW) / 60  # noqa: E731
        unseen = [r for r in rows if not r["seen"]]
        by_fault = []
        for kind in KIND_LABEL:
            sub = [r for r in rows if r["kind"] == kind]
            if not sub:
                continue
            got = [r["delays"][k] for r in sub if r["delays"][k] is not None]
            by_fault.append(DetectorFaultScore(kind=kind, label=KIND_LABEL[kind], runs=len(sub), detected=len(got), detection_rate=round(len(got) / len(sub), 3),
                                               median_delay_min=_median(got), mean_delay_cost=round(float(np.mean([cost(r) for r in sub])), 2)))
        scores.append(DetectorScore(
            id=k, name=cls.name, kind=cls.kind, note=cls.note, false_alarms_per_100h=round(data["false_alarms"][k] / data["clean_hours"] * 100, 2),
            detection_rate=round(len(found) / len(rows), 3), median_delay_min=_median(found),
            p90_delay_min=float(np.percentile(found, 90)) if found else None, mean_delay_cost=round(float(np.mean([cost(r) for r in rows])), 2),
            unseen_detection_rate=round(sum(r["delays"][k] is not None for r in unseen) / len(unseen), 3) if unseen else None,
            unseen_median_delay_min=_median([r["delays"][k] for r in unseen if r["delays"][k] is not None]),
            by_fault=by_fault))
    curve = []
    bursts = [r for r in rows if r["kind"] == "pipe_burst"]
    for sev in sorted({r["severity"] for r in bursts}):
        sub = [r for r in bursts if r["severity"] == sev]
        results = []
        for k in meta:
            got = [r["delays"][k] for r in sub if r["delays"][k] is not None]
            results.append(LeakResult(detector_id=k, detection_rate=round(len(got) / len(sub), 3), median_delay_min=_median(got)))
        curve.append(LeakPoint(leak_m3h=round(300 * sev, 1), results=results))
    mix = [FaultMix(kind=kind, label=KIND_LABEL[kind], runs=len(sub), hourly_cost=round(float(np.mean([r["hourly"] for r in sub])), 2))
           for kind in KIND_LABEL if (sub := [r for r in rows if r["kind"] == kind])]
    quiet = [s for s in scores if s.false_alarms_per_100h <= 5.0] or scores
    best = min(quiet, key=lambda s: s.mean_delay_cost).id
    notes = [
        "Results come from the twin engine with faults injected at a random time of a random day. The detectors see readings and the twin's expectation only.",
        f"The expectation's demand is off by about {MISMATCH_SD * 100:.0f}% (slowly varying), as a real demand forecast would be.",
        "Test faults use leak sizes, start times and noise that training never saw. The supervised model also never saw some locations; its score there is shown separately.",
        "Delay cost = the cost model's hourly cost of the fault (at the current prices) times the delay. A fault a detector missed counts as the whole window.",
        "This is synthetic data. It shows how the methods compare here, not how they would score on the real network.",
    ]
    return LabBenchmark(
        generated_at=datetime.now(timezone.utc),
        config=BenchmarkConfig(tick_minutes=TICK_MINUTES, window_minutes=WINDOW * TICK_MINUTES, expectation_error_pct=round(MISMATCH_SD * 100, 1),
                               train_runs=data["train_runs"], test_runs=data["test_runs"], clean_hours=round(data["clean_hours"], 1),
                               holdout_zones=data["holdout_zones"], seed=data["seed"]),
        detectors=scores, leak_curve=curve, faults=mix, best_detector_id=best, notes=notes)


def summarize_localization(data: dict, n_sensors: int) -> LabLocalization:
    rows = data["localization"]
    runs = sum(r["runs"] for r in rows)
    return LabLocalization(by_size=[LocalizationBySize(leak_m3h=r["leak_m3h"], top1=round(r["top1"], 3), top3=round(r["top3"], 3), runs=r["runs"]) for r in rows],
                           top1=round(sum(r["top1"] * r["runs"] for r in rows) / runs, 3), top3=round(sum(r["top3"] * r["runs"] for r in rows) / runs, 3),
                           runs=runs, sensors_total=n_sensors)


def summarize_quality(data: dict) -> LabQuality:
    q = data["quality"]
    kinds = []
    for r in q["rows"]:
        got = [d for d in r["delays"] if d is not None]
        kinds.append(QualityKind(kind=r["kind"], label=r["label"], runs=len(r["delays"]), detected=len(got), detection_rate=round(len(got) / len(r["delays"]), 3),
                                 median_delay_min=_median(got)))
    return LabQuality(kinds=kinds, false_flags_per_100_sensor_hours=round(q["flagged_clean"] / q["sensor_hours"] * 100, 2), sensor_hours=round(q["sensor_hours"], 1),
                      process_runs=q["process_runs"], process_false_flags=q["process_flags"], window_ticks=Q.WINDOW)


def summarize_sensors(data: dict, A: Assumptions) -> SensorValue:
    sv, zt = data["sensor_value"], data["zone_types"]
    cache: dict = {}
    hourly = float(np.mean([_summary(IncidentFacts(kind="pipe_burst", zone_types={z: zt[z]}, leak_m3h=sv["leak_m3h"]), A)["c_delay"] for z in sv["zones"]]))
    eff = lambda d: [WINDOW if x is None else x for x in d]  # noqa: E731
    base = float(np.mean(eff(sv["base_delays"])))
    found = [d for d in sv["base_delays"] if d is not None]
    rows = []
    for r in sv["rows"]:
        got = [d for d in r["delays"] if d is not None]
        extra = float(np.mean(eff(r["delays"]))) - base
        rows.append(SensorValueRow(asset_id=r["asset_id"], name=r["name"], kind=r["kind"], sensors=r["sensors"],
                                   detection_rate=round(len(got) / len(r["delays"]), 3), median_delay_min=_median(got),
                                   extra_delay_min=round(extra, 2), extra_cost=round(max(extra, 0) * hourly / 60, 2), localization_top1=round(r["top1"], 3)))
    rows.sort(key=lambda x: (-x.extra_cost, x.localization_top1, x.asset_id))
    return SensorValue(detector_id="d4", leak_m3h=sv["leak_m3h"], runs_per_asset=sv["runs"],
                       baseline_detection_rate=round(len(found) / len(sv["base_delays"]), 3), baseline_delay_min=_median(found),
                       baseline_localization_top1=round(sv["base_top1"], 3), hourly_cost=round(hourly, 2), rows=rows)


class LabCache:
    """Builds the benchmark once and keeps its results on disk; the trained models stay in memory for the interactive trials."""

    def __init__(self, seed: int):
        self.seed = seed
        self.data: dict | None = None
        self.fit: Fit | None = None
        self.lock = threading.Lock()
        self.fit_lock = threading.Lock()
        self.run_lock = threading.Lock()  # the benchmark engines are shared, so one run at a time

    def load(self) -> dict | None:
        if self.data is None and CACHE.exists():
            try:
                d = json.loads(CACHE.read_text())
                if d.get("version") == VERSION and d.get("seed") == self.seed:
                    self.data = d
            except Exception:  # noqa: BLE001
                log.warning("lab cache unreadable, rebuilding")
        return self.data

    def models(self) -> Fit:
        with self.fit_lock:
            if self.fit is None:
                self.fit = fit(self.seed)
            return self.fit

    def ensure(self) -> dict:
        with self.lock:
            if self.load() is None:
                with self.run_lock:
                    self.data = build(self.seed, self.models())
                CACHE.parent.mkdir(parents=True, exist_ok=True)
                CACHE.write_text(json.dumps(self.data))
            return self.data

    def live_quality_model(self):
        """Quality model for the running twin: its expectation has no demand error, so the residual scale comes from clean runs without one."""
        with self.fit_lock:
            if getattr(self, "_qm", None) is None:
                b = Bench(self.seed)
                rng = np.random.default_rng(self.seed + 31)
                runs = [b.run(None, 8000 + i, random_start(rng), mismatch=False) for i in range(12)]
                self._qm = Q.model_for(b, Scale.fit(runs, b).sigma)
            return self._qm

    def warm(self) -> None:
        self.ensure()
        self.models()

    def trial(self, req, A: Assumptions) -> TrialResult:
        """Inject a hidden burst into a copy of the twin and see what each detector and the localizer make of it."""
        m = self.models()
        b = m.bench
        rng = np.random.default_rng(req.seed if req.seed is not None else int(time.time() * 1000) % 10 ** 9)
        pipes = list(b.g.zone_pipe.values())
        pipe = req.pipe_id if req.pipe_id in pipes else pipes[int(rng.integers(len(pipes)))]
        size = float(min(max(req.leak_m3h, 10.0), 300.0))
        off = frozenset(x for x in req.disabled_sensors if x in b.index)
        seed = int(rng.integers(1, 10 ** 6))
        with self.run_lock:
            r = b.run(Fault("pipe_burst", pipe, size / 300.0), seed, random_start(rng), disabled=off)
            flags = {k: scan(d.flag(r), r.onset) for k, d in m.det.items()}
            z = np.nanmean(r.residual[ONSET + 25:ONSET + 40], axis=0) / m.loc.scale.sigma
            z[[b.index[x] for x in off]] = np.nan
            loc = m.loc.localize(z)
        zone = b.zone_of_pipe[pipe]
        hourly = float(_summary(IncidentFacts(kind="pipe_burst", zone_types={zone: b.g.zone_index[zone].type}, leak_m3h=size), A)["c_delay"])
        names = {x.id: x.name for x in b.g.topology.assets}
        dets = [TrialDetector(detector_id=k, name=NAMES[k].name, detected=after is not None, delay_min=None if after is None else float((after - r.onset) * TICK_MINUTES))
                for k, (_, after) in flags.items()]
        return TrialResult(truth_asset_id=pipe, truth_name=names[pipe], leak_m3h=size, window_min=WINDOW * TICK_MINUTES, hourly_cost=round(hourly, 2),
                           detectors=dets, localization=loc, hit_rank=next((c.rank for c in loc.candidates if c.asset_id == pipe), None),
                           disabled_sensors=sorted(off))
