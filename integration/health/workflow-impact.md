# How the twin's three pillars reach your KPI workflow

For the healthcare-investment team. The twin (Care Grid) has three pillars: **Water**, **Electricity** and **Cooling**. This page says where each one enters your five-level
KPI workflow (pillar KPIs, sub-indices, outcomes, scores, city KPIs), what the twin can give you today, and what we need you to decide. Every link below is **structural**
(which district, which hospital, which KPI), not statistical: your correlations (r) are measured on your data and we do not claim any for ours. Both datasets are simulated.

## Three things to know first

1. **Only Water has columns in your panel.** Water touches WP4 (leak incidents), WP6 (supply interruption) and WP7 (non-revenue water). Electricity and Cooling have none:
   your framework replaced the Electricity pillar with Water Pollution, and your proposal's first version had electricity inside the ESI composite and as an occupancy proxy.
2. **The effect of an outage on the ESI is small.** WP6 is 10% of WPS and WPS is 30% of ESI, so a full 24 extra hours of interruption adds at most 0.10 x 0.30 x 100 = **3.0 ESI
   points**. The highest ESI in September 2026 is 28.9, so an outage alone cannot take a district to High (35 or more).
3. **The bigger channel is Healthcare Capacity, and your scored KPIs do not see it.** A power or cooling failure can take hospitals out of service, but hospital capacity only enters
   HA2 and HA5, which are context KPIs. CPS and the Need Score use HA4 = `pc_visits_demand / pc_capacity_visits` (primary care).

Electricity is also a water failure: a substation trip stops two or three district pumps (SS-01, SS-02, SS-03: two; SS-04: three), so it reaches WP6 as well.

## Where each pillar enters (L1 = raw KPI, L2 = sub-index, L3 = outcome, L4 = scores, L5 = city KPIs)

### Water

| Your KPI (level) | What the twin gives | Basis | What we need |
| --- | --- | --- | --- |
| WP6 supply interruption, `pw_supply_interruption_hours` (L1, 10% of WPS, then ESI) | Which zones lose supply when a pump or pipe fails (`scenarios.csv`: `affected_zones`, `pumps_stopped`) | Zones: simulated network. Duration: assumed (crew response plus repair time of our cost model) | Confirm your hours are a district-month total |
| WP4 leak and sewage incidents (L1, 15% of WPS) | Leak rate of a pipe burst, e.g. 150 m3/h on `water-pipe-01`, and whether the detector finds it | Simulated. We assume **no failure rate**, so we say "if it happens", never "how often" | Your counts are history; we cannot predict their frequency |
| WP7 non-revenue water (L1, context) | Our non-revenue-water KPI (about 10%) | Calculated on the simulated network | Optional comparison |
| WP1 to WP3 (E. coli, turbidity, chlorine) and WP5 (complaints) | **Nothing.** The twin has no water-quality model | A pressure loss raising contamination risk is a hypothesis, not modelled | None; we leave them to your data |

It reaches L3 only through WPS (your link to waterborne visits, HD4). Air quality and Waste have no link to the twin.

### Electricity

| Your KPI (level) | What the twin gives | Basis | What we need |
| --- | --- | --- | --- |
| HA2 effective capacity `pc_effective_capacity` and HA5 `cpr` (L1, **context, not scored**) | Which hospitals a failure reaches: `hospital_exposure.csv`. A trip of SS-02 surely reaches 13 hospitals (government 3, medical city 8, knowledge city 2), SS-01 reaches 3 | Placed by your district and our zone match (`zone_mapping.csv`), with a `certainty` column | How much capacity a hospital loses without power, and whether it has backup |
| HA4 primary-care CPR, CPS, Need Score (L1, L2, L4: 40% of Need) | Clinics reached by the same failure (`clinics` column) | Same match | See question 3 below |
| WP6 supply interruption (L1, through the pumps) | Pumps stopped per substation trip (`pumps_stopped`) | Simulated network | None |
| Electricity as an occupancy and seasonal-load proxy (your proposal) | Load per zone in MW; unserved MW per failure (`unserved_mw`) | Simulated | Whether you want load added as a column |

### Cooling

| Your KPI (level) | What the twin gives | Basis | What we need |
| --- | --- | --- | --- |
| HD5 heat-related visits per 1,000 (L3) | Cooling deficit per plant failure: DCP-01 (government, presidential) 1,593 tons, DCP-02 (CBD, culture) 1,300 tons | Simulated. Loss of indoor cooling adding heat-related visits is a **hypothesis**; neither dataset supports it | Whether to keep it as a stated assumption |
| HA2 effective capacity (L1, context) | Hospitals in the districts a plant serves: `cooling_fault:cool-plant-01` 3, `cool-plant-02` 12 | Same match as above | Capacity lost without cooling |
| HT1 and HT2 (heat driver, L2) | **No link in this direction.** Our plants do not change outdoor temperature | | |

The other direction is useful to us: your `avg_temp_c` and `heat_days_ge38c` by district and month can drive our cooling and electricity demand scenarios.

## What a failure does to your score, by your own formula (illustration, not a result)

If a district gets **6 extra interrupted hours** in a month: s(WP6) = 100 x 6 / 24 = 25, so WPS rises 2.5 points and ESI 0.75 points. At **24 hours** it is 3.0 points. In your panel the
monthly interruption already averages 7.1 hours and some districts are above 24 (the score is clipped at 100), so for those the extra hours add nothing.

## Questions for you

1. By how much should a power or cooling failure reduce a hospital's capacity (a fraction, with or without backup generators)? We will not invent the number.
2. Do you want an electricity / reliability pillar back in the workflow, or keep our figures as an overlay next to it (the joint table does the second today)?
3. Should an outage move a **scored** KPI (HA4 and so CPS and Need) or stay a context KPI? Today it can only reach context KPIs.
4. What does `pc_centers` count? Your `hospital_centers` equals the hospitals in `healthcare_facilities.csv` in every district, but `pc_centers` does not equal the clinics (for example
   district 1004: 7 against 3, district 1013: 17 against 8), so we cannot yet say which clinics a failure reaches.

## What you can take today

`out/scenarios.csv` (per failed asset), `out/hospital_exposure.csv` (per failure and district with hospitals), `out/joint_decision.csv` (per district, both sides next to each other),
`out/districts_on_twin_map.csv` (your districts file with the centroids moved onto the twin's zones, drawing positions only). Column meanings are in `out/README.md`.
