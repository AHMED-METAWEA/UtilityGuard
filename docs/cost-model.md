# Cost model and input provenance

Reviewed 2026-10-29. Currency is EGP. These are deterministic planning calculations on a synthetic twin, not verified Egyptian prices or interruption-cost estimates. No tariff or budget is labelled official. `source` records default/user attribution; `status`, `basis`, `reviewed`, and `[low, high]` describe the underlying evidence. A user override does not verify its basis or move its uncertainty range. Finance and operations must validate the listed documents before using results for investment approval.

## Named incident formulas

- **Water loss per hour:** leaked m3/h times marginal water production cost. It represents physical production loss, not missed retail revenue.
- **Unserved energy per zone per hour:** total unserved MW times 1,000 kW/MW times the zone's affected accounts divided by all affected accounts, times that zone type's Value of Lost Load (VoLL). Customer shares are a planning allocation proxy until zone-level outage metering is available. Shares sum to one, so the same unserved energy is allocated once.
- **Utility disruption per zone:** affected service accounts times utility support cost per account-hour times outage hours. This buys notification and customer-support administration; it excludes customer economic harm. The hourly calculation shows one outage hour; the delayed-cost row applies the fault-specific total duration once. Full-zone affected counts are a planning upper estimate until disconnected-account data is available.
- **Primary wasted electricity per hour:** excess MW times 1,000 times purchased-energy tariff.
- **Independent cooling shortfall per hour:** undelivered refrigeration ton-hours per hour times marginal cooling cost. When unserved electricity is present, VoLL already owns the economic harm of losing dependent services, so a cooling shortfall on that same event is excluded.
- **Secondary purchased electricity per hour:** additional dependent-network MW times 1,000 times purchased-energy tariff. This input must represent extra purchases, excluding both unserved energy and primary-network excess purchases. The current detector estimates it from the synthetic twin; meter reconciliation is needed in production.
- **False-dispatch cost per hour:** cost per unnecessary dispatch divided by hours between such dispatches. Both are explicit planning assumptions. This owns visits caused by an unresolved sensor fault; the repair visit below restores the instrument.
- **Repair now:** one crew dispatch plus the applicable parts allowance; energy waste has only the crew allowance. **Repair later:** repair now times the fault-specific late-repair multiplier. The multiplier is a scenario allowance, not a fitted damage-growth law.
- **Hourly consequence C_delay:** sum of loss, disruption and secondary terms. **Response losses:** C_delay times response hours. **ActNow:** repair now plus response losses. **CostOfDelay:** C_delay times the incident's deferred response horizon plus repair later. **Saving:** CostOfDelay minus ActNow. Escalation is included once in the repair row, with no additional premium row.

Existing detector kinds `leak` and `sensor_fault` use policies `pipe_burst` and `stuck_sensor`. Each breakdown row has a unique owner (`group`) and distinct cause. Displayed delayed terms use cents; `costOfDelay` is the sum of those displayed terms, and saving is their sum minus the cent-rounded act-now cost. No intermediate input is truncated. Fractional-cent reconciliation can differ from the unrounded analytical saving by a few cents. Sanity checks use the analytical saving and only flag a review; they never clamp a result.

## Incident worked examples

All use one response hour and default registry values below. Unless specified, the affected zone is `zone-gov` with 500 institutional service accounts. Their administration rate is 5 EGP/account-hour, hence 2,500 EGP/h.

| Kind | Consequences per hour (hand calculation) | Repair now × escalation | Act now | Cost of delay | Saving |
| --- | --- | --- | ---: | ---: | ---: |
| pipe_burst | 140 × 8 water + 500 × 5 administration + 0.125 × 1000 × 1.6 secondary = 3820 | (3400 + 15000) × 1.7 = 31280 | 22220 | 3820 × 6 + 31280 = 54200 | 31980 |
| pump_failure | 500 × 5 = 2500 | (3400 + 42000) × 1.4 = 63560 | 47900 | 2500 × 8 + 63560 = 83560 | 35660 |
| substation_trip | 20000 × (500 × 45 + 300 × 75) / 800 VoLL + (500 × 5 + 300 × 8) administration + 250 × 1.6 secondary = 1130300 | (3400 + 26000) × 1.25 = 36750 | 1159700 | 1130300 × 4 + 36750 = 4557950 | 3398250 |
| cooling_fault | 1200 × 1.5 cooling + 500 × 5 administration = 4300 | (3400 + 58000) × 1.5 = 92100 | 65700 | 4300 × 10 + 92100 = 135100 | 69400 |
| stuck_sensor | 3400 / 4 = 850 | (3400 + 2400) × 1.1 = 6380 | 6650 | 850 × 12 + 6380 = 16580 | 9930 |
| energy_waste | 3 × 1000 × 1.6 = 4800 | 3400 × 1 = 3400 | 8200 | 4800 × 6 + 3400 = 32200 | 24000 |

The trip covers `zone-gov` and `zone-cult` (300 service accounts). Its 1,000-ton cooling deficit is deliberately excluded because the VoLL economic-harm owner already covers the electricity-caused downstream loss. Its 0.25 MW secondary input is separately purchased energy.

## Public reference check

Each input can carry public figures (publisher, link, date, note) and a verdict on how they sit against the registered range: `consistent`, `partly`, `differs`, `related` (context only) or `none`. The registered values are unchanged: a reference is evidence beside a value, and the assumptions panel offers a "Try" button for a single-figure reference so the effect can be seen before anyone adopts it. Searched on 2026-10-29; nothing was checked against an official gazette.

