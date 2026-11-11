"""Official open-data connectors (Stage K): authoritative year-by-year series for any topic.

The stock-market path fetches NSE index closes and builds a verified dataset; every other domain only had
whatever tables web pages happened to contain. This module gives every report the same footing:

  1. ``plan_datasets``  - one LLM call picks up to N datasets from a curated CATALOG (World Bank WDI,
     IMF World Economic Outlook, Our World in Data) and the countries/regions they are needed for; code
     validates every id and entity (keyword fallback when the call fails);
  2. ``fetch_dataset``   - the publisher's own public API (no keys), clipped to the question's window
     (whole years; IMF projections and the year in progress excluded);
  3. ``as_source``       - a source row like a fetched publisher page: the human-facing dataset URL, the
     publisher, and the year-by-year table as the page text ("retrieved via the publisher's API" is stated),
     so citations, the source list, the number policy, chart verification and the rendered checks all apply
     unchanged.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date

log = logging.getLogger("official_data")

# ── catalog ──────────────────────────────────────────────────────────────────────────────────────
# (id, connector, code, title, unit, keywords)
_WB = [
    ("wb.gdp_growth", "NY.GDP.MKTP.KD.ZG", "GDP growth", "annual %", "gdp growth economy economic output recession"),
    ("wb.gdp_usd", "NY.GDP.MKTP.CD", "GDP", "current US$", "gdp economy size output"),
    ("wb.gdp_pc", "NY.GDP.PCAP.CD", "GDP per capita", "current US$", "income per capita living standards"),
    ("wb.inflation", "FP.CPI.TOTL.ZG", "Inflation, consumer prices", "annual %", "inflation prices cpi cost of living"),
    ("wb.unemployment", "SL.UEM.TOTL.ZS", "Unemployment", "% of labor force", "unemployment jobs labour labor employment"),
    ("wb.exports_gdp", "NE.EXP.GNFS.ZS", "Exports of goods and services", "% of GDP", "exports trade openness"),
    ("wb.imports_gdp", "NE.IMP.GNFS.ZS", "Imports of goods and services", "% of GDP", "imports trade"),
    ("wb.trade_gdp", "NE.TRD.GNFS.ZS", "Trade", "% of GDP", "trade openness globalisation"),
    ("wb.fdi_inflows", "BX.KLT.DINV.CD.WD", "Foreign direct investment, net inflows", "current US$", "fdi foreign investment capital inflows"),
    ("wb.manufacturing_gdp", "NV.IND.MANF.ZS", "Manufacturing, value added", "% of GDP", "manufacturing industry factories production"),
    ("wb.hightech_exports", "TX.VAL.TECH.CD", "High-technology exports", "current US$", "high-tech exports electronics semiconductors technology"),
    ("wb.hightech_share", "TX.VAL.TECH.MF.ZS", "High-technology exports", "% of manufactured exports", "high-tech exports semiconductors electronics"),
    ("wb.rnd_gdp", "GB.XPD.RSDV.GD.ZS", "Research and development expenditure", "% of GDP", "r&d research innovation technology"),
    ("wb.internet", "IT.NET.USER.ZS", "Individuals using the Internet", "% of population", "internet digital connectivity online"),
    ("wb.mobile", "IT.CEL.SETS.P2", "Mobile cellular subscriptions", "per 100 people", "mobile phones telecom subscriptions"),
    ("wb.population", "SP.POP.TOTL", "Population", "people", "population demographics"),
    ("wb.urban", "SP.URB.TOTL.IN.ZS", "Urban population", "% of total population", "urbanisation urbanization cities"),
    ("wb.life_exp", "SP.DYN.LE00.IN", "Life expectancy at birth", "years", "life expectancy health mortality"),
    ("wb.health_exp", "SH.XPD.CHEX.GD.ZS", "Current health expenditure", "% of GDP", "health spending healthcare expenditure"),
    ("wb.education_exp", "SE.XPD.TOTL.GD.ZS", "Government expenditure on education", "% of GDP", "education spending schools"),
    ("wb.electricity_access", "EG.ELC.ACCS.ZS", "Access to electricity", "% of population", "electricity access energy poverty"),
    ("wb.renewable_share", "EG.FEC.RNEW.ZS", "Renewable energy consumption", "% of total final energy consumption", "renewable energy clean energy transition"),
    ("wb.co2_pc", "EN.GHG.CO2.PC.CE.AR5", "CO2 emissions per capita (excluding LULUCF)", "t CO2e per capita", "co2 emissions carbon climate"),
    ("wb.agri_gdp", "NV.AGR.TOTL.ZS", "Agriculture, forestry and fishing, value added", "% of GDP", "agriculture farming rural"),
    ("wb.services_gdp", "NV.SRV.TOTL.ZS", "Services, value added", "% of GDP", "services sector economy"),
    ("wb.govt_debt", "GC.DOD.TOTL.GD.ZS", "Central government debt", "% of GDP", "government debt public finances fiscal"),
    ("wb.remittances", "BX.TRF.PWKR.CD.DT", "Personal remittances received", "current US$", "remittances migrants diaspora"),
    ("wb.tourism_arrivals", "ST.INT.ARVL", "International tourism, number of arrivals", "arrivals", "tourism tourists travel"),
    ("wb.poverty", "SI.POV.DDAY", "Poverty headcount ratio at $2.15 a day", "% of population", "poverty extreme poverty inequality"),
    ("wb.gini", "SI.POV.GINI", "Gini index", "index (0-100)", "inequality gini income distribution"),
    ("wb.market_cap", "CM.MKT.LCAP.GD.ZS", "Market capitalization of listed domestic companies", "% of GDP", "stock market capitalisation equities"),
    ("wb.domestic_credit", "FS.AST.PRVT.GD.ZS", "Domestic credit to private sector", "% of GDP", "credit banking lending finance"),
    ("wb.lending_rate", "FR.INR.LEND", "Lending interest rate", "%", "interest rates lending monetary policy"),
    ("wb.gross_capital", "NE.GDI.TOTL.ZS", "Gross capital formation", "% of GDP", "investment capex capital formation infrastructure"),
    ("wb.military", "MS.MIL.XPND.GD.ZS", "Military expenditure", "% of GDP", "defence defense military spending"),
]
_IMF = [
    ("imf.gdp_growth", "NGDP_RPCH", "Real GDP growth", "annual %", "gdp growth economy outlook imf"),
    ("imf.inflation", "PCPIPCH", "Inflation rate, average consumer prices", "annual %", "inflation prices imf"),
    ("imf.unemployment", "LUR", "Unemployment rate", "% of labor force", "unemployment jobs imf"),
    ("imf.current_account", "BCA_NGDPD", "Current account balance", "% of GDP", "current account external balance trade deficit"),
    ("imf.govt_debt", "GGXWDG_NGDP", "General government gross debt", "% of GDP", "government debt fiscal public debt"),
    ("imf.fiscal_balance", "GGXCNL_NGDP", "General government net lending/borrowing", "% of GDP", "fiscal deficit budget balance"),
    ("imf.gdp_pc_ppp", "PPPPC", "GDP per capita, current prices (PPP)", "international $", "income per capita ppp"),
]
_OWID = [
    ("owid.ev_car_share", "electric-car-sales-share", "Share of new cars that are electric", "%", "electric vehicles ev cars sales share adoption"),
    ("owid.ev_car_sales", "electric-car-sales", "Electric car sales", "cars", "electric vehicles ev car sales"),
    ("owid.ev_stocks", "electric-car-stocks", "Electric car stocks", "cars", "electric vehicles ev fleet stock"),
    ("owid.solar_share", "share-electricity-solar", "Share of electricity from solar", "%", "solar power electricity renewable"),
    ("owid.wind_share", "share-electricity-wind", "Share of electricity from wind", "%", "wind power electricity renewable"),
    ("owid.renewables_share_elec", "share-electricity-renewables", "Share of electricity from renewables", "%", "renewable electricity clean power"),
    ("owid.coal_share_elec", "share-electricity-coal", "Share of electricity from coal", "%", "coal electricity fossil fuels"),
    ("owid.electricity_gen", "electricity-generation", "Electricity generation", "TWh", "electricity generation power demand"),
    ("owid.energy_pc", "per-capita-energy-use", "Primary energy consumption per capita", "kWh per person", "energy consumption demand"),
    ("owid.co2_total", "annual-co2-emissions-per-country", "Annual CO2 emissions", "tonnes", "co2 emissions carbon climate"),
    ("owid.co2_pc", "co-emissions-per-capita", "CO2 emissions per capita", "tonnes per person", "co2 emissions per capita climate"),
    ("owid.oil_prod", "oil-production-by-country", "Oil production", "TWh", "oil production crude petroleum"),
    ("owid.gas_prod", "gas-production-by-country", "Gas production", "TWh", "natural gas production lng"),
    ("owid.internet_users", "share-of-individuals-using-the-internet", "Share of the population using the Internet", "%", "internet users digital"),
    ("owid.life_expectancy", "life-expectancy", "Life expectancy at birth", "years", "life expectancy health"),
    ("owid.child_mortality", "child-mortality", "Child mortality rate", "%", "child mortality health children"),
    ("owid.measles_vacc", "share-of-children-vaccinated-against-measles", "Share of one-year-olds vaccinated against measles", "%", "vaccination immunisation measles vaccines"),
    ("owid.dtp3_vacc", "share-of-children-immunized-dtp3", "Share of one-year-olds vaccinated with DTP3", "%", "vaccination immunisation dtp3 vaccines"),
    ("owid.obesity", "share-of-adults-defined-as-obese", "Share of adults who are obese", "%", "obesity health diet"),
    ("owid.health_exp_pc", "annual-healthcare-expenditure-per-capita", "Healthcare expenditure per capita", "international $", "healthcare spending health expenditure"),
    ("owid.cereal_yield", "cereal-yield", "Cereal yield", "tonnes per hectare", "agriculture crop yields cereals food"),
    ("owid.fertilizer", "nitrogen-fertilizer-application-per-hectare-of-cropland", "Nitrogen fertilizer use per hectare", "kg per hectare", "fertiliser fertilizer agriculture"),
    ("owid.air_pollution", "pm25-air-pollution", "PM2.5 air pollution, mean annual exposure", "µg/m³", "air pollution pm2.5 smog"),
    ("owid.forest_area", "forest-area-as-share-of-land-area", "Forest area", "% of land area", "forests deforestation land"),
    ("owid.military_exp", "military-expenditure-share-gdp", "Military expenditure", "% of GDP", "defence defense military spending"),
    ("owid.patent_apps", "patent-applications-per-million", "Patent applications per million people", "per million people", "patents innovation r&d"),
    ("owid.rnd_gdp", "research-spending-gdp", "Research and development spending", "% of GDP", "r&d research spending innovation"),
    ("owid.ai_investment", "private-investment-in-artificial-intelligence", "Private investment in artificial intelligence", "US$", "ai artificial intelligence investment"),
    ("owid.urban_share", "share-of-population-urban", "Share of the population living in urban areas", "%", "urbanisation cities"),
    ("owid.tourist_arrivals", "international-tourist-trips", "International tourist arrivals", "arrivals", "tourism tourists travel"),
]

# Stage L: FRED (Federal Reserve Bank of St. Louis) - keyless CSV; market and macro series used by finance,
# trade and energy topics in every country. Values are ANNUAL AVERAGES of the publisher's daily / monthly
# observations (complete years only) - the unit says so.
_FRED = [
    ("fred.us10y", "DGS10", "US 10-year Treasury yield", "%, annual average", "us treasury yield bond yields interest rates fed dollar"),
    ("fred.fedfunds", "FEDFUNDS", "Federal funds effective rate", "%, annual average", "federal reserve fed rate interest rates monetary policy"),
    ("fred.brent", "DCOILBRENTEU", "Brent crude oil price", "US$ per barrel, annual average", "oil crude brent prices energy"),
    ("fred.wti", "DCOILWTICO", "WTI crude oil price", "US$ per barrel, annual average", "oil crude wti prices energy"),
    ("fred.usd_inr", "DEXINUS", "Indian rupees per US dollar", "INR per US$, annual average", "rupee inr currency exchange rate depreciation"),
    ("fred.usd_cny", "DEXCHUS", "Chinese yuan per US dollar", "CNY per US$, annual average", "yuan renminbi currency exchange rate"),
    ("fred.usd_jpy", "DEXJPUS", "Japanese yen per US dollar", "JPY per US$, annual average", "yen currency exchange rate"),
    ("fred.eur_usd", "DEXUSEU", "US dollars per euro", "US$ per EUR, annual average", "euro currency exchange rate"),
    ("fred.dollar_index", "DTWEXBGS", "Nominal broad US dollar index", "index, annual average", "dollar strength currency emerging markets"),
    ("fred.vix", "VIXCLS", "CBOE Volatility Index (VIX)", "index, annual average", "volatility risk sentiment markets"),
    ("fred.sp500", "SP500", "S&P 500 index", "index, annual average", "us stocks equities s&p 500"),
]
_FRED_ENTITY = {"fred.usd_inr": "India", "fred.usd_cny": "China", "fred.usd_jpy": "Japan", "fred.eur_usd": "Euro area",
                "fred.brent": "World", "fred.wti": "World"}
_FRED_PAGE = "https://fred.stlouisfed.org/series/{code}"
_WB_PAGE = "https://data.worldbank.org/indicator/{code}"
_IMF_PAGE = "https://www.imf.org/external/datamapper/{code}"
_OWID_PAGE = "https://ourworldindata.org/grapher/{code}"


@dataclass
class CatalogEntry:
    id: str
    connector: str
    code: str
    title: str
    unit: str
    keywords: str

    @property
    def publisher(self) -> str:
        return {"wb": "World Bank", "imf": "International Monetary Fund", "owid": "Our World in Data",
                "fred": "FRED (Federal Reserve Bank of St. Louis)"}[self.connector]

    @property
    def collection(self) -> str:
        return {"wb": "World Development Indicators", "imf": "World Economic Outlook (DataMapper)",
                "owid": "Our World in Data grapher", "fred": "Federal Reserve Economic Data"}[self.connector]

    @property
    def page_url(self) -> str:
        return {"wb": _WB_PAGE, "imf": _IMF_PAGE, "owid": _OWID_PAGE, "fred": _FRED_PAGE}[self.connector].format(code=self.code)


CATALOG: dict[str, CatalogEntry] = {}
for _conn, _rows in (("wb", _WB), ("imf", _IMF), ("owid", _OWID), ("fred", _FRED)):
    for _id, _code, _title, _unit, _kw in _rows:
        CATALOG[_id] = CatalogEntry(_id, _conn, _code, _title, _unit, _kw)

# ISO3 code -> display name (OWID / World Bank naming); "WLD" is the world aggregate
COUNTRIES = {
    "WLD": "World", "IND": "India", "USA": "United States", "CHN": "China", "JPN": "Japan", "DEU": "Germany",
    "GBR": "United Kingdom", "FRA": "France", "ITA": "Italy", "ESP": "Spain", "CAN": "Canada", "BRA": "Brazil",
    "RUS": "Russia", "KOR": "South Korea", "TWN": "Taiwan", "AUS": "Australia", "MEX": "Mexico", "IDN": "Indonesia",
    "TUR": "Turkey", "SAU": "Saudi Arabia", "ARE": "United Arab Emirates", "ZAF": "South Africa", "NGA": "Nigeria",
    "EGY": "Egypt", "ARG": "Argentina", "NLD": "Netherlands", "CHE": "Switzerland", "SWE": "Sweden", "NOR": "Norway",
    "POL": "Poland", "BEL": "Belgium", "AUT": "Austria", "IRL": "Ireland", "ISR": "Israel", "SGP": "Singapore",
    "MYS": "Malaysia", "THA": "Thailand", "VNM": "Vietnam", "PHL": "Philippines", "PAK": "Pakistan", "BGD": "Bangladesh",
    "LKA": "Sri Lanka", "NPL": "Nepal", "KEN": "Kenya", "ETH": "Ethiopia", "IRN": "Iran", "IRQ": "Iraq", "QAT": "Qatar",
    "KWT": "Kuwait", "NZL": "New Zealand", "CHL": "Chile", "COL": "Colombia", "PER": "Peru", "UKR": "Ukraine",
    "DNK": "Denmark", "FIN": "Finland", "PRT": "Portugal", "GRC": "Greece", "CZE": "Czechia", "HUN": "Hungary",
    "ROU": "Romania", "EUU": "European Union",
}
_ALIASES = {"us": "USA", "u.s.": "USA", "america": "USA", "united states": "USA", "uk": "GBR", "britain": "GBR",
            "united kingdom": "GBR", "eu": "EUU", "european union": "EUU", "europe": "EUU", "global": "WLD", "world": "WLD",
            "korea": "KOR", "south korea": "KOR", "indian": "IND", "chinese": "CHN", "japanese": "JPN", "german": "DEU",
            "taiwanese": "TWN", "uae": "ARE"}
# OWID entity names for aggregates / ISO codes it uses differently
_OWID_ENTITY = {"WLD": "World", "EUU": "European Union (27)"}


@dataclass
class Dataset:
    entry: CatalogEntry
    years: list[int]
    series: dict[str, list[float | None]]           # entity name -> values per year
    retrieved: str
    latest: int | None = None
    notes: list[str] = field(default_factory=list)
    fit: str = "direct"   # Stage N: "stand-in" = a related indicator of the same subject, labelled as such
    measure: str = ""     # the measure of the plan this dataset serves


# ── planning ─────────────────────────────────────────────────────────────────────────────────────
PLAN_SYSTEM = ("You choose official statistical datasets that give a research report hard, year-by-year numbers. "
               "Pick only datasets that directly measure what the question is about. Return JSON only.")
PLAN_PROMPT = """QUESTION: {question}
TODAY: {today}

