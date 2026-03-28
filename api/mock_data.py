"""
Mock stock data for demonstration / offline use.
Generates plausible but synthetic fundamentals for a set of well-known NASDAQ stocks.
When yfinance is reachable in production, this module is never called.
"""

from __future__ import annotations
import random
from typing import Any

# Deterministic seed per ticker so the data is stable across restarts
def _rng(ticker: str) -> random.Random:
    seed = sum(ord(c) * (i + 1) for i, c in enumerate(ticker))
    return random.Random(seed)


SECTORS = {
    "AAPL": ("Technology", "Consumer Electronics"),
    "MSFT": ("Technology", "Software—Infrastructure"),
    "NVDA": ("Technology", "Semiconductors"),
    "META": ("Communication Services", "Internet Content & Information"),
    "GOOGL": ("Communication Services", "Internet Content & Information"),
    "AMZN": ("Consumer Cyclical", "Internet Retail"),
    "TSLA": ("Consumer Cyclical", "Auto Manufacturers"),
    "AVGO": ("Technology", "Semiconductors"),
    "ORCL": ("Technology", "Software—Infrastructure"),
    "AMD": ("Technology", "Semiconductors"),
    "INTC": ("Technology", "Semiconductors"),
    "QCOM": ("Technology", "Semiconductors"),
    "ADBE": ("Technology", "Software—Application"),
    "CRM": ("Technology", "Software—Application"),
    "NOW": ("Technology", "Software—Application"),
    "NFLX": ("Communication Services", "Entertainment"),
    "COST": ("Consumer Defensive", "Discount Stores"),
    "PYPL": ("Financial Services", "Credit Services"),
    "SQ": ("Financial Services", "Software—Infrastructure"),
    "DDOG": ("Technology", "Software—Application"),
    "SNOW": ("Technology", "Software—Infrastructure"),
    "CRWD": ("Technology", "Software—Infrastructure"),
    "PANW": ("Technology", "Software—Infrastructure"),
    "ZS": ("Technology", "Software—Infrastructure"),
    "NET": ("Technology", "Software—Infrastructure"),
    "AMGN": ("Healthcare", "Drug Manufacturers"),
    "GILD": ("Healthcare", "Drug Manufacturers"),
    "BIIB": ("Healthcare", "Drug Manufacturers"),
    "VRTX": ("Healthcare", "Drug Manufacturers"),
    "IDXX": ("Healthcare", "Diagnostics & Research"),
    "ISRG": ("Healthcare", "Medical Devices"),
    "SBUX": ("Consumer Cyclical", "Restaurants"),
    "MNST": ("Consumer Defensive", "Beverages—Non-Alcoholic"),
    "LULU": ("Consumer Cyclical", "Apparel Retail"),
    "FAST": ("Industrials", "Industrial Distribution"),
    "PAYX": ("Industrials", "Staffing & Employment"),
    "ADP": ("Technology", "Software—Application"),
    "CSCO": ("Technology", "Communication Equipment"),
    "MRVL": ("Technology", "Semiconductors"),
    "AMAT": ("Technology", "Semiconductor Equipment"),
    "LRCX": ("Technology", "Semiconductor Equipment"),
    "KLAC": ("Technology", "Semiconductor Equipment"),
    "CDNS": ("Technology", "Software—Application"),
    "SNPS": ("Technology", "Software—Application"),
    "ANSS": ("Technology", "Software—Application"),
    "PLTR": ("Technology", "Software—Infrastructure"),
    "TTD": ("Technology", "Software—Application"),
    "ENPH": ("Technology", "Solar"),
    "FSLR": ("Technology", "Solar"),
    "RIVN": ("Consumer Cyclical", "Auto Manufacturers"),
    "LCID": ("Consumer Cyclical", "Auto Manufacturers"),
    "MRNA": ("Healthcare", "Biotechnology"),
    "BNTX": ("Healthcare", "Biotechnology"),
    "HOOD": ("Financial Services", "Capital Markets"),
    "MELI": ("Consumer Cyclical", "Internet Retail"),
    "SPOT": ("Communication Services", "Entertainment"),
    "ABNB": ("Consumer Cyclical", "Travel Services"),
    "BKNG": ("Consumer Cyclical", "Travel Services"),
}

