# Forecast: does more history help?

Measured 01 November 2026 with `scripts/stress/history_length.py` (seeds [42, 7, 123]). Every row is scored on the same last ten days, including the same two heat days and two holidays; only the number of days before them changes. Nothing was tuned on those days. The data is simulated, so this says how the method behaves on this twin, not how it would on equipment in the field.

## Water

| History (days) | Seed | Test MAPE | Test MAE | Skill vs best baseline | Band coverage (80% intended) | Error on heat and holiday test days | Verdict | Train time | Peak memory |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 75 | 7 | 4.82% | 170.62 | 19% | 81% | 8.68% | indicative | 26 s | 301 MB |
| 120 | 7 | 3.86% | 142.71 | 31% | 82% | 5.36% | reliable | 32 s | 390 MB |
| 180 | 7 | 3.46% | 127.21 | 40% | 89% | 4.46% | reliable | 38 s | 315 MB |
| 270 | 7 | 3.77% | 137.63 | 35% | 88% | 4.65% | reliable | 33 s | 464 MB |
| 365 | 7 | 3.77% | 138.90 | 34% | 86% | 4.63% | reliable | 37 s | 547 MB |
| 75 | 42 | 3.21% | 120.14 | 54% | 94% | 3.22% | reliable | 37 s | 268 MB |
| 120 | 42 | 3.03% | 112.44 | 57% | 97% | 2.33% | reliable | 44 s | 332 MB |
| 180 | 42 | 2.61% | 96.42 | 63% | 98% | 2.38% | reliable | 30 s | 487 MB |
| 270 | 42 | 2.45% | 91.87 | 65% | 95% | 1.71% | reliable | 33 s | 483 MB |
| 365 | 42 | 3.12% | 118.18 | 55% | 92% | 2.99% | reliable | 38 s | 527 MB |
| 75 | 123 | 3.97% | 150.80 | 48% | 75% | 2.70% | indicative | 25 s | 310 MB |
| 120 | 123 | 3.38% | 128.22 | 56% | 80% | 2.01% | indicative | 27 s | 390 MB |
| 180 | 123 | 3.14% | 123.32 | 58% | 76% | 2.96% | reliable | 29 s | 526 MB |
| 270 | 123 | 2.62% | 101.68 | 65% | 91% | 2.02% | reliable | 34 s | 528 MB |
| 365 | 123 | 2.54% | 99.41 | 66% | 86% | 1.83% | reliable | 37 s | 656 MB |

## Electricity

| History (days) | Seed | Test MAPE | Test MAE | Skill vs best baseline | Band coverage (80% intended) | Error on heat and holiday test days | Verdict | Train time | Peak memory |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 75 | 7 | 1.80% | 3.14 | 24% | 84% | 3.20% | indicative | 26 s | 301 MB |
| 120 | 7 | 1.45% | 2.58 | 39% | 81% | 1.96% | reliable | 32 s | 390 MB |
| 180 | 7 | 1.25% | 2.21 | 48% | 88% | 1.45% | reliable | 38 s | 315 MB |
| 270 | 7 | 1.41% | 2.50 | 39% | 86% | 1.62% | reliable | 33 s | 464 MB |
| 365 | 7 | 1.41% | 2.50 | 40% | 87% | 1.65% | reliable | 37 s | 547 MB |
| 75 | 42 | 1.19% | 2.12 | 56% | 93% | 1.14% | reliable | 37 s | 268 MB |
| 120 | 42 | 1.11% | 1.99 | 59% | 94% | 0.94% | reliable | 44 s | 332 MB |
| 180 | 42 | 1.00% | 1.78 | 63% | 94% | 0.95% | reliable | 30 s | 487 MB |
| 270 | 42 | 0.98% | 1.76 | 65% | 92% | 0.78% | reliable | 33 s | 483 MB |
| 365 | 42 | 1.21% | 2.18 | 56% | 87% | 1.24% | reliable | 38 s | 527 MB |
| 75 | 123 | 1.51% | 2.72 | 49% | 75% | 1.11% | reliable | 25 s | 310 MB |
| 120 | 123 | 1.31% | 2.36 | 55% | 80% | 0.85% | reliable | 27 s | 390 MB |
| 180 | 123 | 1.29% | 2.34 | 56% | 72% | 1.20% | reliable | 29 s | 526 MB |
| 270 | 123 | 0.96% | 1.74 | 68% | 88% | 0.66% | reliable | 34 s | 528 MB |
| 365 | 123 | 1.08% | 1.95 | 64% | 85% | 0.81% | reliable | 37 s | 656 MB |

## Cooling

| History (days) | Seed | Test MAPE | Test MAE | Skill vs best baseline | Band coverage (80% intended) | Error on heat and holiday test days | Verdict | Train time | Peak memory |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 75 | 7 | 6.17% | 2.25 | 66% | 75% | 9.56% | reliable | 26 s | 301 MB |
| 120 | 7 | 3.99% | 1.37 | 79% | 81% | 4.44% | reliable | 32 s | 390 MB |
| 180 | 7 | 3.92% | 1.35 | 79% | 74% | 3.49% | reliable | 38 s | 315 MB |
| 270 | 7 | 4.29% | 1.50 | 77% | 82% | 5.48% | reliable | 33 s | 464 MB |
| 365 | 7 | 4.49% | 1.61 | 76% | 84% | 6.28% | reliable | 37 s | 547 MB |
| 75 | 42 | 3.83% | 1.37 | 77% | 84% | 4.32% | reliable | 37 s | 268 MB |
| 120 | 42 | 4.05% | 1.44 | 76% | 80% | 4.91% | reliable | 44 s | 332 MB |
| 180 | 42 | 3.85% | 1.34 | 78% | 81% | 4.44% | reliable | 30 s | 487 MB |
| 270 | 42 | 3.20% | 1.14 | 81% | 84% | 3.04% | reliable | 33 s | 483 MB |
| 365 | 42 | 3.49% | 1.24 | 80% | 84% | 3.73% | reliable | 38 s | 527 MB |
| 75 | 123 | 4.47% | 1.80 | 74% | 78% | 4.51% | reliable | 25 s | 310 MB |
| 120 | 123 | 3.86% | 1.47 | 79% | 77% | 3.29% | reliable | 27 s | 390 MB |
| 180 | 123 | 3.57% | 1.31 | 81% | 76% | 3.19% | reliable | 29 s | 526 MB |
| 270 | 123 | 3.06% | 1.17 | 83% | 85% | 2.95% | reliable | 34 s | 528 MB |
| 365 | 123 | 3.28% | 1.25 | 82% | 84% | 3.29% | reliable | 37 s | 656 MB |