| Input | What was found | Verdict |
| --- | --- | --- |
| `elec_tariff` 1.6 EGP/kWh | Commercial tiers 1.62 to 2.79 (April 2026, Egypt Independent), industrial about 2.33 (blog), business 1.94 (aggregator, June 2025). Most are above the registered range 1.2 to 2.0. | partly |
| `discount_rate` 18% | Central Bank of Egypt deposit 19% and lending 20%. The registered rate is below the deposit rate, so finance should confirm it. | consistent with the range, with a warning in the note |
| `pm_n1` 1.0 | Theory 0.5 to 1.5; 107 South African zones measured -0.34 to 2.20. | consistent |
| `reset_pct_per_c` 2.5%/°C | Rule of thumb 1.8 to 3.6%/°C; DOE 0.9 to 1.35%/°C fixed-speed and 3.6 to 5.4%/°C variable-speed. | partly |
| `water_price` 8 EGP/m3 | Latest public production cost is 1.4 EGP/m3 from 2014; tariffs from 2017. No 2026 figure. | related |
| `cooling_price`, `voll_*` | No public price per ton-hour; no Egypt-specific value of lost load. | none, with a search note |
| `crew_callout`, `false_dispatch` | Minimum wage 8,000 EGP/month from July 2026 (private-sector coverage disputed); diesel about 20.5 EGP/L. Context for the budget, not a price for it. | related |

## Savings advisor formulas

The advisor uses hourly synthetic baseline history without injected incidents. Sum hourly power as energy (one-hour samples), then normalize to a 30-day month using `30 / (history_hours / 24)`. `1 MW = 1000 kW`; thermal demand uses `284.3 TR/MW`, an engineering unit conversion rounded from approximately 3.517 kW/TR.

- **After-hours setback:** monthly off-hours government cooling ton-hours times setback reduction times cooling cost. Office hours are Sunday–Thursday 08:00–16:00 Cairo time; other hours are off-hours. These occupancy hours need BMS/occupancy verification.
- **Night pressure management:** estimate background leakage L0 as night production Q times `background_loss_ratio / (1 + background_loss_ratio)`. FAVAD approximates L1 = L0 × (target pressure / current pressure)^N1. Saving is max(0, L0 − L1) times six night hours times 30 days times water production cost. The leakage ratio is relative to billed demand; it is not a measured leakage fraction of production.
- **Supply temperature reset:** monthly chiller kWh times fraction of hours below 35°C times energy-saving fraction per degree times temperature raise times electricity tariff. Using a fraction of hours assumes uniform eligible energy; use hourly energy weighting when real metering is available. The 35°C eligibility threshold needs OEM and comfort commissioning verification.
- **Condenser cleaning:** (worst plant kW/TR − best plant kW/TR) times worst-plant monthly delivered TR-hours times electricity tariff. Only gaps above 0.01 kW/TR generate an opportunity. The best plant is a planning clean reference; load mix, weather and equipment differences are not corrected. No improvement is guaranteed by that comparison alone.
- **Planned maintenance:** 30-day failure probability times fault-specific CostOfDelay minus planned repair expense, where planned expense = emergency repair-now crew/parts times planned_factor. Each future-failure proxy (leak rate, lost-load fraction or cooling fraction) is in the registry. Only positive opportunities among the three greatest net benefits are returned. Cooling maintenance currently covers the first two served zones; this scope must be checked against isolation plans. This is a recurring monthly expected net benefit with the planned expense already subtracted each month; there is no separate upfront investment. Repeating its risk and planned cost for three years is an explicit planning approximation, not a one-off replacement model.
- **Upfront investment:** the registered commissioning/hardware/cleaning budget, or zero for recurring maintenance. **Monthly net saving:** max(0, the named opportunity formula). **Simple payback months:** upfront investment divided by unrounded monthly net saving; days = months × 30. With no positive saving, payback fields are omitted (null in the Python model); with zero investment and positive benefit, payback is zero.
- **Three-year NPV:** sum the same monthly net saving at each month end for months 1 through 36, discounted by `(1 + annual_discount_rate)^(month / 12)`, then subtract upfront investment once. At 18% effective annual discount, monthly rate is 0.01388843034840992 and the 36-month annuity factor is 28.17950751112164. No inflation, tax, terminal value, financing or additional operating costs are assumed.
- **Report totals:** totalMonthly is the sum of displayed opportunity monthly savings; totalInvestment sums their displayed investments. The dashboard savings KPI uses this same total. These standalone estimates assume additive interventions; combined cooling interventions need an integrated baseline before finance should approve the sum.

## Savings worked examples

The test baseline is one Sunday (2026-10-25) with 24 constant hourly samples in Cairo time, normalized to 30 days. Government cooling is 10 TR/h in each of its three zones, hence 30 × 16 off-hours × 30 = 14,400 monthly off-hours ton-hours. Aggregate night water production is 1,115 m3/h with 4.25 bar at every pump. Chiller electric demand is 2 MW with ambient 30°C all day. Both reference plants deliver 100 TR; plant 01 uses 0.09 MW and plant 02 uses 0.07 MW. For maintenance only the nominated asset has a failure forecast (0.8 per 30 days); each zone's electrical demand is 10 MW and plant 01's live thermal delivery metric is 10 MW.

