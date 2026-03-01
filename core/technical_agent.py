# core/technical_agent.py
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from core.market_engine import Candle
from core import config as cfg


@dataclass(frozen=True)
class TechnicalVerdict:
    ok: bool
    sl: Optional[float]
    mode: str  # "STANDARD" or "SHOCK"
    notes: str = ""


class TechnicalAgent:
    """
    Technical Analyst Agent with Regime Detection (STANDARD vs SHOCK)

    SHOCK confirmation (kept simple, no scope creep):
      - ATR spike vs ATR_SMA * multiplier
      - range expansion vs median range * multiplier
      - event_score >= min score
      - break of prior high/low (lookback window)

    Stop doctrine (locked):
      - STANDARD: ATR_MULT_STANDARD * ATR
      - SHOCK:    ATR_MULT_SHOCK * ATR
    """

    def evaluate(
        self,
        *,
        candles: List[Candle],
        side: str,
        event_score: int,
    ) -> TechnicalVerdict:
        side = (side or "").upper()
        if side not in ("BUY", "SELL"):
            return TechnicalVerdict(False, None, "STANDARD", "invalid_side")

        if not candles:
            return TechnicalVerdict(False, None, "STANDARD", "no_candles")

        # Need enough data for EMA/RSI/ATR plus ATR baseline for regime detection.
        min_need = max(
            int(cfg.TECH_EMA_SLOW),
            int(cfg.TECH_RSI_PERIOD) + 2,
            int(cfg.TECH_ATR_PERIOD) + int(cfg.ATR_SMA_PERIOD),
            50,
        )
        if len(candles) < min_need:
            return TechnicalVerdict(False, None, "STANDARD", f"not_enough_candles:{len(candles)}<{min_need}")

        closes = [float(c.c) for c in candles]
        highs = [float(c.h) for c in candles]
        lows = [float(c.l) for c in candles]

        last = float(closes[-1])

        # Break of prior high/low uses a small fixed lookback.
        # Ensure we have enough to slice safely.
        lb = 6  # use previous 5 candles for prior high/low
        if len(highs) < lb:
            return TechnicalVerdict(False, None, "STANDARD", "not_enough_for_breakout")

        prev_high = max(highs[-lb:-1])
        prev_low = min(lows[-lb:-1])

        ema_fast = self._ema(closes, int(cfg.TECH_EMA_FAST))
        ema_slow = self._ema(closes, int(cfg.TECH_EMA_SLOW))
        rsi = self._rsi(closes, int(cfg.TECH_RSI_PERIOD))

        atr_series = self._atr_series(highs, lows, closes, int(cfg.TECH_ATR_PERIOD))
        if not atr_series or len(atr_series) < int(cfg.ATR_SMA_PERIOD):
            return TechnicalVerdict(False, None, "STANDARD", "atr_calc_fail")

        current_atr = float(atr_series[-1])
        atr_sma = float(sum(atr_series[-int(cfg.ATR_SMA_PERIOD):]) / float(cfg.ATR_SMA_PERIOD))

        # Range expansion (robust against short arrays)
        ranges = [(h - l) for h, l in zip(highs, lows)]
        recent_ranges = ranges[-20:] if len(ranges) >= 20 else ranges
        if not recent_ranges:
            return TechnicalVerdict(False, None, "STANDARD", "no_ranges")

        sr = sorted(recent_ranges)
        median_range = float(sr[len(sr) // 2])
        last_range = float(ranges[-1])

        # NOTE: Use *_DEFAULT constants to match the config file you updated.
        range_expansion = last_range > (median_range * float(cfg.RANGE_EXPANSION_MULTIPLIER_DEFAULT))
        high_volatility = current_atr > (atr_sma * float(cfg.ATR_SHOCK_MULTIPLIER_DEFAULT))
        major_event = int(event_score) >= int(cfg.SHOCK_MIN_EVENT_SCORE_DEFAULT)

        # -----------------------------
        # SHOCK MODE
        # -----------------------------
        if high_volatility and major_event and range_expansion:
            if side == "BUY" and last > prev_high:
                sl = last - (float(cfg.ATR_MULT_SHOCK) * current_atr)
                return TechnicalVerdict(True, float(sl), "SHOCK", "shock_breakout_buy")

            if side == "SELL" and last < prev_low:
                sl = last + (float(cfg.ATR_MULT_SHOCK) * current_atr)
                return TechnicalVerdict(True, float(sl), "SHOCK", "shock_breakdown_sell")

        # -----------------------------
        # STANDARD MODE (strict trend alignment)
        # -----------------------------
        if side == "BUY":
            if not (ema_fast > ema_slow):
                return TechnicalVerdict(False, None, "STANDARD", "trend_reject")
            if rsi >= float(cfg.TECH_RSI_OVERBOUGHT):
                return TechnicalVerdict(False, None, "STANDARD", "rsi_reject_overbought")
            sl = last - (float(cfg.ATR_MULT_STANDARD) * current_atr)
            return TechnicalVerdict(True, float(sl), "STANDARD", "trend_confirm_buy")

        if side == "SELL":
            if not (ema_fast < ema_slow):
                return TechnicalVerdict(False, None, "STANDARD", "trend_reject")
            if rsi <= float(cfg.TECH_RSI_OVERSOLD):
                return TechnicalVerdict(False, None, "STANDARD", "rsi_reject_oversold")
            sl = last + (float(cfg.ATR_MULT_STANDARD) * current_atr)
            return TechnicalVerdict(True, float(sl), "STANDARD", "trend_confirm_sell")

        return TechnicalVerdict(False, None, "STANDARD", "conditions_not_met")

    # ---- indicator helpers ----

    def _ema(self, xs: List[float], period: int) -> float:
        k = 2.0 / (period + 1.0)
        ema = sum(xs[:period]) / float(period)
        for x in xs[period:]:
            ema = (x * k) + (ema * (1.0 - k))
        return float(ema)

    def _rsi(self, closes: List[float], period: int) -> float:
        gains: List[float] = []
        losses: List[float] = []

        for i in range(1, period + 1):
            d = closes[i] - closes[i - 1]
            gains.append(max(d, 0.0))
            losses.append(max(-d, 0.0))

        avg_gain = sum(gains) / float(period)
        avg_loss = sum(losses) / float(period)

        for i in range(period + 1, len(closes)):
            d = closes[i] - closes[i - 1]
            gain = max(d, 0.0)
            loss = max(-d, 0.0)
            avg_gain = (avg_gain * (period - 1) + gain) / float(period)
            avg_loss = (avg_loss * (period - 1) + loss) / float(period)

        if avg_loss == 0.0:
            return 100.0

        rs = avg_gain / avg_loss
        return float(100.0 - (100.0 / (1.0 + rs)))

    def _atr_series(self, highs: List[float], lows: List[float], closes: List[float], period: int) -> List[float]:
        trs: List[float] = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(float(tr))

        if len(trs) < period:
            return []

        atrs: List[float] = []
        atr = sum(trs[:period]) / float(period)
        atrs.append(float(atr))

        for tr in trs[period:]:
            atr = (atr * (period - 1) + tr) / float(period)
            atrs.append(float(atr))

        return atrs
