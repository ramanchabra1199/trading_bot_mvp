# core/config.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class CategoryRule:
    any_keywords: Tuple[str, ...]


@dataclass(frozen=True)
class TradeRule:
    symbol: str
    side: str  # "BUY" or "SELL"
    # minimum number of strong keywords required to trigger
    strong_keywords: Tuple[str, ...]
    min_hits: int = 1


# --- Categorisation rules (title + summary) ---
CATEGORY_RULES: Dict[str, CategoryRule] = {
    "personal_finance": CategoryRule(
        any_keywords=("401(k)", "401k", "roth", "ira", "retirement", "mortgage", "credit card")
    ),
    "markets": CategoryRule(
        any_keywords=(
            "s&p",
            "dow",
            "nasdaq",
            "index",
            "stocks",
            "equities",
            "futures",
            "options",
            "yield",
            "treasury",
            "volatility",
            "breakout",
        )
    ),
    "earnings": CategoryRule(any_keywords=("earnings", "guidance", "quarter", "q1", "q2", "q3", "q4", "revenue", "eps")),
    "crypto": CategoryRule(any_keywords=("bitcoin", "btc", "ethereum", "eth", "dogecoin", "doge", "crypto", "altcoin")),
    "geopolitics": CategoryRule(
        any_keywords=("china", "russia", "iran", "sanctions", "war", "missile", "invasion", "tariff", "negotiations", "trade talks", "conflict")
    ),
}

# --- Trade mapping rules ---
# IMPORTANT: geopolitics does NOT automatically mean gold.
# It only triggers gold if strong escalation keywords appear.
TRADE_RULES: Dict[str, List[TradeRule]] = {
    "geopolitics": [
        TradeRule(
            symbol="GC=F",
            side="BUY",
            strong_keywords=("war", "attack", "missile", "invasion", "escalation", "sanctions", "nuclear", "strike", "airstrike"),
            min_hits=1,
        )
    ],
    "crypto": [
        # example: you might map BTC to a crypto instrument later
        # TradeRule(symbol="BTC-USD", side="BUY", strong_keywords=("breakout", "surge"), min_hits=1)
    ],
}

# --- Quote sanity ranges (guardrails) ---
SANITY_RANGES = {
    "GC=F": (800.0, 4000.0),  # Gold futures (USD/oz) guardrail
    "SI=F": (5.0, 100.0),     # Silver
    "EURUSD=X": (0.5, 2.0),
    "GBPUSD=X": (0.5, 2.5),
    "USDINR=X": (30.0, 150.0),
}

# =============================================================================
# Multi-Agent / Regime Detection Settings (NEW)
# =============================================================================

# Master switch for technical gating. If False -> old tp/sl multipliers path is used.
TECH_ENABLED = True

# Candle fetch parameters (used by MarketEngine.get_candles)
TECH_TF_INTERVAL = "15m"  # yfinance-supported: "5m", "15m", "30m", "60m", etc.
TECH_TF_PERIOD = "30d"    # intraday history window (enough bars for EMA200 on 15m)
TECH_LIMIT = 300          # how many candles to keep from the returned dataframe

# Indicators
TECH_EMA_FAST = 50
TECH_EMA_SLOW = 200
TECH_RSI_PERIOD = 14
TECH_ATR_PERIOD = 14

# RSI exhaustion gates (STANDARD mode only)
TECH_RSI_OVERBOUGHT = 70.0
TECH_RSI_OVERSOLD = 30.0

# Risk Manager Agent (reward:risk minimum)
MIN_RR_DEFAULT = 2.0

# =============================================================================
# SHOCK MODE (Volatility Switch)
# =============================================================================

# ATR baseline smoothing length
ATR_SMA_PERIOD = 20

# Shock trigger: current ATR > ATR_SMA * multiplier
ATR_SHOCK_MULTIPLIER_DEFAULT = 1.5

# Shock confirmation: last candle range expansion vs median recent range
RANGE_EXPANSION_MULTIPLIER_DEFAULT = 1.5

# Minimum event score needed to allow Shock Mode.
SHOCK_MIN_EVENT_SCORE_DEFAULT = 4

# Stop doctrine (locked):
# STANDARD uses tighter ATR stop; SHOCK uses wider ATR stop.
ATR_MULT_STANDARD = 1.2
ATR_MULT_SHOCK = 2.5

# Simple size scaling in shock mode (apply after RiskEngine sizing)
SHOCK_SIZE_MULTIPLIER = 0.7

# -----------------------------------------------------------------------------
# Backward-compatible aliases (IMPORTANT)
# These prevent breakage if any file still references the old non-DEFAULT names.
# -----------------------------------------------------------------------------
ATR_SHOCK_MULTIPLIER = ATR_SHOCK_MULTIPLIER_DEFAULT
RANGE_EXPANSION_MULTIPLIER = RANGE_EXPANSION_MULTIPLIER_DEFAULT
SHOCK_MIN_EVENT_SCORE = SHOCK_MIN_EVENT_SCORE_DEFAULT

# --------------------------------------------------------------------------
# Per-symbol overrides (optional; not used unless you wire it in)
# Key should match whatever you choose to pass to TechnicalAgent:
# - INTERNAL symbol (recommended): "USDINR", "NIFTY", "BANKNIFTY", etc.
# --------------------------------------------------------------------------
SYMBOL_REGIME_OVERRIDES: Dict[str, Dict[str, float]] = {
    # Examples:
    # "NIFTY": {"atr_shock_mult": 1.5, "range_mult": 1.5, "shock_min_score": 6},
    # "BANKNIFTY": {"atr_shock_mult": 1.7, "range_mult": 1.6, "shock_min_score": 6},
    # "GOLD": {"atr_shock_mult": 1.5, "range_mult": 1.5, "shock_min_score": 4},
}
