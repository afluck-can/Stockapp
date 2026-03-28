"""
Lightweight Yahoo Finance fetcher — no pandas/yfinance required.
Uses the public quoteSummary API with just the `requests` library.
"""
from __future__ import annotations
import requests

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}


def _raw(d: dict, key: str):
    """Extract .raw value from a Yahoo Finance typed field, or the value itself."""
    v = d.get(key)
    if isinstance(v, dict):
        return v.get("raw")
    return v


def fetch_info(ticker: str, timeout: int = 8) -> dict | None:
    """Return a flat dict of fundamentals for *ticker*, or None on failure."""
    try:
        url = (
            f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
        )
        params = {
            "modules": (
                "price,defaultKeyStatistics,financialData,"
                "summaryDetail,assetProfile"
            )
        }
        r = requests.get(url, params=params, headers=_HEADERS, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        result = data.get("quoteSummary", {}).get("result") or []
        if not result:
            return None
        block = result[0]

        price   = block.get("price", {})
        kstats  = block.get("defaultKeyStatistics", {})
        fin     = block.get("financialData", {})
        summary = block.get("summaryDetail", {})
        profile = block.get("assetProfile", {})

        return {
            "longName":                      price.get("longName"),
            "shortName":                     price.get("shortName"),
            "sector":                        profile.get("sector"),
            "industry":                      profile.get("industry"),
            "quoteType":                     price.get("quoteType"),
            "currentPrice":                  _raw(fin, "currentPrice"),
            "regularMarketPrice":            _raw(price, "regularMarketPrice"),
            "fiftyTwoWeekHigh":              _raw(summary, "fiftyTwoWeekHigh"),
            "fiftyTwoWeekLow":               _raw(summary, "fiftyTwoWeekLow"),
            "marketCap":                     _raw(price, "marketCap"),
            "forwardPE":                     _raw(summary, "forwardPE"),
            "trailingPE":                    _raw(summary, "trailingPE"),
            "priceToBook":                   _raw(kstats, "priceToBook"),
            "priceToSalesTrailing12Months":  _raw(summary, "priceToSalesTrailing12Months"),
            "earningsGrowth":                _raw(fin, "earningsGrowth"),
            "revenueGrowth":                 _raw(fin, "revenueGrowth"),
            "forwardEps":                    _raw(kstats, "forwardEps"),
            "trailingEps":                   _raw(kstats, "trailingEps"),
            "returnOnEquity":                _raw(fin, "returnOnEquity"),
            "profitMargins":                 _raw(fin, "profitMargins"),
            "grossMargins":                  _raw(fin, "grossMargins"),
            "debtToEquity":                  _raw(fin, "debtToEquity"),
            "averageVolume":                 _raw(summary, "averageVolume"),
            "dividendYield":                 _raw(summary, "dividendYield"),
            "currency":                      price.get("currency", "USD"),
            "exchange":                      price.get("exchangeName", "NASDAQ"),
        }
    except Exception:
        return None