| Opportunity | Hand calculation of monthly saving | Investment | Payback months | NPV at 18%, 36 months |
| --- | --- | ---: | ---: | ---: |
| setback | 14400 × 0.35 × 1.5 = 7560 | 25000 | 3.3069 | 188037.08 |
| pressure | L0 = 1115 × 0.115 / 1.115 = 115; L1 = 115 × (3.4 / 4.25)^1 = 92; (115 − 92) × 6 × 30 × 8 = 33120 | 140000 | 4.2271 | 793305.29 |
| reset | 2 × 1000 × 24 × 30 × 1 × 0.025 × 1 × 1.6 = 57600 | 10000 | 0.1736 | 1613139.63 |
| cleaning | ((0.09 × 1000 / 100) − (0.07 × 1000 / 100)) × (100 × 24 × 30) × 1.6 = 23040 | 250000 | 10.8507 | 399255.85 |
| maintenance pump | 0.8 × 83560 − 45400 × 0.6 = 39608 | 0 | 0 | 1116133.93 |
| maintenance pipe | 0.8 × ((100 × 8 + 2500) × 6 + 18400 × 1.7) − 18400 × 0.6 = 29824 | 0 | 0 | 840425.63 |
| maintenance substation | unserved = (10 + 10) × 0.45 = 9 MW; hourly = 9000 × 56.25 + 4900 = 511150; 0.8 × (511150 × 4 + 29400 × 1.25) − 29400 × 0.6 = 1647440 | 0 | 0 | 46424047.85 |
| maintenance chiller | deficit = 10 × 0.5 × 284.3 = 1421.5 TR; hourly = 1421.5 × 1.5 + (500 + 200) × 5 = 5632.25; 0.8 × (5632.25 × 10 + 61400 × 1.5) − 61400 × 0.6 = 81898 | 0 | 0 | 2307845.31 |

For each row payback is investment/monthly saving; NPV is monthly saving × 28.17950751112164 − investment. The substation maintenance example uses SS-02 (government and culture); chiller maintenance uses plant 01 (government and presidential scope). All eligible maintenance asset types are checked separately so top-three ranking does not hide an example.

## Decision Studio

`GET /api/v1/incidents/{incidentId}/decision` compares responses using the existing incident cost functions. It recovers the unrounded facts, affected zone types and assumption values from the finding's calculation snapshot, rather than the latest detector readings. Thus `fix_now.currentCost` matches the incident's displayed `Cost.actNow`, even if live prices or measurements have since changed. Missing incidents or matching findings return 404 `not_found`.

Let `L = C_loss`, `D = C_disruption`, `S = C_secondary`, `C = L + D + S`, `R = repair_now`, `r = response_hours`, `h = delay_hours_{kind}`, `e = late_multiplier_{kind}`, `p = planned_factor`, `q = isolate_loss_reduction`, and `K = crew_callout`. Costs are EGP and hourly consequences are EGP/h.

- `fix_now = R + C × r`.
- For pipe bursts only, `isolate_fix = C × r + [L × (1 − q) + D + S] × (h − r) + p × R + K`. Here the loss group is lost water. Isolation at crew arrival reduces only that water loss; utility disruption and secondary purchases continue for all `h` hours. The existing horizon is assumed to follow arrival. The un-escalated planned repair includes its own crew and parts, with one additional call-out for the isolation visit. `isolate_loss_reduction = 0.85 [0.70, 0.95]` is a planning allowance, not measured valve performance.
- `wait_w = C × w + R × m(w)` for `w = 6, 24, 72` hours, where `m(w) = min(late_multiplier_cap, 1 + (e − 1) × w / h)`. Waiting losses run to `w` without extra response hours. Linear escalation is a planning choice, not a fitted damage-growth law. The planning cap is `3.0 [2.0, 4.0]`. At each kind's own horizon, `m(h) = e` and waiting equals the existing `Cost.costOfDelay`; all registered multipliers are below the cap's lower bound. An override placing the cap below `e` intentionally changes that equality by capping the repair cost.
- A non-pipe wait option ties `fix_now` at hourly consequence rate `C* = R × (1 − m(w)) / (w − r)` in EGP/h. For pipe bursts, hold non-water hourly costs `B` fixed and solve for leak rate: `Q* = [R × (1 − m(w)) − B × (w − r)] / [water_price × (w − r)]` in m3/h. Each `breakEven` row returns its signed algebraic crossing, formula and input provenance. A negative crossing has no feasible non-negative rate. If the denominator is zero, the rate is absent: every rate ties when the numerator is zero, otherwise there is no finite crossing.

Decision Studio reuses `Assumptions.sampled` for 800 draws, uniform independently within each used assumption's registered `[low, high]`. The seed is `CRC32(incidentId) XOR seed` (runtime seed for the endpoint, default 42 for direct builds). Every option uses the same draw in each row. Facts stay fixed: this comparison samples assumption prices and planning parameters, with no extra leak measurement draw. `expectedCost` is the mean; `p10`, `p50` and `p90` are sample percentiles. Worst case means sampled p90, never a maximum. `expectedRegret(o) = mean_s(total_s(o) − min_j total_s(j))`, pairing the alternatives within every draw; subtracting the minimum option mean would incorrectly give the best mean option zero regret. `winShare` is a fraction, described as “cheapest in X% of sampled price scenarios”; exact sampled ties count for each cheapest option. It is not the probability of a real event.

`recommendedOptionId` minimizes the unrounded sampled mean, with null for an exact tie. `netBenefitVsFixNow = expectedCost(option) − expectedCost(fix_now)` retains its sign: positive is cost avoided by fixing now, negative means that option is cheaper. For example, a cheap energy-waste fault with 0.001 MW excess, tariff 1.6, crew cost 3400 and an eight-hour response costs 3412.80 to fix now versus 3409.60 to wait six hours (no repair escalation). The signed difference is −3.20 EGP. With an eight-hour response range this also remains negative in the sampled comparison; merely overriding a value does not move the registry range.

