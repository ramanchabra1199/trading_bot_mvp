# core/risk_manager_agent.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core import config as cfg


@dataclass(frozen=True)
class RRDecision:
    ok: bool
    tp: Optional[float] = None
    rr: Optional[float] = None
    notes: str = ""


class RiskManagerAgent:
    """
    Risk Manager Agent:
      - enforces minimum reward:risk (default MIN_RR_DEFAULT)
      - computes TP from entry & SL
    """

    def compute_tp(self, *, entry: float, sl: float, side: str, min_rr: float) -> RRDecision:
        side = (side or "").upper()
        if side not in ("BUY", "SELL"):
            return RRDecision(False, None, None, "invalid_side")

        entry = float(entry)
        sl = float(sl)

        risk = abs(entry - sl)
        if risk <= 0.0:
            return RRDecision(False, None, None, "zero_risk")

        # Fallback to config default, and enforce sane RR
        if min_rr is None or float(min_rr) <= 0.0:
            min_rr_f = float(cfg.MIN_RR_DEFAULT)
        else:
            min_rr_f = float(min_rr)

        if min_rr_f <= 0.0:
            return RRDecision(False, None, None, "invalid_min_rr")

        if side == "BUY":
            tp = entry + (min_rr_f * risk)
        else:
            tp = entry - (min_rr_f * risk)

        rr = abs(tp - entry) / risk if risk else 0.0
        if rr + 1e-9 < min_rr_f:
            return RRDecision(False, None, float(rr), f"rr_fail:{rr:.2f}<{min_rr_f:.2f}")

        return RRDecision(True, float(tp), float(rr), f"rr_ok:{rr:.2f}")