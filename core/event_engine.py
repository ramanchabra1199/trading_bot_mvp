# core/event_engine.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from providers.news_provider_base import UnifiedNewsItem


@dataclass
class EventItem:
    category: str
    title: str
    url: str
    provider: str
    published_at: Optional[datetime]
    reason: str  # e.g. keyword hit(s)


@dataclass
class Event:
    category: str
    items: List[EventItem]


def _norm_text(s: str) -> str:
    return " ".join((s or "").lower().split())


class EventEngine:
    """
    Category rules are passed from config.yml:

      category_rules = {
        "geopolitics": {"any_keywords": ["war", "sanctions", ...]},
        "rates_macro": {"any_keywords": [...]},
        ...
      }

    Backward compatible:
      - also accepts {"keywords": [...]} (legacy)
    """

    # Hard India anchors for india_policy to avoid misclassifying US/EU trade headlines.
    _INDIA_POLICY_ANCHORS = (
        "india",
        "indian",
        "rbi",
        "sebi",
        "rupee",
        "inr",
        "usd/inr",
        "usdinr",
        "new delhi",
        "finance ministry",
        "ministry of finance",
        "gst",
        "union budget",
    )

    def __init__(self, category_rules: Dict[str, Any]) -> None:
        self.category_rules = category_rules or {}

        # Flatten to: category -> [keywords...]
        self._keywords: Dict[str, List[str]] = {}
        for cat, spec in (self.category_rules or {}).items():
            spec = spec or {}

            # Support BOTH keys: any_keywords (current YAML) and keywords (legacy)
            kws = spec.get("any_keywords")
            if not kws:
                kws = spec.get("keywords")
            if not kws:
                kws = []

            # normalize and clean
            normed: List[str] = []
            for k in kws:
                ks = str(k).strip().lower()
                if ks:
                    normed.append(ks)

            self._keywords[str(cat)] = normed

    def classify(self, item: UnifiedNewsItem) -> Tuple[str, str]:
        title = item.title or ""
        summary = getattr(item, "summary", "") or ""
        text = _norm_text(title + " " + summary)

        best_cat = "unknown"
        best_hits: List[str] = []
        best_count = 0

        # Best-hit-wins (more robust than first-hit-wins)
        for cat, kws in self._keywords.items():
            if not kws:
                continue

            # India policy hard gate (prevents US/EU "tariff" headlines polluting india_policy)
            if cat == "india_policy":
                if not any(a in text for a in self._INDIA_POLICY_ANCHORS):
                    continue

            hits = [kw for kw in kws if kw in text]
            if len(hits) > best_count:
                best_count = len(hits)
                best_cat = cat
                best_hits = hits

        if best_cat == "unknown":
            return "unknown", "no_rule_match"

        preview = ",".join(best_hits[:6])
        return best_cat, f"kw_hits={best_count}:{preview}"

    def build_event_items(self, items: List[UnifiedNewsItem]) -> List[EventItem]:
        out: List[EventItem] = []
        for it in items or []:
            category, reason = self.classify(it)
            out.append(
                EventItem(
                    category=category,
                    title=it.title or "",
                    url=it.url or "",
                    provider=it.provider or "",
                    published_at=it.published_at,
                    reason=reason,
                )
            )
        return out

    def group_events(self, event_items: List[EventItem]) -> List[Event]:
        grouped: Dict[str, List[EventItem]] = {}
        for ei in event_items or []:
            grouped.setdefault(ei.category, []).append(ei)

        def _ts(x: EventItem) -> float:
            if not x.published_at:
                return 0.0
            try:
                return x.published_at.timestamp()
            except Exception:
                return 0.0

        events: List[Event] = []
        for c, v in grouped.items():
            v_sorted = sorted(v, key=_ts, reverse=True)
            events.append(Event(category=c, items=v_sorted))

        events.sort(key=lambda ev: _ts(ev.items[0]) if ev.items else 0.0, reverse=True)
        return events

    def build_event_summary(self, events: List[Event], top_n: int = 6, tz=None) -> str:
        lines = ["✅ News events (grouped):", ""]
        for ev in events:
            for it in ev.items[:top_n]:
                ts = self._fmt_time(it.published_at, tz)
                lines.append(f"[{ev.category}] {ts} {it.title}")
                lines.append(f"Publishers: {it.provider}")
                lines.append("")
        return "\n".join(lines).strip()

    def build_raw_summary(self, event_items: List[EventItem], top_n: int = 6, tz=None) -> str:
        lines = ["🗞️ Latest headlines (RAW):", ""]
        for it in (event_items or [])[:top_n]:
            ts = self._fmt_time(it.published_at, tz)
            lines.append(f"[{it.category}] {ts} {it.title}")
            lines.append(f"• {it.provider}")
            lines.append(f"• {it.url}")
            lines.append("")
        return "\n".join(lines).strip()

    def _fmt_time(self, dt: Optional[datetime], tz) -> str:
        if not dt:
            return "[time?]"
        if tz is None:
            return dt.strftime("[%Y-%m-%d %H:%M]")
        try:
            return dt.astimezone(tz).strftime("[%Y-%m-%d %H:%M]")
        except Exception:
            return dt.strftime("[%Y-%m-%d %H:%M]")