`costLines` use the existing `Calculation` structure (`name`, `formula`, `inputs` with value/unit/basis, `result`, `unit`) at current assumptions, with a separate `currentCost`. These lines explain the current incident numbers; `expectedCost` summarizes the sampled scenarios and can differ. Line results use cents; at the existing delay horizon their sum matches `Cost.costOfDelay` including its rounding convention. `fix_now` includes a cent reconciliation line when needed to match the existing rounded `actNow`. Sampled calculations retain full precision until reporting. All used assumptions, including review limits, retain their full registry metadata. `review` compares the recommended expected cost with the existing inclusive `saving_min_{kind}` and `saving_max_{kind}` bounds without clamping it; `reviewReason` explicitly notes that those existing thresholds were calibrated for savings.

The decision map ranks assumptions with the existing tornado machinery applied to the paired mean cost gap between `fix_now` and the wait option with the lowest sampled mean. It selects the two largest endpoint swings, breaking equal swings by key and filling from remaining ranged inputs if needed. It evaluates a deterministic 9 × 9 grid with all other assumptions at current values. Axes span their registered ranges; the nearest interior point is replaced with the exact current value when it lies within the range, retaining nine points and both endpoints. `cells` are rows of y and columns of x, with null on an exact cost tie. `currentOptionId` is the cheapest at current values and equals that grid cell when both current values are in range. This deterministic recommendation can differ from the sampled-mean `recommendedOptionId`; an override outside its registered range places the current marker outside the grid.

### Decision Studio worked example

The pipe burst used in `backend/tests/test_decision.py` has 140 m3/h leaked water, 500 government accounts and 0.125 MW secondary electricity. With registry defaults, `L = 140 × 8 = 1120`, `D = 500 × 5 = 2500`, `S = 125 × 1.6 = 200`, `C = 3820`, and `R = 3400 + 15000 = 18400`. Arrival is at one hour and planned repair at six hours.

| Option | Current-assumption calculation | Total EGP |
| --- | --- | ---: |
| fix_now | 18400 + 3820 × 1 | 22220 |
| isolate_fix | 3820 × 1 + (1120 × 0.15 + 2500 + 200) × 5 + 18400 × 0.6 + 3400 | 32600 |
| wait_6h | 3820 × 6 + 18400 × 1.7 | 54200 |
| wait_24h | 3820 × 24 + 18400 × 3 | 146880 |
| wait_72h | 3820 × 72 + 18400 × 3 | 330240 |

Isolation's named lines are water 1960, administration 15000, secondary purchases 1200, planned repair 11040 and extra call-out 3400 EGP. Fixing now has the lowest current cost and sampled mean for this example. The six-hour wait leak break-even is `[18400 × (1 − 1.7) − 2700 × (6 − 1)] / [8 × (6 − 1)] = −659.5 m3/h`, so there is no non-negative crossing. The other two leak crossings are −537.5 and approximately −402.29 m3/h.

## Uncertainty, review and comparison

**Monte Carlo:** incident P10/P50/P90 are percentile values of 800 reruns, seeded by CRC32 of incident id; savings uses 600 joint reruns seeded by CRC32("savings") XOR twin seed. Each used assumption is sampled uniformly at its absolute registry low/high endpoints, independently of other keys. Same-key draws are shared between opportunities. Incident leak measurement also receives uniform ±15% error, a planning allowance pending paired flow-meter calibration certificates. Percentiles are scenario summaries, not statistically calibrated confidence intervals. An override outside a default range can therefore lie outside the reported scenario range.

**Tornado:** hold measured facts and every other assumption fixed, and recompute the actual saving function with one assumption at low and high. Include only assumptions with different endpoint savings; sort by descending absolute difference, then key for ties. For the pipe example, water_price 6 gives 31,980 − 140 × (8 − 6) × (6 − 1) = 30,580 and price 10 gives 33,380 (swing 2,800). Late repair multiplier 1.5 gives 28,300; 1.9 gives 35,660. Fixed review limits do not affect saving and are excluded.

**Legacy sensitivity:** independently rerun the main price/repair key, fault-specific delay and response hours at ±20% of their current values. This compatibility view differs from the registered-range tornado.

**Sanity bounds:** compare analytical saving to the inclusive fault-specific min/max registry limits. Outside the interval set review=true and reviewReason, preserving the amount. These thresholds are planning budgets rather than physical feasibility proofs.

**Scenario comparison:** the no-action side uses the incident CostOfDelay and its per-kind horizon; intervention uses ActNow, already including response losses once. Water lost is leak rate times the applicable duration. Compare carries the same cost explanation, so both totals and saving resolve to the named incident formulas. The 60 simulation ticks display a compressed clock with horizon/60 hours per tick; that presentation does not change cost arithmetic.

## Example verification data

The following independent expected numbers are part of this document. `backend/tests/test_cost_provenance.py` and `test_savings.py` parse them and recompute the real cost functions, opportunity builders, paybacks and NPVs. The human tables above are also checked against these expected figures.

