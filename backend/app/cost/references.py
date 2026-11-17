"""Public reference points for the cost inputs, each with its publisher, link and date.

A reference is evidence next to a registered value, not a replacement for it. `low`/`high` are in the
assumption's own unit (a point has low == high); a reference with no `low` is context only and is
never compared. Nothing here was checked against an official gazette or tariff schedule: press and
aggregator figures are labelled as such. Found by web search on 2026-10-29.
"""

from __future__ import annotations

EI_TARIFF = "https://www.egyptindependent.com/egypts-2026-electricity-prices-the-full-list-of-household-and-commercial-rates-after-latest-updates/"
TARIFF_NOTE = "Reported by the press; the regulator's own schedule was not checked."


def _commercial(label: str, v: float) -> dict:
    return dict(label=label, low=v, high=v, unit="EGP/kWh", kind="reported", publisher="Egypt Independent", url=EI_TARIFF, asof="April 2026", note=TARIFF_NOTE)


REFERENCES: dict[str, list[dict]] = {
    "elec_tariff": [
        _commercial("Commercial tariff, up to 100 kWh a month", 1.62),
        _commercial("Commercial tariff, up to 250 kWh a month", 2.16),
        _commercial("Commercial tariff, up to 600 kWh a month", 2.64),
        _commercial("Commercial tariff, up to 1,000 kWh a month", 2.74),
        _commercial("Commercial tariff, above 1,000 kWh a month", 2.79),
        dict(label="Industrial flat rate", low=2.33, high=2.33, unit="EGP/kWh", kind="reported", publisher="Reslink (solar industry blog)",
             url="https://www.reslink.org/blogs/electricity-tariffs-just-rose-again-solar-payback-is-now-under-5-years/", asof="August 2026",
             note="A blog summary of the tariff table. Sources disagree on some residential tiers, so treat it as indicative."),
        dict(label="Business electricity price", low=1.94, high=1.94, unit="EGP/kWh", kind="aggregator", publisher="GlobalPetrolPrices",
             url="https://www.globalpetrolprices.com/Egypt/electricity_prices/", asof="June 2025 data",
             note="An aggregator, and older than the April 2026 increase."),
    ],
    "water_price": [
        dict(label="Production cost of one cubic metre, Holding Company for Water and Wastewater", low=1.4, high=1.4, unit="EGP/m3", kind="historical",
             publisher="Ahram Online", url="https://english.ahram.org.eg/News/100283.aspx", asof="2014",
             note="The latest production cost found. It is twelve years old and is shown for context, not compared."),
        dict(label="Household tariff after the 2017 increase, 0.65 to 3.15 EGP/m3; commercial 3.6 EGP/m3", low=None, high=None, unit="EGP/m3", kind="historical",
             publisher="Associated Press via Fox News", url="https://www.foxnews.com/world/egypt-raises-water-sewage-bills-as-part-of-reform-program.amp", asof="2017",
             note="A tariff is what customers pay, not what production costs. No 2026 schedule was found."),
    ],
    "cooling_price": [
        dict(label="First district cooling station in the capital (Gascool): first stage about 2.3 billion EGP", low=None, high=None, unit="EGP", kind="related",
             publisher="Egypt Independent", url="https://www.egyptindependent.com/contract-signed-for-cooling-station-in-new-capital/", asof="contract announcement",
             note="A capital cost. No price per ton-hour has been published; ask the authority or the operator."),
    ],
    "discount_rate": [
        dict(label="Central Bank of Egypt overnight deposit rate 19%, lending rate 20%", low=0.19, high=0.20, unit="fraction/year", kind="reported",
             publisher="Central Banking", url="https://www.centralbanking.com/central-banks/monetary-policy/monetary-policy-decisions/7975038/egypt-cuts-rates-by-100bp-and-lowers-reserve-requirements",
             asof="February 2026 cut; press reports say it was held at the September 2026 meeting",
             note="A project discount rate normally sits above the policy rate by a risk margin. The registered 18% is below the 19% deposit rate, so finance should confirm it."),
    ],
    "pm_n1": [
        dict(label="Theory: N1 from 0.5 for fixed-area leaks to 1.5 for variable-area leaks", low=0.5, high=1.5, unit="×", kind="benchmark",
             publisher="Journal of Environmental Studies, University of Tehran", url="https://jes.ut.ac.ir/article_60064_en.html", asof="published study",
             note="The pressure-leakage power law."),
        dict(label="Field: 107 pressure-management zones in South Africa, N1 from -0.34 to 2.20", low=-0.34, high=2.20, unit="×", kind="benchmark",
             publisher="University of Cape Town", url="https://open.uct.ac.za/handle/11427/30949", asof="published thesis",
             note="The authors treat values outside 0.5 to 1.5 as signs of data or assumption errors."),
    ],
    "reset_pct_per_c": [
        dict(label="Rule of thumb: 1 to 2% per °F of chilled-water set-point raise (1.8 to 3.6% per °C)", low=0.018, high=0.036, unit="fraction/°C", kind="benchmark",
             publisher="CSE Magazine and HVAC training guides", url="https://www.csemag.com/hvac-energy-saving-strategies/", asof="published article",
             note="CSE quotes 2 to 2.5% per °F for compressor input."),
        dict(label="US Department of Energy: 0.5 to 0.75% per °F for fixed-speed chillers (0.9 to 1.35% per °C)", low=0.009, high=0.0135, unit="fraction/°C", kind="benchmark",
             publisher="US Department of Energy", url="https://www1.eere.energy.gov/buildings/publications/pdfs/alliances/hea_chillers.fs.pdf", asof="fact sheet",
             note="Depends on the compressor drive. Check which kind the plants have."),
        dict(label="US Department of Energy: 2 to 3% per °F for variable-speed chillers (3.6 to 5.4% per °C)", low=0.036, high=0.054, unit="fraction/°C", kind="benchmark",
             publisher="US Department of Energy", url="https://www1.eere.energy.gov/buildings/publications/pdfs/alliances/hea_chillers.fs.pdf", asof="fact sheet",
             note="Variable-speed machines save the most per degree."),
    ],
}

