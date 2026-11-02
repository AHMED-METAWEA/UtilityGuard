# UtilityGuard x Healthcare Investment Planning

This folder is the only point of contact between two projects:

- **UtilityGuard**, a live digital twin of the New Administrative Capital's water, electricity and cooling networks (this repository).
- **Healthcare Investment Planning**, a Streamlit dashboard that forecasts healthcare demand per district and ranks investment packages (a separate repository, `master_panel.csv` keyed by `district_key` 1001..1015).

The idea in one line: *they decide where a clinic or hospital should be built; the twin says how reliable the water, electricity and cooling will be there, and what it costs
when they fail.* Nothing in either project is edited by the other: UtilityGuard writes the CSV files in `out/`, and the healthcare dashboard may read them.

## What is in here

| File | What it is |
| --- | --- |
| `zone_mapping.csv` | Which UtilityGuard zone each healthcare district corresponds to, and how good the match is (`direct`, `type`, `none`) |
| `packages.csv` | The editable placeholders for the improvement packages: cost, and the effect they have on a failure |
| `out/` | The tables written by `backend/scripts/export_for_health.py` (see `out/README.md` for every column) |
| `streamlit_example.py` | A ready page for the Streamlit dashboard that reads `out/` |

## Run it

```bash
cd backend
PYTHONPATH=. .venv/bin/python scripts/export_for_health.py            # writes integration/health/out/ (about 10 seconds, no network, no language model)
PYTHONPATH=. .venv/bin/python scripts/export_for_health.py --budget 30 # change the budget of the Funded / Over budget column (M EGP)
```

The same seed gives the same files (`MANIFEST.json` lists a SHA-256 for each).

## What the tables say, in one example

`out/reliability_packages.csv` ranks improvement packages the way the healthcare dashboard ranks its own packages. The first row of the default run:

> Rank 1, P1 "Second feed (N-1) for a substation", asset `elec-sub-02`: 6.0 M EGP over five years; one trip of that substation costs about 3.7 M EGP now and
> about 0.8 M EGP with the package; the package pays for itself after about 2 such trips in five years.

No failure rate is assumed. The table says how many failures are needed, and the reader judges whether that many is likely.

## What is real and what is a placeholder

- The failures are runs of the twin (sandbox copy, the real detector, the real cost engine, the registered default assumptions). The twin is simulated.
- The cost assumptions of the twin are planning values with a stated basis, and the package costs and effects in `packages.csv` are placeholders: replace them with quotations before any real use.
- Matching is by name and type, not by place: only 4 of the 15 healthcare districts are the same district as one of ours (`direct`: 1002, 1009, 1010, 1012). Eleven are read as "a district of that type" (`type`, 1011 and 1013 and 1014 approximately) or have no counterpart (`none`, 1015).
- The twin models flow, pressure, electricity and cooling. It does **not** model water quality (E. coli, chlorine, turbidity), air quality or waste, and these tables contain none of them.

## Using the tables from the Streamlit dashboard

Copy `streamlit_example.py` next to your `app.py` and point `DATA` at the `out/` folder (or call `render()` from one of your views). It needs only pandas and streamlit, reads the CSV files,
and never touches `master_panel.csv`. If you would rather keep the tables out of your repository, copy the `out/` folder somewhere you control.