```json cost-examples
{
  "incidents": [
    {"kind": "pipe_burst", "zones": {"zone-gov": "government"}, "facts": {"leak_m3h": 140, "secondary_mw": 0.125}, "hourly": 3820, "act": 22220, "delay": 54200, "saving": 31980},
    {"kind": "pump_failure", "zones": {"zone-gov": "government"}, "facts": {}, "hourly": 2500, "act": 47900, "delay": 83560, "saving": 35660},
    {"kind": "substation_trip", "zones": {"zone-gov": "government", "zone-cult": "services"}, "facts": {"unserved_mw": 20, "secondary_mw": 0.25, "cooling_deficit_tons": 1000}, "hourly": 1130300, "act": 1159700, "delay": 4557950, "saving": 3398250},
    {"kind": "cooling_fault", "zones": {"zone-gov": "government"}, "facts": {"cooling_deficit_tons": 1200}, "hourly": 4300, "act": 65700, "delay": 135100, "saving": 69400},
    {"kind": "stuck_sensor", "zones": {"zone-gov": "government"}, "facts": {}, "hourly": 850, "act": 6650, "delay": 16580, "saving": 9930},
    {"kind": "energy_waste", "zones": {"zone-gov": "government"}, "facts": {"excess_mw": 3}, "hourly": 4800, "act": 8200, "delay": 32200, "saving": 24000}
  ],
  "opportunities": [
    {"id": "opp-setback", "monthly": 7560, "investment": 25000, "payback": 3.3069, "npv": 188037.08},
    {"id": "opp-night-pressure", "monthly": 33120, "investment": 140000, "payback": 4.2271, "npv": 793305.29},
    {"id": "opp-supply-reset", "monthly": 57600, "investment": 10000, "payback": 0.1736, "npv": 1613139.63},
    {"id": "opp-clean-cool-plant-01", "monthly": 23040, "investment": 250000, "payback": 10.8507, "npv": 399255.85},
    {"id": "opp-pm-water-pump-01", "monthly": 39608, "investment": 0, "payback": 0, "npv": 1116133.93},
    {"id": "opp-pm-water-pipe-01", "monthly": 29824, "investment": 0, "payback": 0, "npv": 840425.63},
    {"id": "opp-pm-elec-sub-02", "monthly": 1647440, "investment": 0, "payback": 0, "npv": 46424047.85},
    {"id": "opp-pm-cool-plant-01", "monthly": 81898, "investment": 0, "payback": 0, "npv": 2307845.31}
  ]
}
```

## Non-registry input bases

Measured denotes synthetic twin data here; it never implies a real meter has been read. The baseline in these examples is deliberately simple for arithmetic verification.

| Input | Example value | Unit | Status | Basis and operational verification |
| --- | --- | --- | --- | --- |
| Pipe leak | 140 | m3/h | planning | Hand scenario; in the app, paired synthetic flow difference; verify real inlet/outlet calibration. |
| Trip unserved load | 20 | MW | planning | Hand scenario; in the app, synthetic outage estimate; verify backup transfer and feeder meters. |
| Secondary electricity | 0.125 pipe; 0.25 trip | MW | planning | Hand scenario of additional purchases; verify dependent-network meters exclude primary consumption and unserved demand. |
| Cooling deficit | 1200 cooling; 1000 trip | TR | planning | Hand scenario; in the app, synthetic delivered-minus-required estimate; verify thermal meters. |
| Wasted electricity | 3 | MW | planning | Hand scenario; verify actual-versus-forecast meter baseline. |
| Government off-hours cooling | 14400 | ton-h/month | planning | 30 TR × 16 h × 30 days in the controlled Sunday history; verify occupancy and cooling meters. |
| Night production | 1115 | m3/h | planning | Synthetic constant test baseline; verify district production/billing reconciliation. |
| Night pressure | 4.25 | bar | planning | Synthetic constant pump readings; verify pressure logger calibration and remote-customer pressure. |
| Chiller energy | 1440000 | kWh/month | planning | 2 MW × 1000 × 24 × 30; verify chiller submeters. |
| Mild share | 1 | fraction | planning | All example hours are 30°C, below the planning 35°C threshold; verify weather and comfort trials. |
| Plant efficiency | 0.9 worst; 0.7 best | kW/TR | planning | 90 or 70 kW / 100 TR; verify matched plant operation and clean-reference validity. |
| Worst plant delivery | 72000 | TR-h/month | planning | 100 TR × 24 × 30; verify thermal delivery meters. |
| Failure probability | 0.8 | per 30 days | planning | Controlled forecast fixture; real use requires horizon/calibration validation against failure records. |
| Future trip demand | 10 per zone | MW | planning | Controlled current demand; verify live feeder metering and contingency allocation. |
| Future chiller delivery | 10 | MW thermal | planning | Controlled current plant metric; verify plant duty logs (fallback also registered). |
| Office schedule | 8 to 16, Sun–Thu | Cairo local hours | planning | Synthetic occupancy schedule; verify actual BMS/occupancy schedule. |
| Pressure-control window | 0 to 6 | Cairo local hours | planning | Six-hour planning night window; verify pressure-management operating procedure. |
| Appraisal month/horizon | 30 days; 36 months | duration | planning | Constant month and three-year appraisal convention, to confirm with finance. |
| Unit conversions | 1000; 284.3 | kW/MW; TR/MW thermal | engineering | SI power conversion; rounded refrigeration-ton conversion from approximately 3.517 kW/TR. |
| Leak measurement error | ±15% | fraction | planning | Illustrative paired-flow error; verify against meter calibration certificates. |
| Monte Carlo runs | 800 incident; 600 advisor | runs | engineering | Reproducibility/sample-count choices; not fitted evidence. |

## Assumption input table

Every registry default used in these examples or the application is listed below with its status, basis and uncertainty bounds. The test also checks this table against DEFAULTS so it cannot silently drift.

