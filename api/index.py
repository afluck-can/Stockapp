"""
Vercel serverless handler — Flask WSGI app.
Differences from local backend/app.py:
  - No SQLite caching (serverless is stateless); uses a module-level dict for warm-start hits
  - Auto-detects internet/yfinance availability; falls back to synthetic demo data
  - Serves /api/* routes; static frontend is served by Vercel from /public/
"""

from __future__ import annotations
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import yfinance as yf
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

import scorer
import mock_data as mock_mod
from tickers import fetch_nasdaq_tickers

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Simple in-memory cache (survives warm starts, reset on cold start)
_CACHE: dict[str, dict] = {}
_CACHE_TTL = 6 * 3600  # 6 hours

MAX_WORKERS = 15

# ──────────────────────────────────────────────
# Detect whether Yahoo Finance is reachable
# ──────────────────────────────────────────────
def _check_yfinance() -> bool:
    try:
        import requests as req_lib
        req_lib.get("https://finance.yahoo.com", timeout=5)
        return True
    except Exception:
        return False

_YFINANCE_AVAILABLE = _check_yfinance()
if not _YFINANCE_AVAILABLE:
    logger.warning("Yahoo Finance unreachable — DEMO mode active.")


# ──────────────────────────────────────────────
# Cache helpers
# ──────────────────────────────────────────────
def _cache_get(ticker: str) -> dict | None:
    entry = _CACHE.get(ticker)
    if entry and (time.time() - entry["_ts"]) < _CACHE_TTL:
        return entry
    return None

def _cache_set(ticker: str, data: dict) -> None:
    data["_ts"] = time.time()
    _CACHE[ticker] = data


# ──────────────────────────────────────────────
# Core fetch + score
# ──────────────────────────────────────────────
def _fetch_and_score(ticker: str) -> dict[str, Any] | None:
    cached = _cache_get(ticker)
    if cached:
        return {k: v for k, v in cached.items() if k != "_ts"}

    try:
        if _YFINANCE_AVAILABLE:
            info = yf.Ticker(ticker).info
            if not info or info.get("quoteType") not in ("EQUITY", "ETF", None):
                return None
        else:
            info = mock_mod.mock_info(ticker)

        price = info.get("currentPrice") or info.get("regularMarketPrice")
        if not price:
            return None

        scores = scorer.score_stock(info)
        result: dict[str, Any] = {
            "ticker": ticker,
            "name": info.get("longName") or info.get("shortName", ticker),
            "sector": info.get("sector", "Unknown"),
            "industry": info.get("industry", "Unknown"),
            "market_cap": info.get("marketCap"),
            "price": price,
            "currency": info.get("currency", "USD"),
            "exchange": info.get("exchange", "NASDAQ"),
            "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
            "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
            "avg_volume": info.get("averageVolume"),
            "dividend_yield": info.get("dividendYield"),
            "demo_mode": not _YFINANCE_AVAILABLE,
            **scores,
        }
        _cache_set(ticker, result)
        return result

    except Exception as exc:
        logger.debug("Failed to fetch %s: %s", ticker, exc)
        return None


# ──────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────

@app.route("/")
def index():
    html = Path(__file__).parent.parent / "public" / "index.html"
    return send_file(str(html))


@app.route("/api/status")
def status():
    return jsonify({
        "demo_mode": not _YFINANCE_AVAILABLE,
        "message": (
            "Running in DEMO mode with synthetic data."
            if not _YFINANCE_AVAILABLE else
            "Live mode — connected to Yahoo Finance."
        ),
    })


