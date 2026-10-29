"""AI lab shapes: detector benchmark, localization, sensor value, data-quality flags (contract 4.11)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from app.schemas import Model

DetectorKind = Literal["baseline", "statistical", "unsupervised", "supervised"]


class DetectorFaultScore(Model):
    kind: str
    label: str
    runs: int
    detected: int
    detection_rate: float
    median_delay_min: float | None = None
    mean_delay_cost: float  # EGP per fault: hourly cost of not knowing times the delay, a miss counting as the whole window


class DetectorScore(Model):
    id: str
    name: str
    kind: DetectorKind
    note: str
    false_alarms_per_100h: float
    detection_rate: float
    median_delay_min: float | None = None
    p90_delay_min: float | None = None
    mean_delay_cost: float
    unseen_detection_rate: float | None = None  # on locations the supervised model never saw in training
    unseen_median_delay_min: float | None = None
    by_fault: list[DetectorFaultScore]


class LeakResult(Model):
    detector_id: str
    detection_rate: float
    median_delay_min: float | None = None


class LeakPoint(Model):
    leak_m3h: float
    results: list[LeakResult]


class FaultMix(Model):
    kind: str
    label: str
    runs: int
    hourly_cost: float  # mean cost-model C_delay per hour over the runs of this kind


class BenchmarkConfig(Model):
    tick_minutes: int
    window_minutes: int
    expectation_error_pct: float
    train_runs: int
    test_runs: int
    clean_hours: float
    holdout_zones: list[str]
    seed: int


class LabBenchmark(Model):
    generated_at: datetime
    config: BenchmarkConfig
    detectors: list[DetectorScore]
    leak_curve: list[LeakPoint]
    faults: list[FaultMix]
    best_detector_id: str
    notes: list[str]


class LocalizationCandidate(Model):
    rank: int
    asset_id: str
    name: str
    zone_id: str
    score: float  # share of the residual explained, 0 to 1
    leak_m3h: float  # estimated leak size if this pipe is the one


class Localization(Model):
    method: str
    sensors_used: int
    sensors_total: int
    demand_error_pct: float  # estimated error of the expectation's demand, fitted together with the leak
    candidates: list[LocalizationCandidate]
    ambiguous: bool  # the top two are too close to call
    incident_id: str | None = None


class LocalizationBySize(Model):
    leak_m3h: float
    top1: float
    top3: float
    runs: int


class SensorValueRow(Model):
    asset_id: str
    name: str
    kind: str
    sensors: list[str]
    detection_rate: float  # of test bursts still found with this asset's sensors offline
    median_delay_min: float | None = None
    extra_delay_min: float  # mean delay minus the delay with every sensor on, a missed burst counting as the whole window
    extra_cost: float  # EGP per burst: extra delay times the cost model's hourly cost of a burst of this size
    localization_top1: float


class SensorValue(Model):
    detector_id: str
    leak_m3h: float
    runs_per_asset: int
    baseline_detection_rate: float
    baseline_delay_min: float | None = None
    baseline_localization_top1: float
    hourly_cost: float
    rows: list[SensorValueRow]


class LabLocalization(Model):
    by_size: list[LocalizationBySize]
    top1: float
    top3: float
    runs: int
    sensors_total: int


class TrialRequest(Model):
    pipe_id: str | None = None  # random when omitted
    leak_m3h: float = 60.0
    disabled_sensors: list[str] = []
    seed: int | None = None


class TrialDetector(Model):
    detector_id: str
    name: str
    detected: bool
    delay_min: float | None = None


class TrialResult(Model):
    """A hidden burst injected into a copy of the twin; the detectors and the localizer see readings only."""
    truth_asset_id: str
    truth_name: str
    leak_m3h: float
    window_min: int
    hourly_cost: float
    detectors: list[TrialDetector]
    localization: Localization
    hit_rank: int | None = None
    disabled_sensors: list[str]


class QualityFlagItem(Model):
    sensor_id: str
    status: Literal["missing", "stuck", "drift"]
    detail: str


class QualityNow(Model):
    sensors_total: int
    flagged: list[QualityFlagItem]


class QualityKind(Model):
    kind: str
    label: str
    runs: int
    detected: int
    detection_rate: float
    median_delay_min: float | None = None


class LabQuality(Model):
    kinds: list[QualityKind]
    false_flags_per_100_sensor_hours: float  # distinct sensors flagged in fault-free runs
    sensor_hours: float
    process_runs: int  # bursts, trip, pump, chiller, heat wave and holiday: the process changed, no sensor is at fault
    process_false_flags: int  # sensors flagged in those runs
    window_ticks: int
