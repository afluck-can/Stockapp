"""
NASDAQ Ticker List
==================
Provides a curated list of actively traded NASDAQ stocks,
organized by sector, covering large-, mid-, and small-caps.

The list can be refreshed from NASDAQ's screener API.
"""

from __future__ import annotations
import requests
import logging

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Curated list of ~500 actively traded NASDAQ stocks across all sectors.
# Used as a fallback when the live NASDAQ screener is unavailable.
# ──────────────────────────────────────────────────────────────────────────────
CURATED_NASDAQ = [
    # Technology — Mega Cap
    "AAPL", "MSFT", "NVDA", "META", "GOOGL", "GOOG", "AMZN", "TSLA",
    "AVGO", "ORCL", "AMD", "INTC", "QCOM", "TXN", "MU", "AMAT",
    "LRCX", "KLAC", "MRVL", "NXPI", "ON", "MCHP", "ADI", "SWKS",
    "QRVO", "MPWR", "ENTG", "CREE", "MKSI", "COHR",
    # Software / Cloud
    "ADBE", "CRM", "NOW", "WDAY", "TEAM", "ZM", "SNOW", "DDOG",
    "NET", "CRWD", "PANW", "FTNT", "ZS", "OKTA", "MDB", "ESTC",
    "CFLT", "GTLB", "HUBS", "BILL", "TTD", "TRADE", "PUBM", "MGNI",
    "APPS", "APP", "APPN", "SMAR", "NCNO", "COUP", "VEEV", "ANSS",
    "CDNS", "SNPS", "MANH", "PCOR", "TYL", "BL", "FRPT", "SPSC",
    # Internet / E-Commerce
    "EBAY", "ETSY", "CHWY", "WISH", "PINS", "SNAP", "TWTR", "MTCH",
    "IAC", "ANGI", "CARG", "TDC", "CARS", "ABNB", "BKNG", "EXPE",
    "TRIP", "LMND", "OPEN", "RDFN", "ZILLOW", "Z", "ZG",
    # Semiconductors
    "ASML", "ARM", "SMCI", "WOLF", "RMBS", "FORM", "POWI", "DIOD",
    "SLAB", "ALGM", "AMBA", "CEVA", "MTSI", "AXTI", "IMOS",
    # Biotech / Pharma
    "AMGN", "GILD", "BIIB", "VRTX", "REGN", "ILMN", "IDXX", "ISRG",
    "ALGN", "DXCM", "HOLX", "HSIC", "PDCO", "XRAY", "ABMD", "CTLT",
    "BMRN", "EXAS", "NTRA", "PACB", "NVAX", "BNTX", "MRNA",
    "INCY", "ALXN", "SGEN", "HZNP", "ARRY", "FOLD", "ACAD",
    "IONS", "SRPT", "BLUE", "EDIT", "CRSP", "NTLA", "BEAM",
    "FATE", "RCUS", "KYMR", "ARQT", "RXRX", "CDNA", "SHPG",
    "ALNY", "RARE", "PTCT", "ACMR", "KDMN",
    # Healthcare
    "VRSN", "HSIC", "IDXX", "PODD", "TNDM", "INSP", "OFIX",
    "MMSI", "LMAT", "NVCR", "MDXG", "ADUS", "ACET",
    # Financial
    "PYPL", "SQ", "AFRM", "UPST", "SOFI", "NRDS", "LC",
    "MKTX", "IBKR", "LPLA", "SEIC", "FFIV", "NDAQ",
    "CBOE", "MORN", "VRSK", "MSCI", "FDS",
    # Consumer
    "SBUX", "MNST", "MDLZ", "KHC", "COST", "WBA", "DLTR",
    "DOLA", "FIVE", "OLLI", "BJ", "PDD", "JD", "BABA",
    "NKE", "LULU", "UA", "UAA", "COLM", "SKX", "CROX",
    "DECK", "HBI", "PVH", "RL", "TPR", "FOSL",
    "NFLX", "ROKU", "FUBO", "PARA", "WBD", "SPOT",
    "SIRI", "LSXMK", "LSXMA", "CHTR", "CMCSA",
    # Industrial / Clean Energy
    "ENPH", "SEDG", "FSLR", "SPWR", "RUN", "NOVA", "ARRY",
    "STEM", "NRGV", "BEEM", "AMRC", "GPRE", "REX",
    # Retail / Restaurant
    "TSCO", "ULTA", "ORLY", "AZO", "BBWI", "CASY",
    "WING", "SHAK", "BLMN", "JACK", "TXRH", "EAT",
    # Travel / Leisure
    "MAR", "HLT", "WYNN", "MLCO", "PENN", "RSI",
    # Industrials
    "FAST", "POOL", "AIRC", "RGEN", "NDSN", "AAON",
    "ADTN", "NTGR", "VIAV", "CIEN", "CALX", "INFN",
    # Data / Analytics
    "PLTR", "AI", "BBAI", "SOUN", "GFAI", "EXAI",
    # Electric Vehicles
    "RIVN", "LCID", "FSR", "GOEV", "NKLA", "XL", "RIDE",
    # Financials
    "HOOD", "FUTU", "TIGR", "MQ", "PAYA", "EVTC",
    "CLOV", "HIMS", "ACRS", "TRTX",
    # Additional Large Caps
    "CSCO", "NTES", "BIDU", "NDAQ", "PAYX", "ADP",
    "CTSH", "INFY", "WIT", "EPAM", "GLOB", "TERN",
    "PAGS", "STNE", "MELI", "DESP", "VTEX",
]

# De-duplicate while preserving order
_seen: set[str] = set()
CURATED_NASDAQ_DEDUPED: list[str] = []
for _t in CURATED_NASDAQ:
    if _t not in _seen:
        _seen.add(_t)
        CURATED_NASDAQ_DEDUPED.append(_t)

CURATED_NASDAQ = CURATED_NASDAQ_DEDUPED


def fetch_nasdaq_tickers(limit: int = 500) -> list[str]:
    """
    Try to fetch the live NASDAQ screener list.
    Falls back to the curated list on any error.
    Returns up to `limit` tickers.
    """
    try:
        url = (
            "https://api.nasdaq.com/api/screener/stocks"
            "?tableonly=true&limit=5000&exchange=NASDAQ"
        )
        headers = {"User-Agent": "Mozilla/5.0 (compatible; StockApp/1.0)"}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("data", {}).get("table", {}).get("rows", [])
        tickers = [r["symbol"].strip() for r in rows if r.get("symbol")]
        # Remove anything with '/' (warrants / preferred shares)
        tickers = [t for t in tickers if "/" not in t and "^" not in t]
        logger.info("Fetched %d NASDAQ tickers from live API", len(tickers))
        return tickers[:limit]
    except Exception as exc:
        logger.warning("Live NASDAQ fetch failed (%s), using curated list", exc)
        return CURATED_NASDAQ[:limit]