_LABOUR = [
    dict(label="National minimum wage 8,000 EGP a month from July 2026", low=None, high=None, unit="EGP/month", kind="related",
         publisher="Egyptian Streets", url="https://egyptianstreets.com/2026/04/02/egypt-raises-minimum-wage-to-8000-egp-from-july-2026/", asof="April 2026",
         note="Sources disagree whether it covers the private sector. At 26 days of 8 hours it is about 38 EGP an hour for labour alone (our arithmetic); a call-out also pays for the vehicle, tools and the contractor's margin."),
    dict(label="Diesel about 20.5 EGP a litre", low=None, high=None, unit="EGP/L", kind="related",
         publisher="GlobalPetrolPrices", url="https://www.globalpetrolprices.com/Egypt/diesel_prices/", asof="September 2026",
         note="An aggregator. The official fuel-pricing committee decision was not found."),
]
REFERENCES["crew_callout"] = _LABOUR
REFERENCES["false_dispatch"] = _LABOUR

NOT_FOUND = {
    "cooling_price": "Searched on 2026-10-29: no published price per ton-hour for the capital's district cooling.",
    "water_price": "Searched on 2026-10-29: no 2026 production cost or tariff schedule. The newest public figures are from 2014 to 2017.",
    **{f"voll_{t}": "Searched on 2026-10-29: no Egypt-specific value of lost load study. The 2025 World Bank Enterprise Survey records firms' reported outage losses, which could be normalised to EGP per kWh."
       for t in ("government", "business", "residential", "services")},
}


def evidence(key: str, low: float, high: float) -> str:
    """How the public figures sit against the registered range: consistent, partly, differs, related (context only) or none."""
    refs = REFERENCES.get(key, [])
    comparable = [r for r in refs if r.get("low") is not None and r["kind"] != "historical"]
    if not comparable:
        return "related" if refs else "none"
    inside = sum(1 for r in comparable if r["low"] <= high and r["high"] >= low)
    return "consistent" if inside == len(comparable) else "differs" if inside == 0 else "partly"