| Key | Value | Unit | Status | Low | High | Basis |
| --- | ---: | --- | --- | ---: | ---: | --- |
| water_price | 8 | EGP/m3 | planning | 6 | 10 | Illustrative marginal treatment and delivery cost, not a verified Egyptian tariff. Verify against the utility's water production cost ledger and supply contract. |
| elec_tariff | 1.6 | EGP/kWh | planning | 1.2 | 2 | Illustrative purchased-energy rate; Egyptian tariffs cannot be verified offline. Verify against the current electricity supply contract and invoices. |
| cooling_price | 1.5 | EGP/ton-h | planning | 1.1 | 1.9 | Illustrative marginal cooling delivery cost. Verify against the district cooling operating cost ledger, avoiding retail revenue and energy-cost overlap. |
| crew_callout | 3400 | EGP/dispatch | planning | 2500 | 4300 | Budget allowance for transport and emergency labour per dispatch. Verify against crew timesheets and the maintenance contractor rate card. |
| parts_pipe | 15000 | EGP/repair | planning | 10000 | 20000 | Budget for one district-main joint/section repair. Verify against the pipe bill of quantities and recent supplier quotations. |
| parts_pump | 42000 | EGP/repair | planning | 30000 | 54000 | Budget for a pump mechanical/electrical repair. Verify against the pump asset bill of materials and maintenance work orders. |
| parts_substation | 26000 | EGP/repair | planning | 18000 | 34000 | Budget for reset, inspection and minor switchgear work, not transformer replacement. Verify against substation restoration work orders. |
| parts_chiller | 58000 | EGP/repair | planning | 40000 | 76000 | Budget for a minor chiller component repair. Verify against OEM service quotations and plant work orders. |
| parts_sensor | 2400 | EGP/repair | planning | 1600 | 3200 | Budget for a field sensor replacement or calibration. Verify against the instrument register and calibration contractor rate card. |
| response_hours | 1 | h | planning | 0.5 | 1.5 | One hour is a district dispatch-to-isolation planning allowance. Verify against dispatch logs and the emergency response SLA. |
| isolate_loss_reduction | 0.85 | fraction | planning | 0.7 | 0.95 | Planning allowance: isolation cuts lost-water cost only by 85% after crew arrival until the existing deferred horizon; disruption and secondary purchases continue unchanged. Repair uses planned_factor without damage escalation, plus one extra isolation call-out. Verify against valve isolation plans, post-isolation flows and planned work orders. |
| late_multiplier_cap | 3 | × | planning | 2 | 4 | Linear escalation m(w) = min(cap, 1 + (late_multiplier_kind - 1) * w / delay_hours_kind) is a planning choice, with a default cap of three times repair cost. Waiting includes losses to w with no additional response hours. This is not a fitted damage-growth law; verify against matched early and deferred repair work orders. |
| false_dispatch | 3400 | EGP/dispatch | planning | 2500 | 4300 | One unnecessary visit uses the same budget as an emergency crew dispatch. Verify against false-alarm work orders and contractor invoices. |
| false_dispatch_interval | 4 | h/dispatch | planning | 2 | 6 | One false dispatch per four unresolved sensor-fault hours is an illustrative frequency, not observed evidence. Verify against alarm and dispatch history. |
| setback_reduction | 0.35 | fraction | planning | 0.25 | 0.45 | A 35% off-hours reduction is a commissioning target. Verify with occupancy schedules, BMS trends and a setback measurement-and-verification trial. |
| setback_invest | 25000 | EGP | planning | 18000 | 32000 | Budget for BMS scheduling and commissioning. Verify against the controls contractor scope and quotation. |
| pm_target_pressure | 3.4 | bar | planning | 3 | 3.8 | Trial target chosen below normal night pressure while retaining service head. Verify against the hydraulic model and minimum customer pressure SLA. |
| pm_n1 | 1 | × | benchmark | 0.5 | 1.5 | The FAVAD power-law approximation uses a midpoint exponent of one. Calibrate the 0.5–1.5 benchmark range with district pressure-step tests and the leakage study. |
| pm_invest | 140000 | EGP | planning | 100000 | 180000 | Budget for pressure-control hardware and installation. Verify against the valve schedule and supplier installation quotations. |
| reset_delta_c | 1 | °C | planning | 0.5 | 1.5 | One degree is a trial control reset. Verify against OEM operating limits and building comfort commissioning records. |
| reset_pct_per_c | 0.025 | fraction/°C | benchmark | 0.02 | 0.03 | A 2–3% energy reduction per degree is an illustrative engineering benchmark, not a physical law. Verify against OEM performance curves and a metered reset trial. |
| reset_invest | 10000 | EGP | planning | 7000 | 13000 | Budget for controls programming and commissioning. Verify against the BMS change quotation. |
| clean_invest | 250000 | EGP | planning | 180000 | 320000 | Budget for one plant cleaning intervention. Verify against the condenser service scope and contractor quotation. |
| planned_factor | 0.6 | × | planning | 0.45 | 0.75 | Planned work is budgeted at 60% of emergency crew-plus-parts cost. Verify against matched planned and emergency maintenance work orders. |
| discount_rate | 0.18 | fraction/year | planning | 0.12 | 0.24 | Planning rate for investment appraisal; verify with finance against the approved capital-budget discount-rate memorandum. |
| background_loss_ratio | 0.115 | fraction | planning | 0.085 | 0.145 | Synthetic baseline leakage is 11.5% of billed demand; this is not a measured district water balance. Verify against minimum-night-flow tests and production/billing meter reconciliation. |
| maintenance_leak_m3h | 100 | m3/h | planning | 75 | 125 | A 100 m3/h leak represents an illustrative future pipe failure. Verify against pipe diameter, hydraulic breach modelling and historical burst work orders. |
| maintenance_unserved_fraction | 0.45 | fraction | planning | 0.3 | 0.6 | Assume 45% of current zone electrical demand remains unserved after backup transfer. Verify against the substation contingency and protection study. |
| maintenance_cooling_fraction | 0.5 | fraction | planning | 0.35 | 0.65 | Assume half of plant thermal delivery is lost in a future failure. Verify against the plant redundancy and chiller availability study. |
| maintenance_cooling_fallback_mw | 10 | MW | planning | 7.5 | 12.5 | Use 10 MW thermal only when the live plant metric is unavailable. Verify against plant duty records and the rated-load schedule. |
| customer_hour_government | 5 | EGP/customer-h | planning | 2.5 | 7.5 | Illustrative government account-hour budget for notification and utility customer support only; excludes customer economic harm covered by VoLL. Verify against the utility support cost ledger and outage notification work orders. |
| voll_government | 45 | EGP/kWh | planning | 22.5 | 67.5 | Illustrative government customer economic outage value, including lost downstream services; not an official Egyptian VoLL. Verify against a local customer interruption-cost survey and the regulator's approved VoLL study, if available. |
| customer_hour_business | 2.5 | EGP/customer-h | planning | 1.25 | 3.75 | Illustrative business account-hour budget for notification and utility customer support only; excludes customer economic harm covered by VoLL. Verify against the utility support cost ledger and outage notification work orders. |
| voll_business | 60 | EGP/kWh | planning | 30 | 90 | Illustrative business customer economic outage value, including lost downstream services; not an official Egyptian VoLL. Verify against a local customer interruption-cost survey and the regulator's approved VoLL study, if available. |
| customer_hour_residential | 0.15 | EGP/customer-h | planning | 0.075 | 0.225 | Illustrative residential account-hour budget for notification and utility customer support only; excludes customer economic harm covered by VoLL. Verify against the utility support cost ledger and outage notification work orders. |
| voll_residential | 20 | EGP/kWh | planning | 10 | 30 | Illustrative residential customer economic outage value, including lost downstream services; not an official Egyptian VoLL. Verify against a local customer interruption-cost survey and the regulator's approved VoLL study, if available. |
| customer_hour_services | 8 | EGP/customer-h | planning | 4 | 12 | Illustrative services account-hour budget for notification and utility customer support only; excludes customer economic harm covered by VoLL. Verify against the utility support cost ledger and outage notification work orders. |
| voll_services | 75 | EGP/kWh | planning | 37.5 | 112.5 | Illustrative services customer economic outage value, including lost downstream services; not an official Egyptian VoLL. Verify against a local customer interruption-cost survey and the regulator's approved VoLL study, if available. |
| customers_zone_gov | 500 | customers | planning | 375 | 625 | Planning service-account count scaled to the district demand in ZONE_BASE; residential accounts are households, institutional accounts are service connections, not people. Verify against the district occupancy and utility connection registers. |
| customers_zone_cult | 300 | customers | planning | 225 | 375 | Planning service-account count scaled to the district demand in ZONE_BASE; residential accounts are households, institutional accounts are service connections, not people. Verify against the district occupancy and utility connection registers. |
| customers_zone_cbd | 1200 | customers | planning | 900 | 1500 | Planning service-account count scaled to the district demand in ZONE_BASE; residential accounts are households, institutional accounts are service connections, not people. Verify against the district occupancy and utility connection registers. |
| customers_zone_res_2 | 8000 | customers | planning | 6000 | 10000 | Planning service-account count scaled to the district demand in ZONE_BASE; residential accounts are households, institutional accounts are service connections, not people. Verify against the district occupancy and utility connection registers. |
| customers_zone_just | 250 | customers | planning | 187.5 | 312.5 | Planning service-account count scaled to the district demand in ZONE_BASE; residential accounts are households, institutional accounts are service connections, not people. Verify against the district occupancy and utility connection registers. |
| customers_zone_pres | 200 | customers | planning | 150 | 250 | Planning service-account count scaled to the district demand in ZONE_BASE; residential accounts are households, institutional accounts are service connections, not people. Verify against the district occupancy and utility connection registers. |
| customers_zone_res_n | 7000 | customers | planning | 5250 | 8750 | Planning service-account count scaled to the district demand in ZONE_BASE; residential accounts are households, institutional accounts are service connections, not people. Verify against the district occupancy and utility connection registers. |
| customers_zone_res_ne | 6000 | customers | planning | 4500 | 7500 | Planning service-account count scaled to the district demand in ZONE_BASE; residential accounts are households, institutional accounts are service connections, not people. Verify against the district occupancy and utility connection registers. |
| customers_zone_res_far | 5500 | customers | planning | 4125 | 6875 | Planning service-account count scaled to the district demand in ZONE_BASE; residential accounts are households, institutional accounts are service connections, not people. Verify against the district occupancy and utility connection registers. |
| delay_hours_pipe_burst | 6 | h | planning | 4.5 | 7.5 | Continuing erosion can enlarge excavation and pipe damage; this horizon is a planning scenario. Verify against fault-specific restoration logs and the response SLA. |
| late_multiplier_pipe_burst | 1.7 | × | planning | 1.5 | 1.9 | Continuing erosion can enlarge excavation and pipe damage; the multiplier is a planning allowance, not an estimated growth law. Verify against matched early and deferred repair work orders. |
| saving_min_pipe_burst | 0 | EGP | planning | 0 | 0 | Planning review threshold for a single district incident, not a cap on the computed value. Verify against the asset replacement budget and historical incident-loss register. |
| saving_max_pipe_burst | 2e+06 | EGP | planning | 2e+06 | 2e+06 | Planning review threshold for a single district incident, not a cap on the computed value. Verify against the asset replacement budget and historical incident-loss register. |
| delay_hours_pump_failure | 8 | h | planning | 6 | 10 | An idle failed pump requires slower mechanical restoration; this horizon is a planning scenario. Verify against fault-specific restoration logs and the response SLA. |
| late_multiplier_pump_failure | 1.4 | × | planning | 1.2 | 1.6 | An idle failed pump requires slower mechanical restoration; the multiplier is a planning allowance, not an estimated growth law. Verify against matched early and deferred repair work orders. |
| saving_min_pump_failure | 0 | EGP | planning | 0 | 0 | Planning review threshold for a single district incident, not a cap on the computed value. Verify against the asset replacement budget and historical incident-loss register. |
| saving_max_pump_failure | 500000 | EGP | planning | 500000 | 500000 | Planning review threshold for a single district incident, not a cap on the computed value. Verify against the asset replacement budget and historical incident-loss register. |
| delay_hours_substation_trip | 4 | h | planning | 3 | 5 | A protection trip needs prompt inspection with modest assumed additional damage; this horizon is a planning scenario. Verify against fault-specific restoration logs and the response SLA. |
| late_multiplier_substation_trip | 1.25 | × | planning | 1.05 | 1.45 | A protection trip needs prompt inspection with modest assumed additional damage; the multiplier is a planning allowance, not an estimated growth law. Verify against matched early and deferred repair work orders. |
| saving_min_substation_trip | 0 | EGP | planning | 0 | 0 | Planning review threshold for a single district incident, not a cap on the computed value. Verify against the asset replacement budget and historical incident-loss register. |
| saving_max_substation_trip | 5e+07 | EGP | planning | 5e+07 | 5e+07 | Planning review threshold for a single district incident, not a cap on the computed value. Verify against the asset replacement budget and historical incident-loss register. |
| delay_hours_cooling_fault | 10 | h | planning | 7.5 | 12.5 | A deferred chiller repair risks additional component damage over a longer service window; this horizon is a planning scenario. Verify against fault-specific restoration logs and the response SLA. |
| late_multiplier_cooling_fault | 1.5 | × | planning | 1.3 | 1.7 | A deferred chiller repair risks additional component damage over a longer service window; the multiplier is a planning allowance, not an estimated growth law. Verify against matched early and deferred repair work orders. |
| saving_min_cooling_fault | 0 | EGP | planning | 0 | 0 | Planning review threshold for a single district incident, not a cap on the computed value. Verify against the asset replacement budget and historical incident-loss register. |
| saving_max_cooling_fault | 5e+06 | EGP | planning | 5e+06 | 5e+06 | Planning review threshold for a single district incident, not a cap on the computed value. Verify against the asset replacement budget and historical incident-loss register. |
| delay_hours_stuck_sensor | 12 | h | planning | 9 | 15 | An instrument fault can persist through a shift with little additional physical damage; this horizon is a planning scenario. Verify against fault-specific restoration logs and the response SLA. |
| late_multiplier_stuck_sensor | 1.1 | × | planning | 1 | 1.3 | An instrument fault can persist through a shift with little additional physical damage; the multiplier is a planning allowance, not an estimated growth law. Verify against matched early and deferred repair work orders. |
| saving_min_stuck_sensor | 0 | EGP | planning | 0 | 0 | Planning review threshold for a single district incident, not a cap on the computed value. Verify against the asset replacement budget and historical incident-loss register. |
| saving_max_stuck_sensor | 50000 | EGP | planning | 50000 | 50000 | Planning review threshold for a single district incident, not a cap on the computed value. Verify against the asset replacement budget and historical incident-loss register. |
| delay_hours_energy_waste | 6 | h | planning | 4.5 | 7.5 | An efficiency intervention can wait one shift without assumed additional repair damage; this horizon is a planning scenario. Verify against fault-specific restoration logs and the response SLA. |
| late_multiplier_energy_waste | 1 | × | planning | 1 | 1 | An efficiency intervention can wait one shift without assumed additional repair damage; the multiplier is a planning allowance, not an estimated growth law. Verify against matched early and deferred repair work orders. |
| saving_min_energy_waste | 0 | EGP | planning | 0 | 0 | Planning review threshold for a single district incident, not a cap on the computed value. Verify against the asset replacement budget and historical incident-loss register. |
| saving_max_energy_waste | 5e+06 | EGP | planning | 5e+06 | 5e+06 | Planning review threshold for a single district incident, not a cap on the computed value. Verify against the asset replacement budget and historical incident-loss register. |
| crews_available | 2 | crews | planning | 2 | 2 | Planning count of crews that can work at the same time across the whole district. Set it to the real shift roster; it is a given, not an uncertain price, so it is not sampled. Verify against the duty roster. |
| crew_switch_hours | 0.5 | h | planning | 0.25 | 0.75 | Planning allowance for a crew to pack up, travel and set up at its next job. Verify against dispatch logs. |
| fix_hours_pipe_burst | 3 | h | planning | 2.25 | 3.75 | Planning allowance for a crew to excavate, isolate and replace a section or joint, counted from arrival to the asset being back. Verify against work-order durations for matching jobs. |
| fix_hours_pump_failure | 4 | h | planning | 3 | 5 | Planning allowance for a crew to swap or repair a pump and restart it, counted from arrival to the asset being back. Verify against work-order durations for matching jobs. |
| fix_hours_substation_trip | 1.5 | h | planning | 1.125 | 1.875 | Planning allowance for a crew to inspect, re-close the breaker and re-energise, counted from arrival to the asset being back. Verify against work-order durations for matching jobs. |
| fix_hours_cooling_fault | 5 | h | planning | 3.75 | 6.25 | Planning allowance for a crew to diagnose and repair or swap a chiller component, counted from arrival to the asset being back. Verify against work-order durations for matching jobs. |
| fix_hours_stuck_sensor | 1 | h | planning | 0.75 | 1.25 | Planning allowance for a crew to check and recalibrate or replace the sensor, counted from arrival to the asset being back. Verify against work-order durations for matching jobs. |
| fix_hours_energy_waste | 1 | h | planning | 0.75 | 1.25 | Planning allowance for a crew to shed or transfer the excess load, counted from arrival to the asset being back. Verify against work-order durations for matching jobs. |