@app.route("/api/criteria")
def criteria():
    return jsonify({
        "title": "Stock Picking Scoring Methodology",
        "description": (
            "Each stock is scored 0–100 across four equally-weighted pillars. "
            "A higher score indicates a more attractive stock by this multi-factor model."
        ),
        "pillars": [
            {
                "name": "Value",
                "max_points": 25,
                "description": "Is the stock priced cheaply relative to its fundamentals?",
                "metrics": [
                    {"metric": "Forward P/E", "max": 10, "ideal": "< 15", "bad": "> 50",
                     "rationale": "Lower P/E suggests the market is paying less for each dollar of earnings."},
                    {"metric": "Price-to-Book (P/B)", "max": 8, "ideal": "< 1.5", "bad": "> 5",
                     "rationale": "P/B below 1 means you're buying assets below book value."},
                    {"metric": "Price-to-Sales (P/S)", "max": 7, "ideal": "< 1", "bad": "> 6",
                     "rationale": "Low P/S stocks are often undervalued relative to revenue."},
                ],
            },
            {
                "name": "Growth",
                "max_points": 25,
                "description": "Is the business growing earnings and revenue meaningfully?",
                "metrics": [
                    {"metric": "EPS Growth (YoY)", "max": 10, "ideal": "> 50%", "bad": "< 0%",
                     "rationale": "Accelerating earnings growth drives share price appreciation."},
                    {"metric": "Revenue Growth (YoY)", "max": 8, "ideal": "> 30%", "bad": "< 0%",
                     "rationale": "Top-line growth is the foundation of long-term value creation."},
                    {"metric": "Forward EPS vs Trailing EPS", "max": 7, "ideal": "> 30% implied growth", "bad": "Flat/declining",
                     "rationale": "Analyst consensus for future earnings signals market confidence."},
                ],
            },
            {
                "name": "Quality",
                "max_points": 25,
                "description": "Is the business high-quality with a strong balance sheet?",
                "metrics": [
                    {"metric": "Return on Equity (ROE)", "max": 10, "ideal": "> 30%", "bad": "< 0%",
                     "rationale": "High ROE signals efficient use of shareholder capital."},
                    {"metric": "Profit Margin", "max": 8, "ideal": "> 30%", "bad": "< 0%",
                     "rationale": "Wide margins indicate pricing power and durable competitive advantages."},
                    {"metric": "Debt / Equity", "max": 7, "ideal": "< 0.3", "bad": "> 2.0",
                     "rationale": "Low leverage reduces risk and preserves financial flexibility."},
                ],
            },
            {
                "name": "Momentum",
                "max_points": 25,
                "description": "Is price action confirming the fundamental thesis?",
                "metrics": [
                    {"metric": "52-Week Return (vs low)", "max": 15, "ideal": "> 100%", "bad": "Near 52-week low",
                     "rationale": "Strong upward price trends often persist in the medium term."},
                    {"metric": "52-Week High Proximity", "max": 10, "ideal": "Near high", "bad": "Near low",
                     "rationale": "Stocks near their 52-week high exhibit relative strength."},
                ],
            },
        ],
        "signals": [
            {"range": "80–100", "label": "STRONG BUY", "color": "green"},
            {"range": "65–79",  "label": "BUY",         "color": "lime"},
            {"range": "50–64",  "label": "WATCH",       "color": "yellow"},
            {"range": "35–49",  "label": "AVOID",       "color": "orange"},
            {"range": "0–34",   "label": "SKIP",        "color": "red"},
        ],
    })


@app.route("/api/scan")
def scan():
    page = max(1, int(request.args.get("page", 1)))
    per_page = min(100, max(1, int(request.args.get("per_page", 25))))
    sector_filter = request.args.get("sector", "").strip().lower()
    min_score = float(request.args.get("min_score", 0))
    max_score = float(request.args.get("max_score", 100))
    signal_filter = request.args.get("signal", "").strip().upper()
    sort_by = request.args.get("sort_by", "composite_score")
    sort_dir = request.args.get("sort_dir", "desc")
    custom_tickers = request.args.get("tickers", "").strip()
    force_refresh = request.args.get("refresh", "false").lower() == "true"

    if custom_tickers:
        ticker_list = [t.strip().upper() for t in custom_tickers.split(",") if t.strip()]
    elif _YFINANCE_AVAILABLE:
        # Live mode: use curated list to stay within Vercel's timeout
        ticker_list = mock_mod.MOCK_TICKERS
    else:
        ticker_list = mock_mod.MOCK_TICKERS

    if force_refresh:
        for t in ticker_list:
            _CACHE.pop(t, None)

    results: list[dict] = []
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_fetch_and_score, t): t for t in ticker_list}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                results.append(r)

    elapsed = round(time.time() - start_time, 2)

    # Filters
    if sector_filter:
        results = [r for r in results if sector_filter in (r.get("sector") or "").lower()]
    results = [r for r in results if min_score <= r["composite_score"] <= max_score]
    if signal_filter:
        results = [r for r in results if r.get("signal") == signal_filter]

    # Sort
    reverse = sort_dir != "asc"
    def _key(r):
        v = r.get(sort_by)
        return (-math.inf if reverse else math.inf) if v is None else v
    results.sort(key=_key, reverse=reverse)

    all_sectors = sorted({r.get("sector", "Unknown") for r in results if r.get("sector")})
    total = len(results)
    total_pages = max(1, math.ceil(total / per_page))
    start = (page - 1) * per_page

    return jsonify({
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "elapsed_seconds": elapsed,
        "sectors": all_sectors,
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


# Vercel calls this module directly — the `app` variable is the WSGI handler.
