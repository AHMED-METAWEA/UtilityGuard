# Detection: recall, false alarms and precision (matched pairs)

Measured 01 November 2026 at commit `3f3945c` with `scripts/stress/detection_pr.py` (seed 1, 2 start times per case, 212 fault runs, each with a control run on the same seed and start time). Took 29.3 minutes, no network and no language model.

The twin is simulated, so these numbers describe the detector on the simulation, not on equipment in the field.

## Recall

**Overall recall (the right kind of incident within the watch window): 93% (198 of 212), 95% interval 89% to 96%.** Any incident at all: 93%. A wrong kind of incident only: 0.

| Scenario | Severity | Runs | Recall | 95% interval | Right asset | Wrong kind | Median seconds to detect | Worst | Incidents per fault |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `scn-cooling-fault` | 0.2 | 4 | 100% | 51% to 100% | 4 of 4 | 0 | 86 | 106 | 1.00 |
| `scn-cooling-fault` | 0.45 | 4 | 100% | 51% to 100% | 4 of 4 | 0 | 32 | 32 | 1.00 |
| `scn-cooling-fault` | 0.7 | 4 | 100% | 51% to 100% | 4 of 4 | 0 | 24 | 24 | 1.00 |
| `scn-cooling-fault` | 1.0 | 4 | 100% | 51% to 100% | 4 of 4 | 0 | 20 | 20 | 1.00 |
| `scn-hidden-leak` | 0.08 | 18 | 22% | 9% to 45% | 4 of 4 | 0 | 203 | 248 | 0.22 |
| `scn-hidden-leak` | 0.15 | 18 | 100% | 82% to 100% | 18 of 18 | 0 | 112 | 116 | 1.00 |
| `scn-hidden-leak` | 0.2 | 18 | 100% | 82% to 100% | 18 of 18 | 0 | 90 | 116 | 1.00 |
| `scn-hidden-leak` | 0.3 | 18 | 100% | 82% to 100% | 18 of 18 | 0 | 70 | 74 | 1.00 |
| `scn-load-shedding` | 1.0 | 8 | 100% | 68% to 100% | 8 of 8 | 0 | 6 | 6 | 1.00 |
| `scn-pipe-burst` | 0.15 | 18 | 100% | 82% to 100% | 18 of 18 | 0 | 28 | 30 | 1.00 |
| `scn-pipe-burst` | 0.35 | 18 | 100% | 82% to 100% | 18 of 18 | 0 | 18 | 20 | 1.00 |
| `scn-pipe-burst` | 0.7 | 18 | 100% | 82% to 100% | 18 of 18 | 0 | 14 | 14 | 1.00 |
| `scn-pipe-burst` | 1.0 | 18 | 100% | 82% to 100% | 18 of 18 | 0 | 12 | 12 | 1.00 |
| `scn-pump-failure` | 1.0 | 18 | 100% | 82% to 100% | 18 of 18 | 0 | 64 | 64 | 1.00 |
| `scn-stuck-sensor` | 1.0 | 18 | 100% | 82% to 100% | 18 of 18 | 0 | 24 | 24 | 1.00 |
| `scn-substation-trip` | 1.0 | 8 | 100% | 68% to 100% | 8 of 8 | 0 | 6 | 6 | 1.00 |

'Incidents per fault' above 1 means one injected fault opened more than one incident (the continuity problem in `docs/status.md`, known issue 1).

## False alarms

Exposure: 172.5 simulated hours without any fault (312 runs: extra clean runs and the controls of the fault runs), after a 240 second warm-up.

**False alarms: 0 incidents in 172.5 hours = 0.00 per 100 hours, 95% interval 0.00 to 2.14.** By kind: none.

Hard negatives (a demand surge, a holiday, a drifting sensor are not faults):

| Scenario | Runs | Raised a fault incident (leak, trip, pump, cooling) | Raised any incident | Kinds raised |
| --- | --- | --- | --- | --- |
| `scn-iftar-surge` | 12 | 0 (0%, up to 24%) | 2 | {'energy_waste': 4} |
| `scn-holiday` | 12 | 0 (0%, up to 24%) | 0 | - |
| `scn-sensor-drift` | 12 | 0 (0%, up to 24%) | 0 | - |

## Precision at a realistic fault rate

In the fault runs above most runs contain a fault, which flatters precision. In operation faults are rare, so precision is computed from the measured recall and the measured false-alarm rate at an assumed fault rate:

`precision = faults_per_100h x recall / (faults_per_100h x recall + false_alarms_per_100h)`

| Assumed fault rate | Expected true alarms per 100 h | False alarms per 100 h (measured, interval) | Precision |
| --- | --- | --- | --- |
| one fault a day | 3.89 | 0.00 (0.00 to 2.14) | **100%** (65% to 100%) |
| one fault a week | 0.56 | 0.00 (0.00 to 2.14) | **100%** (21% to 100%) |
| one fault a month | 0.13 | 0.00 (0.00 to 2.14) | **100%** (6% to 100%) |