NAMES = {
    "AAPL": "Apple Inc.", "MSFT": "Microsoft Corporation", "NVDA": "NVIDIA Corporation",
    "META": "Meta Platforms Inc.", "GOOGL": "Alphabet Inc.", "AMZN": "Amazon.com Inc.",
    "TSLA": "Tesla Inc.", "AVGO": "Broadcom Inc.", "ORCL": "Oracle Corporation",
    "AMD": "Advanced Micro Devices", "INTC": "Intel Corporation", "QCOM": "Qualcomm Inc.",
    "ADBE": "Adobe Inc.", "CRM": "Salesforce Inc.", "NOW": "ServiceNow Inc.",
    "NFLX": "Netflix Inc.", "COST": "Costco Wholesale", "PYPL": "PayPal Holdings",
    "SQ": "Block Inc.", "DDOG": "Datadog Inc.", "SNOW": "Snowflake Inc.",
    "CRWD": "CrowdStrike Holdings", "PANW": "Palo Alto Networks", "ZS": "Zscaler Inc.",
    "NET": "Cloudflare Inc.", "AMGN": "Amgen Inc.", "GILD": "Gilead Sciences",
    "BIIB": "Biogen Inc.", "VRTX": "Vertex Pharmaceuticals", "IDXX": "IDEXX Laboratories",
    "ISRG": "Intuitive Surgical", "SBUX": "Starbucks Corporation", "MNST": "Monster Beverage",
    "LULU": "Lululemon Athletica", "FAST": "Fastenal Company", "PAYX": "Paychex Inc.",
    "ADP": "Automatic Data Processing", "CSCO": "Cisco Systems", "MRVL": "Marvell Technology",
    "AMAT": "Applied Materials", "LRCX": "Lam Research", "KLAC": "KLA Corporation",
    "CDNS": "Cadence Design Systems", "SNPS": "Synopsys Inc.", "ANSS": "ANSYS Inc.",
    "PLTR": "Palantir Technologies", "TTD": "The Trade Desk", "ENPH": "Enphase Energy",
    "FSLR": "First Solar Inc.", "RIVN": "Rivian Automotive", "LCID": "Lucid Group",
    "MRNA": "Moderna Inc.", "BNTX": "BioNTech SE", "HOOD": "Robinhood Markets",
    "MELI": "MercadoLibre Inc.", "SPOT": "Spotify Technology", "ABNB": "Airbnb Inc.",
    "BKNG": "Booking Holdings",
}


def _rand_float(rng: random.Random, lo: float, hi: float, decimals: int = 2) -> float:
    return round(rng.uniform(lo, hi), decimals)


def mock_info(ticker: str) -> dict[str, Any]:
    """Generate deterministic, plausible mock fundamentals for a ticker."""
    rng = _rng(ticker)
    sector, industry = SECTORS.get(ticker, ("Technology", "Software—Application"))
    name = NAMES.get(ticker, ticker + " Inc.")

    # Base price
    price = _rand_float(rng, 20, 800)
    low52 = price * _rand_float(rng, 0.50, 0.85)
    high52 = price * _rand_float(rng, 1.05, 1.80)

    # Valuation (some stocks are cheap, some expensive)
    pe = _rand_float(rng, 8, 80)
    pb = _rand_float(rng, 0.8, 20)
    ps = _rand_float(rng, 0.5, 15)

    # Growth
    eps_growth = _rand_float(rng, -0.15, 0.65)
    rev_growth = _rand_float(rng, -0.05, 0.45)
    trailing_eps = _rand_float(rng, 0.5, 25)
    fwd_eps = trailing_eps * (1 + _rand_float(rng, -0.10, 0.40))

    # Quality
    roe = _rand_float(rng, -0.05, 0.80)
    margin = _rand_float(rng, -0.02, 0.55)
    debt_equity = _rand_float(rng, 0, 300)   # yfinance returns as %, e.g. 45.2

    # Market cap (make it correlated with known tickers)
    if ticker in {"AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META"}:
        mktcap = int(rng.uniform(1e12, 3e12))
    elif ticker in {"TSLA", "AVGO", "ORCL", "AMD", "ADBE", "CRM", "NFLX", "COST"}:
        mktcap = int(rng.uniform(1e11, 8e11))
    else:
        mktcap = int(rng.uniform(5e9, 5e10))

    return {
        "longName": name,
        "sector": sector,
        "industry": industry,
        "quoteType": "EQUITY",
        "currentPrice": price,
        "regularMarketPrice": price,
        "fiftyTwoWeekHigh": round(high52, 2),
        "fiftyTwoWeekLow": round(low52, 2),
        "marketCap": mktcap,
        "forwardPE": pe,
        "trailingPE": pe * _rand_float(rng, 0.9, 1.3),
        "priceToBook": pb,
        "priceToSalesTrailing12Months": ps,
        "earningsGrowth": eps_growth,
        "revenueGrowth": rev_growth,
        "forwardEps": round(fwd_eps, 2),
        "trailingEps": round(trailing_eps, 2),
        "returnOnEquity": roe,
        "profitMargins": margin,
        "grossMargins": margin + _rand_float(rng, 0.05, 0.30),
        "debtToEquity": debt_equity,
        "averageVolume": int(rng.uniform(1e6, 50e6)),
        "dividendYield": _rand_float(rng, 0, 0.025) if rng.random() > 0.6 else None,
        "currency": "USD",
        "exchange": "NASDAQ",
    }


# Tickers for which we have mock data
MOCK_TICKERS: list[str] = list(SECTORS.keys())
