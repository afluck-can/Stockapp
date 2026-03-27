"""
Stock Picking Scoring Engine
=============================
Scores each stock 0-100 across four equally weighted categories:

  VALUE     (0-25 pts)  — Is the stock priced cheaply vs fundamentals?
  GROWTH    (0-25 pts)  — Is the business growing earnings & revenue?
  QUALITY   (0-25 pts)  — Is the business high-quality (ROE, margins, low debt)?
  MOMENTUM  (0-25 pts)  — Is price action positive vs recent history?

Recommended action tiers
  80-100  STRONG BUY   — Excellent across all four pillars
  65-79   BUY          — Strong on most pillars
  50-64   HOLD/WATCH   — Mixed signals, monitor closely
  35-49   AVOID        — Weak fundamentals or technicals
  0-34    SELL/SKIP    — Multiple red flags
"""

from __future__ import annotations
import math
from typing import Any


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


# ──────────────────────────────────────────────
# VALUE  (25 pts)
# Metrics: forward P/E, trailing P/B, trailing P/S
# Lower ratios → higher score
# ──────────────────────────────────────────────
def _score_value(info: dict[str, Any]) -> tuple[float, dict]:
    breakdown: dict[str, Any] = {}

    # Forward P/E (10 pts): ideal <15, neutral 25, bad >50
    pe = info.get("forwardPE") or info.get("trailingPE")
    if pe and pe > 0:
        pe_score = _clamp(1 - (pe - 5) / 45) * 10   # 5→10, 50→0
        breakdown["pe_ratio"] = round(pe, 2)
        breakdown["pe_score"] = round(pe_score, 1)
    else:
        pe_score = 0.0
        breakdown["pe_ratio"] = None
        breakdown["pe_score"] = 0

    # P/B (8 pts): ideal <1.5, bad >5
    pb = info.get("priceToBook")
    if pb and pb > 0:
        pb_score = _clamp(1 - (pb - 0.5) / 5.5) * 8
        breakdown["pb_ratio"] = round(pb, 2)
        breakdown["pb_score"] = round(pb_score, 1)
    else:
        pb_score = 0.0
        breakdown["pb_ratio"] = None
        breakdown["pb_score"] = 0

    # P/S (7 pts): ideal <1, bad >6
    ps = info.get("priceToSalesTrailing12Months")
    if ps and ps > 0:
        ps_score = _clamp(1 - (ps - 0.5) / 6.5) * 7
        breakdown["ps_ratio"] = round(ps, 2)
        breakdown["ps_score"] = round(ps_score, 1)
    else:
        ps_score = 0.0
        breakdown["ps_ratio"] = None
        breakdown["ps_score"] = 0

    total = pe_score + pb_score + ps_score
    breakdown["total"] = round(total, 1)
    return total, breakdown


# ──────────────────────────────────────────────
# GROWTH  (25 pts)
# Metrics: earnings growth, revenue growth, forward EPS vs trailing EPS
# ──────────────────────────────────────────────
def _score_growth(info: dict[str, Any]) -> tuple[float, dict]:
    breakdown: dict[str, Any] = {}

    # EPS growth YoY (10 pts): >50% = max, <0% = 0
    eg = info.get("earningsGrowth")          # decimal, e.g. 0.25 = 25%
    if eg is not None:
        eg_score = _clamp(eg / 0.5) * 10
        breakdown["earnings_growth_pct"] = round(eg * 100, 1)
        breakdown["earnings_growth_score"] = round(eg_score, 1)
    else:
        eg_score = 0.0
        breakdown["earnings_growth_pct"] = None
        breakdown["earnings_growth_score"] = 0

    # Revenue growth YoY (8 pts): >30% = max, <0% = 0
    rg = info.get("revenueGrowth")
    if rg is not None:
        rg_score = _clamp(rg / 0.30) * 8
        breakdown["revenue_growth_pct"] = round(rg * 100, 1)
        breakdown["revenue_growth_score"] = round(rg_score, 1)
    else:
        rg_score = 0.0
        breakdown["revenue_growth_pct"] = None
        breakdown["revenue_growth_score"] = 0

    # Forward EPS vs Trailing EPS  (7 pts): implies analyst optimism
    fwd_eps = info.get("forwardEps")
    trail_eps = info.get("trailingEps")
    if fwd_eps and trail_eps and trail_eps > 0:
        implied_growth = (fwd_eps - trail_eps) / abs(trail_eps)
        fwd_score = _clamp(implied_growth / 0.30) * 7
        breakdown["implied_fwd_growth_pct"] = round(implied_growth * 100, 1)
        breakdown["fwd_growth_score"] = round(fwd_score, 1)
    else:
        fwd_score = 0.0
        breakdown["implied_fwd_growth_pct"] = None
        breakdown["fwd_growth_score"] = 0

    total = eg_score + rg_score + fwd_score
    breakdown["total"] = round(total, 1)
    return total, breakdown