{measures}CATALOG (id | title | unit):
{catalog}

COUNTRY / REGION CODES: {codes}

Return {{"datasets": [{{"id": "<catalog id>", "entities": ["<code>", ...], "measure": "<the measure / part of the
question it serves>", "fit": "direct" | "stand-in", "why": "<one line>"}}]}}
Rules: at most {limit} datasets; 1-5 entities each (the countries the question is about, plus "WLD" or a peer only
when a comparison helps); "direct" = the dataset measures that quantity itself; "stand-in" = a closely related
indicator of the SAME subject (e.g. share of electricity from solar for solar capacity); never pick a whole-economy
indicator (GDP, FDI, credit, inflation) for a sector-specific measure; return {{"datasets": []}} when nothing in the
catalog measures the question's subject."""


def mentioned_entities(question: str) -> list[str]:
    low = f" {str(question or '').casefold()} "
    found = []
    for code, name in COUNTRIES.items():
        if re.search(rf"\b{re.escape(name.casefold())}\b", low) and code not in found:
            found.append(code)
    for alias, code in _ALIASES.items():
        if re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", low) and code not in found:
            found.append(code)
    return found


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 4 and word.endswith("s") else word


# words that say HOW something is measured, not WHAT: they cannot tie a dataset to a measure on their own
_GENERIC_MEASURE_WORDS = {
    "annual", "total", "share", "rate", "growth", "number", "value", "level", "index", "net", "inflow", "outflow",
    "current", "constant", "investment", "capacity", "percent", "per", "capita", "change", "average", "data",
    "statistic", "trend", "year", "people", "population", "million", "billion", "us$", "usd", "gdp", "sector",
    "private", "public", "domestic", "national", "country", "economy", "the", "and", "for", "from", "with", "into",
}


def subject_words(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z][a-z0-9]{2,}", str(text or "").casefold())} - _GENERIC_MEASURE_WORDS


def fits_measure(entry: "CatalogEntry", measure_text: str) -> bool:
    """True when the dataset shares a SUBJECT word (not a how-measured word) with the measure it is said to serve:
    "Share of electricity from solar" fits "installed solar capacity"; "Domestic credit to private sector" does not
    fit "investment in renewable energy projects"."""
    return bool(subject_words(f"{entry.title} {entry.keywords}") & subject_words(measure_text))


def keyword_plan(question: str, limit: int) -> list[dict]:
    words = {_stem(w) for w in re.findall(r"[a-z0-9&.]+", str(question or "").casefold())}
    scored = []
    for entry in CATALOG.values():
        kw = {_stem(w) for w in entry.keywords.split()}
        hits = len(words & kw)
        if hits >= 2 or (hits == 1 and len(kw & words & {"ev", "electric", "inflation", "unemployment", "vaccination",
                                                          "emission", "renewable", "solar", "internet", "tourism",
                                                          "semiconductor", "export", "import", "fdi", "poverty",
                                                          "remittance", "patent", "obesity", "literacy"}) == 1):
            scored.append((hits + (0.1 if entry.connector == "owid" else 0), entry.id))
    scored.sort(reverse=True)
    entities = mentioned_entities(question) or ["WLD"]
    return [{"id": i, "entities": entities[:4]} for _, i in scored[:limit]]


async def plan_datasets(question: str, gateway, *, today: date, limit: int = 3, measures: str = "") -> tuple[list[dict], dict]:
    """[{id, entities}] validated against the catalog; (plan, note)."""
    note = {"method": "llm", "rejected": []}
    catalog = "\n".join(f"{e.id} | {e.title} | {e.unit}" for e in CATALOG.values())
    plan: list[dict] = []
    data = None
    if gateway is not None:
        try:
            result = await gateway.generate(purpose="official_data_plan", section_id="plan", system=PLAN_SYSTEM,
                                            prompt=PLAN_PROMPT.format(question=question, today=today.isoformat(), catalog=catalog,
                                                                      measures=(f"MEASURES THE REPORT NEEDS (pick datasets that measure these):\n{measures}\n\n"
                                                                                if measures else ""),
                                                                      codes=", ".join(f"{k}={v}" for k, v in COUNTRIES.items()),
                                                                      limit=limit),
                                            max_tokens=1024, temperature=0.0, json_mode=True, timeout=60, retries=1)
            data = result.json() if getattr(result, "ok", False) else None
        except Exception as exc:  # the plan must never abort a report
            note["error"] = type(exc).__name__
    if isinstance(data, dict):
        for item in data.get("datasets") or []:
            if not isinstance(item, dict):
                continue
            entry_id = str(item.get("id") or "").strip()
            if entry_id not in CATALOG:
                note["rejected"].append({"id": entry_id, "reason": "not in the catalog"})
                continue
            entities = [str(e).strip().upper() for e in item.get("entities") or [] if str(e).strip().upper() in COUNTRIES][:5]
            if not entities:
                entities = mentioned_entities(question)[:4] or ["WLD"]
            measure = str(item.get("measure") or "")[:160]
            fit = "stand-in" if str(item.get("fit") or "").strip().casefold().startswith("stand") else "direct"
            # Stage N: the planner's word is checked against the subject words of the measure (or the question)
            if not fits_measure(CATALOG[entry_id], measure or measures or question):
                note["rejected"].append({"id": entry_id, "reason": f"does not measure the subject of '{measure or 'the question'}'"})
                continue
            plan.append({"id": entry_id, "entities": entities, "why": str(item.get("why") or "")[:200],
                         "measure": measure, "fit": fit})
    else:
        note["method"] = "keywords (planner unavailable)"
        plan = keyword_plan(question, limit)
    seen, out = set(), []
    for item in plan:
        if item["id"] not in seen:
            seen.add(item["id"])
            out.append(item)
    return out[:limit], note


# ── fetching ─────────────────────────────────────────────────────────────────────────────────────
def _years(start_year: int, today: date, *, allow_current: bool) -> list[int]:
    end = today.year if allow_current else today.year - 1
    return list(range(start_year, end + 1))


async def _fetch_wb(client, entry: CatalogEntry, entities: list[str], years: list[int]) -> dict[str, dict[int, float]]:
    url = f"https://api.worldbank.org/v2/country/{';'.join(entities)}/indicator/{entry.code}"
    resp = await client.get(url, params={"format": "json", "date": f"{years[0]}:{years[-1]}", "per_page": 1000})
    resp.raise_for_status()
    payload = resp.json()
    out: dict[str, dict[int, float]] = {}
    for row in (payload[1] if isinstance(payload, list) and len(payload) > 1 and payload[1] else []):
        if row.get("value") is None:
            continue
        code = str(row.get("countryiso3code") or "").upper() or ("WLD" if (row.get("country") or {}).get("id") == "1W" else "")
        code = code if code in COUNTRIES else ("EUU" if (row.get("country") or {}).get("id") == "EU" else code)
        if code in COUNTRIES:
            out.setdefault(COUNTRIES[code], {})[int(row["date"])] = float(row["value"])
    return out


async def _fetch_imf(client, entry: CatalogEntry, entities: list[str], years: list[int]) -> dict[str, dict[int, float]]:
    resp = await client.get(f"https://www.imf.org/external/datamapper/api/v1/{entry.code}/{'/'.join(entities)}")
    resp.raise_for_status()
    values = ((resp.json() or {}).get("values") or {}).get(entry.code) or {}
    imf_codes = {"WLD": "WEOWORLD", "EUU": "EU"}
    out: dict[str, dict[int, float]] = {}
    for code in entities:
        row = values.get(imf_codes.get(code, code)) or {}
        for year, value in row.items():
            if value is not None and int(year) in years:
                out.setdefault(COUNTRIES[code], {})[int(year)] = float(value)
    return out


async def _fetch_owid(client, entry: CatalogEntry, entities: list[str], years: list[int]) -> dict[str, dict[int, float]]:
    resp = await client.get(f"https://ourworldindata.org/grapher/{entry.code}.csv",
                            params={"v": "1", "csvType": "full", "useColumnShortNames": "false"})
    resp.raise_for_status()
    reader = csv.reader(io.StringIO(resp.text))
    header = next(reader, [])
    if len(header) < 4 or header[:3] != ["Entity", "Code", "Year"]:
        return {}
    wanted = {code: _OWID_ENTITY.get(code, COUNTRIES[code]) for code in entities}
    by_code = {v: k for k, v in wanted.items()}
    out: dict[str, dict[int, float]] = {}
    for row in reader:
        if len(row) < 4:
            continue
        entity, code, year = row[0], row[1].upper(), row[2]
        target = code if code in wanted else by_code.get(entity)
        if not target or not year.isdigit() or int(year) not in years or not row[3].strip():
            continue
        try:
            out.setdefault(COUNTRIES[target], {})[int(year)] = float(row[3])
        except ValueError:
            continue
    return out


def fred_annual_averages(csv_text: str, years: list[int]) -> dict[int, float]:
    """Annual means of a FRED CSV (``observation_date,CODE``; "." / empty = no value). A year counts only
    with at least 10 observations (a daily series) or 12 (monthly) - partial years are not averaged."""
    reader = csv.reader(io.StringIO(csv_text))
    header = next(reader, [])
    if len(header) < 2:
        return {}
    buckets: dict[int, list[float]] = {}
    for row in reader:
        if len(row) < 2 or not re.match(r"^\d{4}-\d{2}-\d{2}$", row[0]):
            continue
        try:
            value = float(row[1])
        except ValueError:
            continue
        year = int(row[0][:4])
        if year in years:
            buckets.setdefault(year, []).append(value)
    out = {}
    for year, values in buckets.items():
        if len(values) >= 12 and (len(values) >= 200 or len(values) == 12):
            out[year] = round(sum(values) / len(values), 2)
    return out


async def _fetch_fred(client, entry: CatalogEntry, entities: list[str], years: list[int]) -> dict[str, dict[int, float]]:
    resp = await client.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": entry.code})
    resp.raise_for_status()
    values = fred_annual_averages(resp.text, years)
    return {_FRED_ENTITY.get(entry.id, "United States"): values} if values else {}


async def fetch_dataset(client, item: dict, *, start_year: int, today: date) -> Dataset | None:
    entry = CATALOG[item["id"]]
    years = _years(start_year, today, allow_current=entry.connector not in {"imf", "fred"})
    if len(years) < 2:
        return None
    fetcher = {"wb": _fetch_wb, "imf": _fetch_imf, "owid": _fetch_owid, "fred": _fetch_fred}[entry.connector]
    raw = None
    for attempt in (1, 2):
        try:
            raw = await fetcher(client, entry, item["entities"], years)
            break
        except Exception as exc:
            log.warning("Official data: %s fetch failed (%s, attempt %d)", entry.id, type(exc).__name__, attempt)
    if raw is None:
        return None
    raw = {name: vals for name, vals in raw.items() if len(vals) >= 2}
    if not raw:
        return None
    observed = sorted({y for vals in raw.values() for y in vals})
    observed = [y for y in years if y in observed]
    if len(observed) < 3:
        return None
    series = {name: [vals.get(y) for y in observed] for name, vals in raw.items()}
    ds = Dataset(entry=entry, years=observed, series=series, retrieved=today.isoformat(), latest=observed[-1])
    return scale_dataset(ds)


def scale_dataset(ds: Dataset) -> Dataset:
    """Very large values are shown in billions / millions (232,906,639,404 US$ -> 232.91 US$ billion), so
    tables and chart labels stay readable; the unit says so."""
    peak = max((abs(v) for vals in ds.series.values() for v in vals if v is not None), default=0)
    unit = ds.entry.unit
    if peak >= 1e9:
        factor, word = 1e9, "billion"
    elif peak >= 1e7:
        factor, word = 1e6, "million"
    else:
        return ds
    from dataclasses import replace
    new_unit = (unit.replace("US$", f"US$ {word}") if "US$" in unit else f"{unit}, {word}" if unit else word)
    ds.entry = replace(ds.entry, unit=new_unit)
    ds.series = {n: [None if v is None else round(v / factor, 2) for v in vals] for n, vals in ds.series.items()}
    ds.notes.append(f"values divided by {factor:,.0f}")
    return ds


def _fmt(value: float | None, unit: str) -> str:
    if value is None:
        return ""
    if abs(value) >= 1e9:
        return f"{value:,.0f}"
    if abs(value) >= 100:
        return f"{value:,.1f}".rstrip("0").rstrip(".")
    return f"{value:.2f}"


def as_source(ds: Dataset) -> dict:
    e = ds.entry
    names = list(ds.series)
    header = "Year | " + " | ".join(names)
    rows = [f"{y} | " + " | ".join(_fmt(ds.series[n][i], e.unit) for n in names) for i, y in enumerate(ds.years)]
    title = f"{e.publisher}: {e.title} ({e.unit}), {ds.years[0]}-{ds.years[-1]}"
    fit_line = []
    if ds.fit == "stand-in" and ds.measure:
        fit_line = [f"NOTE — context indicator for \"{ds.measure}\": this series measures {e.title.lower()}, "
                    f"not {ds.measure} itself."]
    text = "\n".join([
        f"OFFICIAL DATASET — {e.publisher}, {e.collection}: {e.title} ({e.unit}) [{e.code}]",
        f"Retrieved from the {e.publisher} public API on {ds.retrieved}; the same data is published at {e.page_url}. "
        f"Latest observation: {ds.latest}. Empty cells are years with no published value.",
        *fit_line,
        f"TABLE 1 — {e.title} ({e.unit}), {ds.years[0]}–{ds.years[-1]}",
        header, *rows,
    ])
    return {
        "title": title, "url": e.page_url, "publisher": e.publisher, "published": ds.retrieved,  # retrieval date
        "snippet": f"{e.title} ({e.unit}) for {', '.join(names)}, {ds.years[0]}-{ds.years[-1]} ({e.publisher}).",
        "fullContent": text, "pageContent": text, "pageFetchStatus": "ok", "pageFetchHttpStatus": 200,
        "evidenceOrigin": "publisher_page", "sourceType": "Official dataset", "kind": "Official dataset",
        "authorityTier": "primary_official", "reliabilityScore": 0.95,
        "reliabilityNote": f"primary official ({e.publisher} {e.collection}, retrieved via the publisher's API)",
        "officialDataset": {"id": e.id, "code": e.code, "unit": e.unit, "entities": names, "years": ds.years,
                            "fit": ds.fit, "measure": ds.measure},
    }


async def official_sources(question: str, gateway, *, today: date, tier: str, measures: str = "") -> tuple[list[dict], dict]:
    """Plan, fetch and format; never raises. (source rows, capture note)."""
    import httpx
    limit = 5 if tier == "in_depth" else 3
    note: dict = {"plan": [], "fetched": [], "skipped": []}
    try:
        from utils.source_quality import window_start
        start_year = window_start(question, today).year
    except Exception:
        start_year = today.year - 3
    start_year = min(start_year, today.year - 2)  # at least 3 whole years of context for an annual series
    plan, plan_note = await plan_datasets(question, gateway, today=today, limit=limit, measures=measures)
    note["plan"], note["planNote"] = plan, plan_note
    rows: list[dict] = []
    async with httpx.AsyncClient(timeout=45, follow_redirects=True,
                                 headers={"User-Agent": "GrowthGradual-Research/1.0 (official open data)"}) as client:
        import asyncio
        results = await asyncio.gather(*[fetch_dataset(client, item, start_year=start_year, today=today) for item in plan])
    for item, ds in zip(plan, results):
        if ds is None:
            note["skipped"].append({"id": item["id"], "reason": "no data for the window / entities"})
            continue
        ds.fit, ds.measure = item.get("fit", "direct"), item.get("measure", "")
        rows.append(as_source(ds))
        note["fetched"].append({"id": item["id"], "entities": list(ds.series), "years": [ds.years[0], ds.years[-1]]})
    log.info("Official data: %d planned, %d fetched (%s)", len(plan), len(rows), ", ".join(r["officialDataset"]["id"] for r in rows))
    return rows, note


# ── data-first charts from official datasets in the evidence store ───────────────────────────────
OFFICIAL_PUBLISHERS = ("World Bank", "International Monetary Fund", "Our World in Data", "FRED (Federal Reserve Bank of St. Louis)")


def is_official_passage(passage) -> bool:
    return any(str(getattr(passage, "title", "") or "").startswith(f"{p}: ") for p in OFFICIAL_PUBLISHERS)


def official_charts(store, question: str = "") -> list:
    """One data-first chart per official dataset table (years where every entity has a value; >= 3)."""
    from utils.dataset_extractor import to_chart
    charts = []
    for passage in store.passages:
        if passage.kind != "table" or not is_official_passage(passage):
            continue
        lines = [ln.strip() for ln in str(passage.text or "").splitlines() if " | " in ln]
        if len(lines) < 4 or not lines[0].startswith("Year |"):
            continue
        names = [c.strip() for c in lines[0].split("|")[1:]]
        rows = [[c.strip() for c in ln.split("|")] for ln in lines[1:]]
        rows = [r for r in rows if len(r) == len(names) + 1 and r[0].isdigit() and all(r[1:])]
        if len(rows) < 3:
            continue
        heading = next((ln for ln in str(passage.text).splitlines() if ln.startswith("TABLE")), "")
        m = re.match(r"TABLE \d+\s*[—-]\s*(.+?)\s*\(([^)]+)\),", heading)
        metric, unit = (m.group(1), m.group(2)) if m else (passage.title, "")

        def num(cell: str) -> float:
            return float(cell.replace(",", ""))
        series = [{"name": name, "points": [{"label": r[0], "value": num(r[i + 1]), "raw": r[i + 1]} for r in rows]}
                  for i, name in enumerate(names)]
        # absolute quantities on very different scales (China 13.3M vs India 170k) hide the smaller series:
        # keep the entities the question is about; rates, shares and per-capita values stay comparable
        relative = re.search(r"%|per |rate|share|ratio|index|years", unit, re.I)
        peaks = [max(abs(p["value"]) for p in s["points"]) or 1 for s in series]
        if len(series) > 1 and not relative and max(peaks) / max(min(peaks), 1e-9) > 10:
            focus = {COUNTRIES[c] for c in mentioned_entities(question)} if question else set()
            kept = [s for s in series if s["name"] in focus] or series[:1]
            series = kept
        ds = {"metric": metric, "unit": unit, "x": "time", "period": f"{rows[0][0]}-{rows[-1][0]}",
              "series": series, "forecast": False}
        chart = to_chart(ds, passage, passage.title, 500 + len(charts))
        chart.chart_id = f"o{len(charts) + 1}"
        chart.spec["provenance"] = "official dataset"
        chart.spec["basisNote"] = f"Official dataset: {passage.title}; values copied from passage {passage.passage_id}."
        stand_in = re.search(r'NOTE — context indicator for "([^"]+)"', str(passage.text or ""))
        if stand_in:  # Stage N: a stand-in says so on the chart
            chart.spec["fit"] = "stand-in"
            chart.spec["basisNote"] = (f"Context indicator, not a direct measure of {stand_in.group(1)}. "
                                       + chart.spec["basisNote"])
        charts.append(chart)
    return charts
