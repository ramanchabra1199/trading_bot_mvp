# core/signal_engine.py
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from core.config import TRADE_RULES
from core.event_engine import EventItem


@dataclass
class Signal:
    category: str
    symbol: str
    side: str  # BUY/SELL
    reason: str


def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())


class SignalEngine:
    def generate(self, event_items: List[EventItem]) -> List[Signal]:
        signals: List[Signal] = []

        for ei in event_items:
            rules = TRADE_RULES.get(ei.category, [])
            if not rules:
                continue

            text = _norm(ei.title)
            for r in rules:
                hits = sum(1 for kw in r.strong_keywords if kw.lower() in text)
                if hits >= r.min_hits:
                    signals.append(
                        Signal(
                            category=ei.category,
                            symbol=r.symbol,
                            side=r.side,
                            reason=f"{ei.reason};trade_rule:{r.symbol}:{r.side};strong_hits={hits}",
                        )
                    )
        return signals
