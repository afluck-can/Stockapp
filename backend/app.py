"""
Stock Picking App — Flask Backend
==================================
Endpoints:
  GET  /                        Serve the SPA
  GET  /api/criteria            Return the scoring methodology documentation
  GET  /api/scan                Scan & score stocks (paginated, filterable)
  GET  /api/stock/<ticker>      Full detail for a single ticker
  POST /api/refresh/<ticker>    Force-refresh a ticker's cache entry
  GET  /api/cache/stats         Cache statistics
  POST /api/cache/clear         Clear the entire cache
"""

from __future__ import annotations
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import yfinance as yf
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

import cache as cache_mod
import scorer
from tickers import fetch_nasdaq_tickers, CURATED_NASDAQ
import mock_data as mock_mod

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Detect whether Yahoo Finance / internet is reachable on startup
def _check_yfinance() -> bool:
    try:
        import requests as req_lib
        req_lib.get("https://finance.yahoo.com", timeout=4)
        return True
    except Exception:
        return False

_YFINANCE_AVAILABLE = _check_yfinance()
if not _YFINANCE_AVAILABLE:
    logger.warning("Yahoo Finance unreachable — running in DEMO mode with synthetic data.")

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
app = Flask(__name__, static_folder=str(FRONTEND_DIR / "static"))
CORS(app)

MAX_WORKERS = 20
BATCH_SIZE = 50


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _fetch_and_score(ticker: str) -> dict[str, Any] | None:
    """Fetch stock info (from cache or yfinance/mock) and return a scored summary."""
    cached = cache_mod.get(ticker)
    if cached:
        return cached

    try:
        if _YFINANCE_AVAILABLE:
            info = yf.Ticker(ticker).info
            if not info or info.get("quoteType") not in ("EQUITY", "ETF", None):
                return None
        else:
            # Demo mode: use synthetic data
            info = mock_mod.mock_info(ticker)

        # Skip if no price data
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

        cache_mod.set(ticker, result)
        return result

    except Exception as exc:
        logger.debug("Failed to fetch %s: %s", ticker, exc)
        return None


def _market_cap_label(cap: int | None) -> str:
    if cap is None:
        return "Unknown"
    if cap >= 200e9:
        return "Mega Cap"
    if cap >= 10e9:
        return "Large Cap"
    if cap >= 2e9:
        return "Mid Cap"
    if cap >= 300e6:
        return "Small Cap"
    return "Micro Cap"


# ──────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(str(FRONTEND_DIR), "index.html")


@app.route("/api/status")
def status():
    return jsonify({
        "demo_mode": not _YFINANCE_AVAILABLE,
        "message": (
            "Running in DEMO mode with synthetic data. "
            "Deploy with internet access for live NASDAQ data."
            if not _YFINANCE_AVAILABLE else
            "Live mode — connected to Yahoo Finance."
        ),
    })


@app.route("/api/criteria")
def criteria():
    """Return the scoring methodology as structured JSON."""
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
    """
    Query params:
      page        int  (default 1)
      per_page    int  (default 25, max 100)
      sector      str  (filter by sector)
      min_score   float (default 0)
      max_score   float (default 100)
      signal      str  (STRONG BUY | BUY | WATCH | AVOID | SKIP)
      sort_by     str  (composite_score | name | sector | market_cap | price)
      sort_dir    str  (desc | asc, default desc)
      tickers     str  comma-separated override list
      refresh     bool force refresh even if cached
    """
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
        ticker_list = fetch_nasdaq_tickers(limit=500)
    else:
        # Demo mode: use the curated list we have mock data for
        ticker_list = mock_mod.MOCK_TICKERS

    if force_refresh:
        for t in ticker_list:
            cache_mod.invalidate(t)

    results: list[dict] = []
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_fetch_and_score, t): t for t in ticker_list}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                results.append(r)

    elapsed = round(time.time() - start_time, 2)
    logger.info("Scanned %d stocks in %.2fs", len(results), elapsed)

    # Apply filters
    if sector_filter:
        results = [r for r in results if sector_filter in (r.get("sector") or "").lower()]
    results = [r for r in results if min_score <= r["composite_score"] <= max_score]
    if signal_filter:
        results = [r for r in results if r.get("signal") == signal_filter]

    # Sort
    reverse = sort_dir != "asc"
    def _sort_key(r):
        v = r.get(sort_by)
        if v is None:
            return (-math.inf if reverse else math.inf)
        return v
    results.sort(key=_sort_key, reverse=reverse)

    # Sectors for filter dropdown
    all_sectors = sorted({r.get("sector", "Unknown") for r in results if r.get("sector")})

    total = len(results)
    total_pages = max(1, math.ceil(total / per_page))
    start = (page - 1) * per_page
    page_results = results[start: start + per_page]

    return jsonify({
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "elapsed_seconds": elapsed,
        "sectors": all_sectors,
        "results": page_results,
    })


@app.route("/api/stock/<ticker>")
def stock_detail(ticker: str):
    ticker = ticker.upper().strip()
    force = request.args.get("refresh", "false").lower() == "true"
    if force:
        cache_mod.invalidate(ticker)
    result = _fetch_and_score(ticker)
    if result is None:
        return jsonify({"error": f"No data found for {ticker}"}), 404
    return jsonify(result)


@app.route("/api/refresh/<ticker>", methods=["POST"])
def refresh_ticker(ticker: str):
    ticker = ticker.upper().strip()
    cache_mod.invalidate(ticker)
    result = _fetch_and_score(ticker)
    if result is None:
        return jsonify({"error": f"No data found for {ticker}"}), 404
    return jsonify(result)


@app.route("/api/cache/stats")
def cache_stats():
    return jsonify(cache_mod.stats())


@app.route("/api/cache/clear", methods=["POST"])
def cache_clear():
    cache_mod.clear_all()
    return jsonify({"status": "cleared"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