# ──────────────────────────────────────────────
# QUALITY  (25 pts)
# Metrics: ROE, profit margin, debt/equity
# ──────────────────────────────────────────────
def _score_quality(info: dict[str, Any]) -> tuple[float, dict]:
    breakdown: dict[str, Any] = {}

    # Return on Equity (10 pts): >30% = max, <0% = 0
    roe = info.get("returnOnEquity")
    if roe is not None:
        roe_score = _clamp(roe / 0.30) * 10
        breakdown["roe_pct"] = round(roe * 100, 1)
        breakdown["roe_score"] = round(roe_score, 1)
    else:
        roe_score = 0.0
        breakdown["roe_pct"] = None
        breakdown["roe_score"] = 0

    # Gross / Profit margin (8 pts): >30% = max, <0% = 0
    margin = info.get("profitMargins") or info.get("grossMargins")
    if margin is not None:
        margin_score = _clamp(margin / 0.30) * 8
        breakdown["profit_margin_pct"] = round(margin * 100, 1)
        breakdown["profit_margin_score"] = round(margin_score, 1)
    else:
        margin_score = 0.0
        breakdown["profit_margin_pct"] = None
        breakdown["profit_margin_score"] = 0

    # Debt / Equity (7 pts): <0.3 = max, >2.0 = 0
    de = info.get("debtToEquity")
    if de is not None and de >= 0:
        de_ratio = de / 100  # yfinance returns as %, e.g. 45.2 means 0.452
        de_score = _clamp(1 - de_ratio / 2.0) * 7
        breakdown["debt_to_equity"] = round(de_ratio, 2)
        breakdown["debt_equity_score"] = round(de_score, 1)
    else:
        de_score = 0.0
        breakdown["debt_to_equity"] = None
        breakdown["debt_equity_score"] = 0

    total = roe_score + margin_score + de_score
    breakdown["total"] = round(total, 1)
    return total, breakdown


# ──────────────────────────────────────────────
# MOMENTUM  (25 pts)
# Metrics: 52-week return, 3-month return (via 52wk high/low & current price)
# ──────────────────────────────────────────────
def _score_momentum(info: dict[str, Any]) -> tuple[float, dict]:
    breakdown: dict[str, Any] = {}

    price = info.get("currentPrice") or info.get("regularMarketPrice")
    high52 = info.get("fiftyTwoWeekHigh")
    low52 = info.get("fiftyTwoWeekLow")

    # 52-week return vs low (15 pts): >100% gain from low = max
    if price and low52 and low52 > 0:
        annual_return = (price - low52) / low52
        annual_score = _clamp(annual_return / 1.0) * 15
        breakdown["annual_return_pct"] = round(annual_return * 100, 1)
        breakdown["annual_return_score"] = round(annual_score, 1)
    else:
        annual_score = 0.0
        breakdown["annual_return_pct"] = None
        breakdown["annual_return_score"] = 0

    # Proximity to 52-week high (10 pts): at high = max, at low = 0
    if price and high52 and low52 and (high52 - low52) > 0:
        proximity = (price - low52) / (high52 - low52)
        prox_score = _clamp(proximity) * 10
        breakdown["high_proximity_pct"] = round(proximity * 100, 1)
        breakdown["high_proximity_score"] = round(prox_score, 1)
    else:
        prox_score = 0.0
        breakdown["high_proximity_pct"] = None
        breakdown["high_proximity_score"] = 0

    total = annual_score + prox_score
    breakdown["total"] = round(total, 1)
    return total, breakdown


# ──────────────────────────────────────────────
# MAIN PUBLIC FUNCTION
# ──────────────────────────────────────────────
def score_stock(info: dict[str, Any]) -> dict[str, Any]:
    """Return full scoring result for a stock given its yfinance .info dict."""
    v_total, v_break = _score_value(info)
    g_total, g_break = _score_growth(info)
    q_total, q_break = _score_quality(info)
    m_total, m_break = _score_momentum(info)

    composite = v_total + g_total + q_total + m_total

    if composite >= 80:
        signal = "STRONG BUY"
        signal_color = "green"
    elif composite >= 65:
        signal = "BUY"
        signal_color = "lime"
    elif composite >= 50:
        signal = "WATCH"
        signal_color = "yellow"
    elif composite >= 35:
        signal = "AVOID"
        signal_color = "orange"
    else:
        signal = "SKIP"
        signal_color = "red"

    return {
        "composite_score": round(composite, 1),
        "signal": signal,
        "signal_color": signal_color,
        "value": v_break,
        "growth": g_break,
        "quality": q_break,
        "momentum": m_break,
    }
