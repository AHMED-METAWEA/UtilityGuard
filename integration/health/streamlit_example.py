"""Example Streamlit page: utility reliability for one district, from the UtilityGuard tables.

Copy this file next to your `app.py` (or into `dashboard/views/`) and run it with `streamlit run streamlit_example.py`, or call `render()` from your own
view. It only READS the CSV files written by UtilityGuard's `export_for_health.py`; it never touches `master_panel.csv` or any of your files.

    DATA = Path("path/to/integration/health/out")      # the folder with scenarios.csv, scenario_zones.csv, district_risk.csv, zone_mapping.csv

Works with the packages in your requirements.txt (pandas, streamlit). The tables are simulated, so the page says so.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

DATA = Path(__file__).resolve().parent / "out"


def load_tables(folder: Path = DATA) -> dict[str, pd.DataFrame]:
    tables = {name: pd.read_csv(folder / f"{name}.csv", dtype={"district_key": str}) for name in ("scenarios", "scenario_zones", "district_risk", "zone_mapping")}
    return tables


def district_view(tables: dict[str, pd.DataFrame], district_key: str) -> tuple[pd.Series, pd.DataFrame]:
    """The district's match with the twin, and one row per (failed asset, our zone) that touches it, worst first."""
    mapping = tables["zone_mapping"]
    row = mapping.loc[mapping["district_key"] == str(district_key)].iloc[0]
    zones = [z for z in str(row["our_zone_ids"]).split(";") if z and z != "nan"]
    hits = tables["scenario_zones"][tables["scenario_zones"]["zone_id"].isin(zones)]
    detail = hits.merge(tables["scenarios"][["scenario_id", "asset_name", "cost_per_hour_egp", "cost_of_delay_egp", "delay_hours", "saving_p10_egp", "saving_p90_egp"]], on="scenario_id")
    return row, detail.sort_values("zone_cost_of_delay_egp", ascending=False).reset_index(drop=True)


def render(folder: Path = DATA) -> None:
    import streamlit as st

    tables = load_tables(folder)
    st.title("Utility reliability")
    st.caption("From the UtilityGuard digital twin of the New Administrative Capital. Simulated data and planning cost assumptions, not field measurements.")
    keys = tables["zone_mapping"]["district_key"].tolist()
    names = dict(zip(tables["zone_mapping"]["district_key"], tables["zone_mapping"]["district_name_ar"]))
    key = st.sidebar.selectbox("District", keys, format_func=lambda k: f"{k} {names[k]}")
    row, detail = district_view(tables, key)
    if row["match_kind"] == "none":
        st.info("The twin has no equivalent of this district, so there is nothing to show.")
        return
    if row["match_kind"] == "type":
        st.warning(f"No district of the twin equals this one. The figures are for the twin's {row['our_zone_type']} zones ({row['our_zone_ids']}): read them as a "
                   f"{row['our_zone_type']} district, not as this one.")
    risk = tables["district_risk"].set_index("district_key").loc[key]
    a, b, c = st.columns(3)
    a.metric("Failure scenarios that touch it", f"{risk['scenarios_affecting']:g}")
    b.metric("Worst cost of waiting (EGP)", f"{risk['worst_zone_cost_of_delay_egp']:,.0f}")
    c.metric("Worst electricity not served (MW)", f"{risk['worst_zone_unserved_mw']:,.1f}")
    st.subheader("Which failures hurt most")
    st.bar_chart(detail.set_index("scenario_id")["zone_cost_of_delay_egp"])
    st.dataframe(detail, use_container_width=True)
    st.caption("`zone_cost_of_delay_egp` counts only the cost that belongs to this zone; the incident's shared costs are in scenarios.csv.")


if __name__ == "__main__":
    render()
