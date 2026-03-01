from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from typing import Dict, List

from core.trade_tracker import Trade


@dataclass
class KPI:
    total_closed: int = 0
    hits_tp: int = 0
    hits_sl: int = 0
    accuracy_pct: float = 0.0


class KPIEngine:
    def __init__(self, path: str = "data/kpi.json") -> None:
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.kpi = self._load()

    def _load(self) -> KPI:
        if not os.path.exists(self.path):
            return KPI()
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d: Dict = json.load(f) or {}
            return KPI(**d)
        except Exception:
            return KPI()

    def _save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(asdict(self.kpi), f, ensure_ascii=False, indent=2)

    def update_from_closed(self, closed_trades: List[Trade]) -> None:
        # Only count trades with TP/SL outcomes
        for t in closed_trades:
            if t.outcome not in ("TP", "SL"):
                continue
            self.kpi.total_closed += 1
            if t.outcome == "TP":
                self.kpi.hits_tp += 1
            else:
                self.kpi.hits_sl += 1

        total = self.kpi.total_closed
        self.kpi.accuracy_pct = (self.kpi.hits_tp / total * 100.0) if total else 0.0
        self._save()

    def summary(self) -> str:
        k = self.kpi
        return (
            "📊 KPI (trade outcome accuracy)\n"
            f"- Closed counted: {k.total_closed}\n"
            f"- TP hits: {k.hits_tp}\n"
            f"- SL hits: {k.hits_sl}\n"
            f"- Accuracy: {k.accuracy_pct:.1f}%"
        )
