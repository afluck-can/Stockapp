"""
Vercel serverless handler — Flask WSGI app.
- No network calls at startup (avoids cold-start crashes)
- Always demo mode; live Yahoo Finance attempted per-request
- HTML served from api/static/ (guaranteed in Vercel bundle)
"""

from __future__ import annotations
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

import scorer
import mock_data as mock_mod

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
app = Flask(__name__, static_folder=str(STATIC_DIR))
CORS(app)

_CACHE: dict[str, dict] = {}
_CACHE_TTL = 6 * 3600
MAX_WORKERS = 10


def _cache_get(ticker: str) -> dict | None:
    e = _CACHE.get(ticker)
    if e and (time.time() - e.get("_ts", 0)) < _CACHE_TTL:
        return {k: v for k, v in e.items() if k != "_ts"}
    return None

def _cache_set(ticker: str, data: dict) -> None:
    _CACHE[ticker] = {**data, "_ts": time.time()}


def _try_live(ticker: str) -> dict | None:
    """Attempt a live Yahoo Finance lookup. Returns None on any failure."""
    try:
        import requests
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        url = (f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
               "?modules=price,defaultKeyStatistics,financialData,summaryDetail,assetProfile")
        resp = requests.get(url, headers=headers, timeout=6)
        if resp.status_code != 200:
            return None
        result = resp.json().get("quoteSummary", {}).get("result") or []
        if not result:
            return None
        b = result[0]
        price   = b.get("price", {})
        kstats  = b.get("defaultKeyStatistics", {})
        fin     = b.get("financialData", {})
        summary = b.get("summaryDetail", {})
        profile = b.get("assetProfile", {})
        def raw(d, k):
            v = d.get(k)
            return v.get("raw") if isinstance(v, dict) else v
        return {
            "longName": price.get("longName"), "shortName": price.get("shortName"),
            "sector": profile.get("sector"), "industry": profile.get("industry"),
            "quoteType": price.get("quoteType"),
            "currentPrice": raw(fin, "currentPrice"),
            "regularMarketPrice": raw(price, "regularMarketPrice"),
            "fiftyTwoWeekHigh": raw(summary, "fiftyTwoWeekHigh"),
            "fiftyTwoWeekLow": raw(summary, "fiftyTwoWeekLow"),
            "marketCap": raw(price, "marketCap"),
            "forwardPE": raw(summary, "forwardPE"),
            "trailingPE": raw(summary, "trailingPE"),
            "priceToBook": raw(kstats, "priceToBook"),
            "priceToSalesTrailing12Months": raw(summary, "priceToSalesTrailing12Months"),
            "earningsGrowth": raw(fin, "earningsGrowth"),
            "revenueGrowth": raw(fin, "revenueGrowth"),
            "forwardEps": raw(kstats, "forwardEps"),
            "trailingEps": raw(kstats, "trailingEps"),
            "returnOnEquity": raw(fin, "returnOnEquity"),
            "profitMargins": raw(fin, "profitMargins"),
            "grossMargins": raw(fin, "grossMargins"),
            "debtToEquity": raw(fin, "debtToEquity"),
            "averageVolume": raw(summary, "averageVolume"),
            "dividendYield": raw(summary, "dividendYield"),
            "currency": price.get("currency", "USD"),
            "exchange": price.get("exchangeName", "NASDAQ"),
        }
    except Exception:
        return None


def _fetch_and_score(ticker: str) -> dict[str, Any] | None:
    cached = _cache_get(ticker)
    if cached:
        return cached

    live_info = _try_live(ticker)
    if live_info:
        info = live_info
        is_demo = False
    else:
        info = mock_mod.mock_info(ticker)
        is_demo = True

    price = info.get("currentPrice") or info.get("regularMarketPrice")
    if not price:
        return None

    scores = scorer.score_stock(info)
    result: dict[str, Any] = {
        "ticker":              ticker,
        "name":                info.get("longName") or info.get("shortName", ticker),
        "sector":              info.get("sector") or "Unknown",
        "industry":            info.get("industry") or "Unknown",
        "market_cap":          info.get("marketCap"),
        "price":               price,
        "currency":            info.get("currency", "USD"),
        "exchange":            info.get("exchange", "NASDAQ"),
        "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
        "fifty_two_week_low":  info.get("fiftyTwoWeekLow"),
        "avg_volume":          info.get("averageVolume"),
        "dividend_yield":      info.get("dividendYield"),
        "demo_mode":           is_demo,
        **scores,
    }
    _cache_set(ticker, result)
    return result


# ── Routes ────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(str(STATIC_DIR), "index.html")


@app.route("/api/status")
def status():
    return jsonify({"demo_mode": True, "message": "Running — demo data by default, live data attempted per ticker."})


