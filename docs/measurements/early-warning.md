# Early warning: how much notice does it give, and does it cry wolf?

Measured 04 November 2026 with `backend/scripts/stress/earlywarn_matrix.py --write-evidence` (seeds 42 and 7; pumps P-03, P-01 and P-07; wear times of 3, 5, 8, 12 and 20 minutes, 30 runs; plus a power cut and 24 simulated hours with no fault). It runs the twin in process, with no server and no language model. The twin is simulated, and **in it every wear ends in a stop by design**, so these numbers show that the warning comes in time on this twin, not that a real pump would fail.

## Result

| What | Result |
| --- | --- |
| Wears of 5 minutes or more warned before the pump stopped | **24 of 24** |
| Notice before the stop (those 24 runs) | **12 s to 574 s, median 114 s** |
| Wears of 3 minutes warned before the stop | 2 of 6 (too fast for a warning that needs three readings in a row; stated, not hidden) |
| Warning later than the failure alarm | never |
| Assets warned that were not wearing (other pumps, transformers) | none |
| Two pumps wearing at once | both warned |
| A power cut that stops and restarts the pumps | no warning |
| 24 simulated hours without any fault | **0 warnings** |
| Risk score at the moment of the warning | **2.5% to 97.2%** |

## Notice by wear time (seconds before the pump stopped, six runs each)

| Wear | Notice | Median | Share of the wear still left when the warning came |
| --- | --- | --- | --- |
| 5 min | 12, 18, 18, 40, 42, 42 | 29 s | about 10% |
| 8 min | 36, 48, 100, 102, 102, 114 | 101 s | about 21% |
| 12 min | 98, 174, 206, 218, 266, 266 | 212 s | about 29% |
| 20 min | 180, 180, 212, 458, 494, 574 | 335 s | about 28% |

## What this says

- **The warning is timely, not early.** It opens when the pump's variability and deviation from the twin have moved far enough from its peers, which happens late in a fast wear and earlier in a slow one. For the default 8 minute wear the notice is 36 to 114 seconds. Earlier documents said "about four minutes"; that was one run (the default scenario on one pump) and is not what the matrix shows.
- **The level of the risk score does not say how close the stop is.** A young pump was warned at 12.8% and stopped 18 seconds later; an old one at 92%. The reading to trust is the gap to its peers, which is why the card shows the gap and why it explains it.
- **A fast failure (3 minutes) is mostly missed.** A failure with no precursor (`scn-pump-failure`) is not warned about, correctly.
- **The `minutesTo90` extrapolation was removed.** Against the real stop it said 14 minutes for a pump that stopped 18 seconds later, and "0.0" for pumps that still had 1 to 2 minutes. It is not in the payload any more.

## How the failure score reads

On the held-out quarter of the model's own synthetic history (1,500 assets) the score and the share of assets with a recorded failure agree within 4.6 points on average (Brier 0.154; 0.150 is the best any score can reach given that failures are drawn at random from the hazard). But a failure is recorded for 50% of the assets in that history and the history has no time window, so the **level** of the score is high by construction and is not the chance of failing within a stated time. `GET /why/warning:<id>` and `asset-risk:<id>` say this with the figures (`ml.failure_calibration`).

## Reproduce

```bash
cd backend && PYTHONPATH=. .venv/bin/python scripts/stress/earlywarn_matrix.py --write-evidence   # about 30 minutes, writes data/earlywarn_evidence.json
```

The warning card quotes `data/earlywarn_evidence.json`, so what it says about waiting is the measurement above and not a sentence typed by hand.
