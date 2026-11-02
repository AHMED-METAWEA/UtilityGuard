# Tables for the healthcare-investment project

Made by `backend/scripts/export_for_health.py` from the UtilityGuard digital twin of the New Administrative Capital. `MANIFEST.json` says how (seed, commit,
SHA-256 of every file). **The twin is simulated and the cost figures use planning assumptions, so these tables show how the method behaves, not what a real
outage would cost.**

## How to join

`district_key` (1001..1015, the key of `master_panel.csv`) -> `district_risk.csv` directly. For detail go through `zone_mapping.csv` (`district_key` -> our zone
ids) and then `scenario_zones.csv` (`zone_id`) and `scenarios.csv` (`scenario_id`). Nothing in your files is changed by this.

`match_kind` says how good the match is: `direct` (the same district in both projects: 1002, 1009, 1010, 1012), `type` (no equal district; the numbers are the
average of our zones of the same type, so read them as "a residential district", not as that district), `none` (no counterpart, empty numbers).

## scenarios.csv (one row per failed asset)

| Column | Meaning |
| --- | --- |
| `scenario_id` | `<kind>:<asset_id>` |
| `kind` | `substation_trip`, `pump_failure`, `cooling_fault`, `pipe_burst` |
| `detected` | The twin's detector raised an incident within the run. When false the numeric columns are empty |
| `affected_zones` | Our zones hit, separated by `;` |
| `leak_m3h`, `unserved_mw`, `excess_mw`, `cooling_deficit_tons`, `secondary_mw`, `pumps_stopped` | What the detector measured while the asset was down: water lost (m3/h), electricity demand not served (MW), excess draw (MW), cooling shortfall (tons), electricity bought for a dependent network (MW), pumps stopped (for `pump_failure` the failed pump itself counts) |
| `delay_hours`, `response_hours` | The deferred-response horizon and the crew response time of the cost model (assumptions) |
| `cost_per_hour_egp` | Cost of the incident for each hour it stays open |
| `act_now_egp`, `cost_of_delay_egp`, `saving_egp` | Cost of sending a crew now, of waiting `delay_hours`, and the difference |
| `saving_p10_egp`, `saving_p50_egp`, `saving_p90_egp` | Range of the saving when every assumption varies inside its registered range (800 seeded runs) |
| `needs_review` | The saving falls outside the plausible range of the cost model |

## scenario_zones.csv (one row per failed asset and affected zone)

`customer_share` is the zone's share of the affected service accounts; `unserved_mw` is the incident's unserved load split by that share (the way the cost
model splits it); `zone_cost_of_delay_egp` holds only the cost terms that belong to that zone (energy not served there and the notification and support
work there). Costs shared by the whole incident (repair, water lost, cooling, secondary) are in `scenarios.csv` only, so summing the zone column does not double count.

## district_risk.csv (one row per district_key)

`scenarios_affecting`: how many of the failed-asset scenarios touch the district (an average over the zones when `match_kind` is `type`).
`worst_zone_cost_of_delay_egp` and `worst_zone_unserved_mw`: the largest of the zone's values over all scenarios (averaged over zones for `type`).
`worst_scenario_id`: which scenario it was (only for `direct`).

## hospital_exposure.csv (one row per failed asset and district with hospitals that it reaches)

Their hospitals are placed by the district of their file, and the district by `zone_mapping.csv`; the twin links a zone, not a hospital, to its substation, pump
and plant. `certainty` says how far to trust a row: `all_matched_zones` (every zone the district is matched to is hit; always so for a `direct` district) or
`some_matched_zones` (only some are, so the district may not be affected at all: read `hospitals` as an upper bound). `hospitals`, `clinics`, `specialists` are
the counts of the district in their facilities file.

## joint_decision.csv (one row per district_key)

The twin's side and their side next to each other, never added together: `worst_zone_cost_of_delay_egp`, `worst_zone_unserved_mw`, `scenarios_affecting` and the two
`scenarios_reaching_hospitals_*` counts are ours; `esi_*` (their 0 to 100 stress scores for `env_month`, with their bands: below 20 low, 20 to under 35 moderate,
35 and above high) and the facility counts are theirs. `outage_cost_vs_median` and `environment_vs_median` say whether the district is above or below the median of
the districts that have the figure (a relative reading: no threshold of ours is invented, and all overall scores sit in one band). `group` is `both_higher`,
`outage_higher`, `environment_higher`, `both_lower`, or `no_outage_data` (no counterpart in the twin).

## reliability_packages.csv (ranked improvement packages)

Same idea as the package comparison of the healthcare dashboard: one row per (package, asset it protects), best first.

| Column | Meaning |
| --- | --- |
| `rank` | Fewest failures needed to pay the package back comes first; ties go to the cheaper package |
| `cost_m_egp_5yr`, `cumulative_cost_m_egp`, `budget_status` | Placeholder five-year cost (from `packages.csv`), the running total down the ranking, and `Funded` while the running total stays inside the budget (`--budget`, default 15 M EGP) |
| `event_cost_egp` | What one failure of that asset costs when a crew is sent now: repair plus the hourly cost until the asset is back (response time and on-site work), as the board's plan counts it. From the cost engine |
| `event_cost_with_package_egp`, `avoided_per_event_egp` | The same failure with the package in place, and the difference |
| `break_even_events_5yr` | Package cost divided by the cost avoided per failure: how many such failures in five years it takes for the package to pay for itself. Empty when it avoids nothing |
| `zones_protected`, `direct_districts_protected` | Our zones the failure hits, and the districts of the healthcare project that are the same district as one of them (`match_kind` `direct`) |

The package costs and effects are **placeholders** in `packages.csv` (one editable file, like the cost and price model of the proposal); the failure costs are the
cost engine's. No failure rate is assumed: the table says how many failures are needed, and the reader judges whether that many is likely.

## What these tables do not contain

Water quality (E. coli, chlorine, turbidity), air quality and waste: the twin models flow, pressure, electricity and cooling, not chemistry or emissions.
