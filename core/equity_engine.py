from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

from core.trade_tracker import Trade

log = logging.getLogger(__name__)


@dataclass
class EquityState:
    equity_inr: float = 0.0
    peak_inr: float = 0.0
    max_drawdown_pct: float = 0.0
    applied_trade_ids: Dict[str, datetime] = None
    points: List[Dict] = None


class EquityEngine:
    def __init__(self, path: str = "data/equity.json", start_equity_inr: float = 0.0) -> None:
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.state = self._load(start_equity_inr)

    def _load(self, start_equity_inr: float) -> EquityState:
        if not os.path.exists(self.path):
            st = EquityState(
                equity_inr=float(start_equity_inr),
                peak_inr=float(start_equity_inr),
                max_drawdown_pct=0.0,
                points=[],
            )
            self._save(st)
            return st

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f) or {}
            d.setdefault("points", [])
            d.setdefault("applied_trade_ids", {})
            raw_applied = d.get("applied_trade_ids")
            if isinstance(raw_applied, list):
                # backward compatibility from old list-only format
                now = datetime.now(timezone.utc)
                d["applied_trade_ids"] = {str(tid): now for tid in raw_applied}
            elif isinstance(raw_applied, dict):
                parsed: Dict[str, datetime] = {}
                for tid, ts in raw_applied.items():
                    key = str(tid)
                    dt = self._coerce_dt(ts)
                    if dt is None:
                        continue
                    parsed[key] = dt
                d["applied_trade_ids"] = parsed
            else:
                d["applied_trade_ids"] = {}
            return EquityState(**d)
        except Exception:
            st = EquityState(
                equity_inr=float(start_equity_inr),
                peak_inr=float(start_equity_inr),
                max_drawdown_pct=0.0,
                applied_trade_ids={},
                points=[],
            )
            self._save(st)
            return st

    def _save(self, st: EquityState) -> None:
        payload = asdict(st)
        applied = {}
        for tid, ts in (st.applied_trade_ids or {}).items():
            dt = self._coerce_dt(ts)
            if dt is None:
                continue
            applied[str(tid)] = dt.isoformat()
        payload["applied_trade_ids"] = applied
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def apply_closed_trades(self, closed: List[Trade]) -> None:
        st = self.state
        if st.points is None:
            st.points = []
        if st.applied_trade_ids is None:
            st.applied_trade_ids = {}
        now = datetime.now(timezone.utc)
        self.prune_applied_trade_ids(now=now, retention_days=14)

        for t in closed:
            if str(getattr(t, "status", "")).upper() != "CLOSED":
                log.info("equity_apply_skipped trade_id=%s reason=non_closed_status status=%s", t.id, t.status)
                continue
            tid = str(t.id)
            if tid in st.applied_trade_ids:
                log.info("equity_apply_skipped trade_id=%s reason=already_applied", t.id)
                continue
            pnl = float(t.pnl_inr or 0.0)
            st.equity_inr = float(st.equity_inr) + pnl

            if st.equity_inr > st.peak_inr:
                st.peak_inr = st.equity_inr

            dd = 0.0
            if st.peak_inr > 0:
                dd = (st.peak_inr - st.equity_inr) / st.peak_inr * 100.0
            if dd > st.max_drawdown_pct:
                st.max_drawdown_pct = dd

            st.points.append(
                {
                    "ts": t.close_at or t.created_at,
                    "trade_id": t.id,
                    "symbol": t.symbol,
                    "category": t.category,
                    "outcome": t.outcome,
                    "pnl_inr": pnl,
                    "equity_inr": st.equity_inr,
                }
            )
            st.applied_trade_ids[tid] = now

        self._save(st)

    def prune_applied_trade_ids(self, now: datetime, retention_days: int = 14) -> None:
        st = self.state
        if st.applied_trade_ids is None:
            st.applied_trade_ids = {}
            return
        cutoff = self._coerce_dt(now)
        if cutoff is None:
            cutoff = datetime.now(timezone.utc)
        cutoff = cutoff - timedelta(days=int(retention_days))

        keep: Dict[str, datetime] = {}
        changed = False
        for tid, ts in list(st.applied_trade_ids.items()):
            dt = self._coerce_dt(ts)
            if dt is None:
                changed = True
                continue
            if dt < cutoff:
                changed = True
                continue
            keep[str(tid)] = dt

        if changed:
            st.applied_trade_ids = keep

    def _coerce_dt(self, ts: object) -> Optional[datetime]:
        if isinstance(ts, datetime):
            return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        if isinstance(ts, str) and ts:
            try:
                dt = datetime.fromisoformat(ts)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except Exception:
                return None
        return None

    def summary(self) -> str:
        st = self.state
        n = len(st.points or [])
        return (
            "📈 Equity\n"
            f"- Equity: ₹{st.equity_inr:,.0f}\n"
            f"- Peak: ₹{st.peak_inr:,.0f}\n"
            f"- Max DD: {st.max_drawdown_pct:.2f}%\n"
            f"- Points: {n}"
        )

    def current_equity(self) -> float:
        """realized-only; open trades excluded; MTM must be separate."""
        return float(self.state.equity_inr)