@app.route("/api/criteria")
def criteria():
    return jsonify({
        "title": "Stock Picking Scoring Methodology",
        "description": "Each stock is scored 0–100 across four equally-weighted pillars.",
        "pillars": [
            {"name": "Value", "max_points": 25, "description": "Is the stock priced cheaply relative to its fundamentals?",
             "metrics": [
                 {"metric": "Forward P/E",        "max": 10, "ideal": "< 15",  "bad": "> 50",  "rationale": "Lower P/E = paying less per dollar of earnings."},
                 {"metric": "Price-to-Book (P/B)", "max": 8,  "ideal": "< 1.5", "bad": "> 5",   "rationale": "P/B < 1 means buying assets below book value."},
                 {"metric": "Price-to-Sales (P/S)","max": 7,  "ideal": "< 1",   "bad": "> 6",   "rationale": "Low P/S stocks are often undervalued vs revenue."},
             ]},
            {"name": "Growth", "max_points": 25, "description": "Is the business growing earnings and revenue meaningfully?",
             "metrics": [
                 {"metric": "EPS Growth (YoY)",            "max": 10, "ideal": "> 50%", "bad": "< 0%",        "rationale": "Accelerating earnings growth drives share appreciation."},
                 {"metric": "Revenue Growth (YoY)",        "max": 8,  "ideal": "> 30%", "bad": "< 0%",        "rationale": "Top-line growth is the foundation of long-term value."},
                 {"metric": "Forward EPS vs Trailing EPS", "max": 7,  "ideal": "> 30%", "bad": "Flat/declining","rationale": "Analyst consensus signals market confidence."},
             ]},
            {"name": "Quality", "max_points": 25, "description": "Is the business high-quality with a strong balance sheet?",
             "metrics": [
                 {"metric": "Return on Equity (ROE)", "max": 10, "ideal": "> 30%", "bad": "< 0%",  "rationale": "High ROE = efficient use of shareholder capital."},
                 {"metric": "Profit Margin",          "max": 8,  "ideal": "> 30%", "bad": "< 0%",  "rationale": "Wide margins indicate pricing power & durability."},
                 {"metric": "Debt / Equity",          "max": 7,  "ideal": "< 0.3", "bad": "> 2.0", "rationale": "Low leverage reduces risk and preserves flexibility."},
             ]},
            {"name": "Momentum", "max_points": 25, "description": "Is price action confirming the fundamental thesis?",
             "metrics": [
                 {"metric": "52-Week Return (vs low)", "max": 15, "ideal": "> 100%",   "bad": "Near low",  "rationale": "Strong upward trends often persist medium-term."},
                 {"metric": "52-Week High Proximity",  "max": 10, "ideal": "Near high", "bad": "Near low",  "rationale": "Stocks near their high exhibit relative strength."},
             ]},
        ],
        "signals": [
            {"range": "80–100", "label": "STRONG BUY", "color": "green"},
            {"range": "65–79",  "label": "BUY",        "color": "lime"},
            {"range": "50–64",  "label": "WATCH",      "color": "yellow"},
            {"range": "35–49",  "label": "AVOID",      "color": "orange"},
            {"range": "0–34",   "label": "SKIP",       "color": "red"},
        ],
    })


@app.route("/api/scan")
def scan():
    page     = max(1, int(request.args.get("page", 1)))
    per_page = min(100, max(1, int(request.args.get("per_page", 25))))
    sector_f = request.args.get("sector", "").strip().lower()
    min_sc   = float(request.args.get("min_score", 0))
    max_sc   = float(request.args.get("max_score", 100))
    signal_f = request.args.get("signal", "").strip().upper()
    sort_by  = request.args.get("sort_by", "composite_score")
    sort_dir = request.args.get("sort_dir", "desc")
    custom   = request.args.get("tickers", "").strip()
    force    = request.args.get("refresh", "false").lower() == "true"

    ticker_list = (
        [t.strip().upper() for t in custom.split(",") if t.strip()]
        if custom else mock_mod.MOCK_TICKERS
    )
    if force:
        for t in ticker_list:
            _CACHE.pop(t, None)

    results: list[dict] = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {pool.submit(_fetch_and_score, t): t for t in ticker_list}
        for fut in as_completed(futs):
            r = fut.result()
            if r:
                results.append(r)
    elapsed = round(time.time() - t0, 2)

    if sector_f:
        results = [r for r in results if sector_f in (r.get("sector") or "").lower()]
    results = [r for r in results if min_sc <= r["composite_score"] <= max_sc]
    if signal_f:
        results = [r for r in results if r.get("signal") == signal_f]

    reverse = sort_dir != "asc"
    results.sort(key=lambda r: r.get(sort_by) if r.get(sort_by) is not None
                 else (-math.inf if reverse else math.inf), reverse=reverse)

    total       = len(results)
    total_pages = max(1, math.ceil(total / per_page))
    start       = (page - 1) * per_page

    return jsonify({
        "page": page, "per_page": per_page,
        "total": total, "total_pages": total_pages,
        "elapsed_seconds": elapsed,
        "sectors": sorted({r.get("sector", "Unknown") for r in results if r.get("sector")}),
        "results": results[start: start + per_page],
    })


@app.route("/api/stock/<ticker>")
def stock_detail(ticker: str):
    ticker = ticker.upper().strip()
    if request.args.get("refresh", "false").lower() == "true":
        _CACHE.pop(ticker, None)
    result = _fetch_and_score(ticker)
    if result is None:
        return jsonify({"error": f"No data found for {ticker}"}), 404
    return jsonify(result)
