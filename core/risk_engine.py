# core/risk_engine.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from core.trade_tracker import Trade
from core.risk_utils import round_qty_to_lot


@dataclass(frozen=True)
class InstrumentSpec:
    yfinance_symbol: str
    point_value_inr: float
    lot_step: float
    lot_min: float
    atr_low_pct: float
    atr_high_pct: float
    factors: Tuple[str, ...]
    tier: int = 1


@dataclass(frozen=True)
class RiskDecision:
    ok: bool
    reason: str = ""
    lots: float = 0.0
    qty_raw: float = 0.0
    qty_rounded: float = 0.0
    rounding_applied: bool = False
    risk_inr: float = 0.0


class RiskEngine:
    """
    Risk approval + sizing

    Sizing:
      - Risk budget per trade = equity_inr * risk_pct_per_trade
      - lots = risk_budget / (|entry-sl| * point_value_inr), floored to lot_step
      - blocks if < lot_min

    Portfolio constraints:
      - max_factor_trades per factor
      - max_portfolio_risk_pct (sum of open trade risk_inr budgets)

    Regime constraint:
      - blocks if ATR% < atr_low_pct
      - blocks if ATR% > atr_high_pct unless category is whitelisted
    """

    def __init__(
        self,
        *,
        equity_inr: float,
        risk_pct_per_trade: float,
        max_portfolio_risk_pct: float,
        max_factor_trades: int,
        instruments: Dict[str, "InstrumentSpec"],
        high_vol_categories: Optional[Tuple[str, ...]] = None,
    ) -> None:
        self.equity_inr = float(equity_inr)
        self.risk_pct_per_trade = float(risk_pct_per_trade)
        self.max_portfolio_risk_pct = float(max_portfolio_risk_pct)
        self.max_factor_trades = int(max_factor_trades)
        self.instruments = instruments

        # Categories that are allowed even when ATR% is above atr_high_pct
        self.high_vol_categories = tuple(high_vol_categories or ("geopolitics", "commodities_gold_oil", "rates_macro"))

    def _calc_lots(self, symbol: str, entry: float, sl: float, equity_inr: float) -> Tuple[float, float, float, bool]:
        spec = self.instruments[symbol]

        stop_points = abs(float(entry) - float(sl))
        if stop_points <= 0:
            return 0.0, 0.0, 0.0, False

        risk_inr = float(equity_inr) * self.risk_pct_per_trade
        denom = stop_points * float(spec.point_value_inr)
        if denom <= 0:
            return 0.0, 0.0, 0.0, False

        qty_raw = float(risk_inr / denom)

        step = float(spec.lot_step) if spec.lot_step else 1.0
        qty_rounded = float(round_qty_to_lot(qty_raw, step, mode="floor"))
        rounding_applied = abs(qty_raw - qty_rounded) > 1e-12

        if qty_rounded < float(spec.lot_min):
            # return the risk budget so caller can see why "size too small"
            return qty_raw, 0.0, float(risk_inr), rounding_applied

        return qty_raw, qty_rounded, float(risk_inr), rounding_applied

    def _open_risk(self, open_trades: List[Trade]) -> float:
        return sum(float(t.risk_inr or 0.0) for t in open_trades if t.status == "OPEN")

    def _factor_block(self, symbol: str, open_trades: List[Trade]) -> Tuple[bool, str]:
        spec = self.instruments[symbol]

        factor_counts: Dict[str, int] = {}
        for t in open_trades:
            if t.status != "OPEN":
                continue
            tspec = self.instruments.get(t.symbol)
            if not tspec:
                continue
            for f in tspec.factors:
                factor_counts[f] = factor_counts.get(f, 0) + 1

        for f in spec.factors:
            if factor_counts.get(f, 0) >= self.max_factor_trades:
                return True, f"factor_cap:{f}"
        return False, ""

    def _regime_allow(self, symbol: str, atr_percent: Optional[float], category: str) -> Tuple[bool, str]:
        # Conservative default: if ATR unavailable, allow but record reason.
        if atr_percent is None:
            return True, "atr_unavailable"

        spec = self.instruments[symbol]

        if atr_percent < float(spec.atr_low_pct):
            return False, f"low_vol:atr%={atr_percent:.3f}< {spec.atr_low_pct}"

        if atr_percent > float(spec.atr_high_pct):
            if category in set(self.high_vol_categories):
                return True, f"high_vol_allowed:atr%={atr_percent:.3f}> {spec.atr_high_pct}"
            return False, f"high_vol_blocked:atr%={atr_percent:.3f}> {spec.atr_high_pct}"

        return True, f"atr_ok:{atr_percent:.3f}"

    def approve(
        self,
        *,
        symbol: str,
        category: str,
        entry: float,
        sl: float,
        open_trades: List[Trade],
        atr_percent: Optional[float],
        equity_inr: Optional[float] = None,
    ) -> RiskDecision:
        symbol = (symbol or "").strip()
        category = (category or "").strip()

        if not symbol or symbol not in self.instruments:
            return RiskDecision(False, "unknown_symbol")

        ok_reg, why_reg = self._regime_allow(symbol, atr_percent, category)
        if not ok_reg:
            return RiskDecision(False, why_reg)

        blocked, why = self._factor_block(symbol, open_trades)
        if blocked:
            return RiskDecision(False, why)

        eq = float(equity_inr) if equity_inr is not None else float(self.equity_inr)
        if eq <= 0.0:
            eq = float(self.equity_inr)

        qty_raw, qty_rounded, risk_inr, rounding_applied = self._calc_lots(symbol, entry, sl, eq)
        if qty_rounded <= 0:
            return RiskDecision(
                False,
                "size_too_small",
                lots=0.0,
                qty_raw=float(qty_raw),
                qty_rounded=0.0,
                rounding_applied=bool(rounding_applied),
                risk_inr=float(risk_inr),
            )

        open_risk = self._open_risk(open_trades)
        cap = eq * self.max_portfolio_risk_pct
        if open_risk + risk_inr > cap:
            return RiskDecision(
                False,
                f"portfolio_risk_cap:open={open_risk:.0f}+new={risk_inr:.0f}>cap={cap:.0f}",
                lots=0.0,
                qty_raw=float(qty_raw),
                qty_rounded=0.0,
                rounding_applied=bool(rounding_applied),
                risk_inr=float(risk_inr),
            )

        return RiskDecision(
            True,
            why_reg,
            lots=float(qty_rounded),
            qty_raw=float(qty_raw),
            qty_rounded=float(qty_rounded),
            rounding_applied=bool(rounding_applied),
            risk_inr=float(risk_inr),
        )
