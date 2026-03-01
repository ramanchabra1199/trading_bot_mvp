from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Trade:
    id: str
    category: str
    symbol: str              # INTERNAL symbol e.g. "NIFTY50", "MCX_GOLD"
    direction: str           # BUY/SELL
    entry: float
    tp: float
    sl: float
    quality: str = "RAW"     # RAW/CONFIRMED

    # NEW: sizing + risk bookkeeping
    lots: float = 0.0
    risk_inr: float = 0.0
    point_value_inr: float = 0.0

    # bookkeeping
    created_at: str = field(default_factory=lambda: _utcnow().isoformat())
    expires_at: str = ""     # ISO string
    status: str = "OPEN"     # OPEN/CLOSED/EXPIRED
    close_reason: str = ""
    close_at: Optional[str] = None
    close_price: Optional[float] = None
    outcome: Optional[str] = None  # "TP"/"SL"/None

    # realized PnL
    realized_pnl: float = 0.0
    pnl_inr: float = 0.0

    # evidence
    evidence_sources: List[str] = field(default_factory=list)

    # scoring
    evidence_score_total: int = 0

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Trade":
        # Backward compatible defaults
        d = dict(d or {})
        d.setdefault("evidence_sources", [])
        d.setdefault("evidence_score_total", 0)

        d.setdefault("lots", 0.0)
        d.setdefault("risk_inr", 0.0)
        d.setdefault("point_value_inr", 0.0)
        d.setdefault("realized_pnl", 0.0)
        d.setdefault("pnl_inr", 0.0)
        d.setdefault("close_at", None)

        return Trade(**d)


class TradeTracker:
    def __init__(self, path: str = "data/trades.json") -> None:
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._trades: List[Trade] = self._load()

    def _load(self) -> List[Trade]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f) or []
            return [Trade.from_dict(x) for x in raw]
        except Exception:
            return []

    def _save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump([asdict(t) for t in self._trades], f, ensure_ascii=False, indent=2)

    def save(self) -> None:
        self._save()

    def add(self, trade: Trade) -> None:
        self._trades.append(trade)
        self._save()

    def list_open(self) -> List[Trade]:
        return [t for t in self._trades if t.status == "OPEN"]

    def count_open_by_category(self, category: str) -> int:
        return sum(1 for t in self._trades if t.status == "OPEN" and t.category == category)

    def find_open(
        self,
        category: str,
        symbol: str,
        direction: Optional[str] = None,
    ) -> Optional[Trade]:
        for t in self._trades:
            if t.status != "OPEN":
                continue
            if t.category != category:
                continue
            if t.symbol != symbol:
                continue
            if direction is not None and t.direction != direction:
                continue
            return t
        return None

    def upsert_evidence_scored(
        self,
        trade: Trade,
        evidence_url: str,
        *,
        score: int,
        confirm_score: int,
        extend_ttl_minutes: int = 0,
    ) -> bool:
        changed = False
        url_added = False

        if evidence_url and evidence_url not in trade.evidence_sources:
            trade.evidence_sources.append(evidence_url)
            url_added = True
            changed = True

        if url_added and score > 0:
            trade.evidence_score_total = int(trade.evidence_score_total) + int(score)
            changed = True

        if trade.quality == "RAW" and int(trade.evidence_score_total) >= int(confirm_score):
            trade.quality = "CONFIRMED"
            changed = True

        if extend_ttl_minutes and url_added and trade.expires_at:
            try:
                exp = datetime.fromisoformat(trade.expires_at)
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                exp = exp + timedelta(minutes=int(extend_ttl_minutes))
                trade.expires_at = exp.isoformat()
                changed = True
            except Exception:
                pass

        if changed:
            self._save()

        return changed

    def expire_due(self) -> List[Trade]:
        now = _utcnow()
        expired: List[Trade] = []
        for t in self._trades:
            if t.status != "OPEN" or not t.expires_at:
                continue
            try:
                exp = datetime.fromisoformat(t.expires_at)
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if exp <= now:
                t.status = "EXPIRED"
                t.close_reason = "ttl_expired"
                t.outcome = None
                t.close_price = None
                t.pnl_inr = 0.0
                expired.append(t)
        if expired:
            self._save()
        return expired

    def close_trade(self, trade_id: str, reason: str, price: float, outcome: str) -> Optional[Trade]:
        for t in self._trades:
            if t.id != trade_id:
                continue
            if t.close_at is not None:
                return t
            if t.status != "OPEN":
                return t

            t.status = "CLOSED"
            t.close_reason = reason
            t.close_at = _utcnow().isoformat()
            t.close_price = float(price)
            t.outcome = outcome

            # Realized PnL (INR): (close - entry) * point_value * lots, sign-adjusted for SELL
            try:
                realized_pnl = float(t.realized_pnl or 0.0)
                if realized_pnl == 0.0 and t.lots and t.point_value_inr and t.close_price is not None:
                    move = float(t.close_price) - float(t.entry)
                    if t.direction == "SELL":
                        move = -move
                    realized_pnl = float(move) * float(t.point_value_inr) * float(t.lots)
                t.realized_pnl = realized_pnl
                t.pnl_inr = realized_pnl
            except Exception:
                t.realized_pnl = float(t.realized_pnl or 0.0)
                t.pnl_inr = float(t.pnl_inr or t.realized_pnl)

            self._save()
            return t
        return None

    def close(self, trade_id: str, reason: str, price: float, outcome: str) -> Optional[Trade]:
        return self.close_trade(trade_id, reason, price, outcome)
