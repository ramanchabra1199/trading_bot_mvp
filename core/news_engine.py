# core/news_engine.py
from __future__ import annotations

import copy
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from zoneinfo import ZoneInfo

from providers.rss_provider import RSSProvider
from providers.news_provider_base import UnifiedNewsItem
from core.event_engine import EventEngine, EventItem, Event
from core.market_engine import MarketEngine
from core.trade_tracker import TradeTracker, Trade
from core.kpi_engine import KPIEngine
from core.risk_engine import RiskEngine
from core.equity_engine import EquityEngine

# NEW agents
from core.technical_agent import TechnicalAgent
from core.risk_manager_agent import RiskManagerAgent
from core import config as cfg

if TYPE_CHECKING:
    from core.risk_engine import InstrumentSpec

log = logging.getLogger(__name__)

_TRACKING_PARAMS_PREFIXES = ("utm_",)
_TRACKING_PARAMS_EXACT = {
    "mod", "guccounter", "guce_referrer", "guce_referrer_sig", "ncid", "cmpid",
    "src", "soc_src", "soc_trk", "siteid", "yptr", "feature", "spm", "ref",
}

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "has", "have", "had", "he", "her", "his", "how", "i", "if", "in", "into",
    "is", "it", "its", "just", "may", "more", "most", "new", "not", "of", "on",
    "or", "our", "out", "says", "say", "so", "than", "that", "the", "their",
    "they", "this", "to", "up", "was", "we", "were", "what", "when", "where",
    "which", "who", "will", "with", "you", "your",
    "major", "shares", "share", "stock", "stocks", "update", "report",
    "electric",
}

_NEAR_DUP_JACCARD_DEFAULT = 0.85
_STALE_EMPTY_STREAK_THRESHOLD_DEFAULT = 10


def _norm_title(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def _canon_url(url: str) -> str:
    try:
        p = urlparse((url or "").strip())
        if not p.netloc:
            return (url or "").strip()

        scheme = (p.scheme or "https").lower()
        netloc = p.netloc.lower()

        q = []
        for k, v in parse_qsl(p.query, keep_blank_values=True):
            k_l = (k or "").lower()
            if any(k_l.startswith(pref) for pref in _TRACKING_PARAMS_PREFIXES):
                continue
            if k_l in _TRACKING_PARAMS_EXACT:
                continue
            q.append((k, v))

        query = urlencode(q, doseq=True)
        return urlunparse((scheme, netloc, p.path, "", query, ""))
    except Exception:
        return (url or "").strip()


def _title_tokens(title: str) -> List[str]:
    t = _norm_title(title)
    toks = re.findall(r"[a-z0-9]+", t)
    toks = [x for x in toks if x not in _STOPWORDS]
    return toks


def _title_fingerprint(title: str) -> str:
    toks = _title_tokens(title)
    if not toks:
        toks = re.findall(r"[a-z0-9]+", _norm_title(title))
    return " ".join(toks)


def _time_bucket(it: UnifiedNewsItem, bucket_minutes: int = 60) -> int:
    if not it.published_at:
        return 0
    try:
        ts = int(it.published_at.timestamp())
        return ts // (bucket_minutes * 60)
    except Exception:
        return 0


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _norm_text(s: str) -> str:
    return " ".join((s or "").lower().split())


@dataclass
class NewsRunResult:
    raw_items: List[UnifiedNewsItem]
    event_items: List[EventItem]
    events: List[Event]
    created_trades: List[Trade]
    upgraded_trades: List[Trade]
    expired_trades: List[Trade]
    closed_trades: List[Trade]
    providers_ok: int = 0
    providers_ok_empty: int = 0
    providers_failed: int = 0
    empty_feeds: List[str] = None
    stale_feeds: List[str] = None
    failures: Dict[str, str] = None
    items_before_filter: int = 0
    items_after_filter: int = 0


class NewsEngine:
    def __init__(
        self,
        *,
        feeds: Dict[str, str],
        category_rules: Dict,
        trade_map: Dict[str, Dict[str, Any]],
        tier2_rules: Dict[str, Dict[str, Any]],
        instruments: Dict[str, "InstrumentSpec"],
        max_age_minutes: int,
        confirm_window_minutes: int,
        similarity_threshold: float,  # compat
        upgrade_extend_ttl_minutes: int,
        max_open_per_category: int,
        market: MarketEngine,
        tracker: TradeTracker,
        kpi: KPIEngine,
        risk: RiskEngine,
        equity: EquityEngine,
    ) -> None:
        self.feeds = feeds or {}
        self.max_age_minutes = int(max_age_minutes)
        self.confirm_window_minutes = int(confirm_window_minutes)
        self.upgrade_extend_ttl_minutes = int(upgrade_extend_ttl_minutes)
        self.max_open_per_category = int(max_open_per_category)
        st = float(similarity_threshold)
        if st <= 0.0 or st > 1.0:
            st = _NEAR_DUP_JACCARD_DEFAULT
        self.near_dup_jaccard = st
        self.stale_empty_streak_threshold = _STALE_EMPTY_STREAK_THRESHOLD_DEFAULT
        self.empty_streak: Dict[str, int] = {str(k): 0 for k in self.feeds.keys()}
        self.fail_streak: Dict[str, int] = {str(k): 0 for k in self.feeds.keys()}

        self.market = market
        self.tracker = tracker
        self.kpi = kpi
        self.risk = risk
        self.equity = equity

        self.instruments = instruments
        self.trade_map = trade_map or {}
        self.tier2_rules = tier2_rules or {}

        self.event_engine = EventEngine(category_rules=category_rules)

        # NEW agents
        self.tech_agent = TechnicalAgent()
        self.rr_agent = RiskManagerAgent()

        # Geopolitics escalation scoring (TITLE-based)
        self._geo_weights_word: Dict[str, int] = {
            "attack": 3,
            "airstrike": 3,
            "missile": 3,
            "invasion": 4,
            "nuclear": 4,
            "bombing": 4,
            "sanctions": 2,
            "escalation": 2,
        }
        self._geo_weights_phrase: Dict[str, int] = {
            "drone strike": 3,
            "military strike": 3,
        }

        self.tz = ZoneInfo("UTC")

    def set_timezone(self, tz_name: str) -> None:
        try:
            self.tz = ZoneInfo(tz_name)
        except Exception:
            self.tz = ZoneInfo("UTC")

    def run_once(self) -> NewsRunResult:
        provider = RSSProvider(self.feeds)
        items, stats = provider.fetch_with_stats()
        providers_ok = int(stats.get("providers_ok", 0))
        providers_ok_empty = int(stats.get("providers_ok_empty", 0))
        providers_failed = int(stats.get("providers_failed", 0))
        ok_feeds = set(str(x) for x in (stats.get("ok_feeds", []) or []))
        empty_feeds = list(stats.get("empty_feeds", []) or [])
        failed_feeds = set(str(x) for x in (stats.get("failed_feeds", []) or []))
        failures = dict(stats.get("failures", {}) or {})
        empty_feed_set = set(str(x) for x in empty_feeds)
        for feed_name in self.feeds.keys():
            fn = str(feed_name)
            if fn in ok_feeds:
                self.empty_streak[fn] = 0
                self.fail_streak[fn] = 0
            elif fn in empty_feed_set:
                self.empty_streak[fn] = int(self.empty_streak.get(fn, 0)) + 1
                self.fail_streak[fn] = 0
            elif fn in failed_feeds:
                # Option A: failure streak is tracked separately; empty streak remains unchanged.
                self.fail_streak[fn] = int(self.fail_streak.get(fn, 0)) + 1

        stale_feeds = [
            fn for fn, streak in self.empty_streak.items()
            if int(streak) >= int(self.stale_empty_streak_threshold)
        ]
        items_before = len(items)

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.max_age_minutes)

        filtered: List[UnifiedNewsItem] = []
        for it in items:
            if not it.title or not it.url:
                continue
            if it.published_at and it.published_at < cutoff:
                continue
            filtered.append(it)

        seen_url: set[str] = set()
        seen_fp: set[str] = set()
        seen_token_sets: Dict[str, List[set[str]]] = {}

        deduped: List[UnifiedNewsItem] = []

        for it in filtered:
            canon = _canon_url(it.url)
            host = urlparse(canon).hostname or urlparse(it.url).hostname or ""
            bucket = _time_bucket(it, bucket_minutes=60)

            url_key = canon
            fp = _title_fingerprint(it.title)

            group_key = f"{it.provider}|{host}|{bucket}"
            fp_key = f"{group_key}|{fp}"

            if url_key in seen_url:
                continue
            if fp_key in seen_fp:
                continue

            toks = set(_title_tokens(it.title))
            is_near_dup = False
            if toks:
                for prev in seen_token_sets.get(group_key, []):
                    if _jaccard(toks, prev) >= self.near_dup_jaccard:
                        is_near_dup = True
                        break
            if is_near_dup:
                continue

            seen_url.add(url_key)
            seen_fp.add(fp_key)
            seen_token_sets.setdefault(group_key, []).append(toks)
            deduped.append(it)

        event_items = self.event_engine.build_event_items(deduped)
        events = self.event_engine.group_events(event_items)

        # lifecycle first
        expired = self.tracker.expire_due()
        closed = self._check_tp_sl_and_close_open_trades()
        if closed:
            self.kpi.update_from_closed(closed)
            self.equity.apply_closed_trades(closed)

        created: List[Trade] = []
        upgraded: List[Trade] = []

        for ei in event_items:
            ct, ut = self._maybe_create_or_upgrade_trade_from_event(ei)
            if ct:
                created.append(copy.deepcopy(ct))
            if ut:
                upgraded.append(copy.deepcopy(ut))

        return NewsRunResult(
            raw_items=deduped,
            event_items=event_items,
            events=events,
            created_trades=created,
            upgraded_trades=upgraded,
            expired_trades=expired,
            closed_trades=closed,
            items_before_filter=items_before,
            items_after_filter=len(deduped),
            providers_ok=providers_ok,
            providers_ok_empty=providers_ok_empty,
            providers_failed=providers_failed,
            empty_feeds=empty_feeds,
            stale_feeds=stale_feeds,
            failures=failures,
        )

    def _geo_score(self, title: str) -> int:
        t = _norm_title(title)
        t_for_words = t.replace("-", " ")
        words = set(re.findall(r"[a-z0-9]+", t_for_words))

        score = 0
        for w, pts in self._geo_weights_word.items():
            if w in words:
                score += int(pts)

        for phr, pts in self._geo_weights_phrase.items():
            phr_n = _norm_title(phr)
            if phr_n in t:
                score += int(pts)

        return score

    def _tier2_allowed(self, internal_symbol: str, title: str, summary: str) -> bool:
        spec = self.instruments.get(internal_symbol)
        if not spec:
            return False
        if int(spec.tier) != 2:
            return True

        gate = self.tier2_rules.get(internal_symbol) or {}
        req = gate.get("require_any_keywords") or []
        if not req:
            # If tier2 has no gate, default to BLOCK (safer)
            return False

        text = _norm_text((title or "") + " " + (summary or ""))
        return any(str(k).lower() in text for k in req)

    def _maybe_create_or_upgrade_trade_from_event(self, ei: EventItem) -> Tuple[Optional[Trade], Optional[Trade]]:
        if ei.category not in self.trade_map:
            return None, None

        spec_map = self.trade_map.get(ei.category) or {}
        internal_symbol = str(spec_map.get("symbol", "")).strip()
        direction = str(spec_map.get("direction", "BUY")).strip().upper()

        if not internal_symbol or internal_symbol not in self.instruments:
            return None, None
        if direction not in ("BUY", "SELL"):
            return None, None

        # Tier2 activation gate
        if not self._tier2_allowed(internal_symbol, ei.title, ""):
            return None, None

        create_score = int(spec_map.get("create_score", 1))
        confirm_score = int(spec_map.get("confirm_score", 3))

        # scoring
        score = 1
        if ei.category == "geopolitics":
            score = self._geo_score(ei.title)
            if score < create_score:
                return None, None

        existing = self.tracker.find_open(ei.category, internal_symbol, direction=direction)
        if existing:
            before_quality = existing.quality
            changed = self.tracker.upsert_evidence_scored(
                existing,
                evidence_url=ei.url,
                score=score,
                confirm_score=confirm_score,
                extend_ttl_minutes=self.upgrade_extend_ttl_minutes,
            )
            if changed and existing.quality != before_quality:
                return None, existing
            return None, None

        if self.tracker.count_open_by_category(ei.category) >= self.max_open_per_category:
            return None, None

        inst = self.instruments[internal_symbol]
        yf_symbol = inst.yfinance_symbol

        q = self.market.get_last_price(yf_symbol)
        if not q.ok or q.price is None:
            log.warning("Skipping trade: quote error %s", q.error)
            return None, None

        entry = float(q.price)

        # ------------------------------------------------------------
        # Technical Agent (STANDARD/SHOCK) -> produces SL
        # ------------------------------------------------------------
        sl: Optional[float] = None
        tech_notes = ""
        mode = "STANDARD"

        if cfg.TECH_ENABLED:
            candles = self.market.get_candles(
                yf_symbol,
                period=cfg.TECH_TF_PERIOD,
                interval=cfg.TECH_TF_INTERVAL,
                limit=cfg.TECH_LIMIT,
            )
            min_need = max(
                int(cfg.TECH_EMA_SLOW),
                int(cfg.TECH_RSI_PERIOD) + 2,
                int(cfg.TECH_ATR_PERIOD) + int(cfg.ATR_SMA_PERIOD),
                50,
            )
            got = len(candles)
            if got < min_need:
                log.debug(
                    "TECH candles symbol=%s tf=%s period=%s got=%s required=%s",
                    internal_symbol,
                    cfg.TECH_TF_INTERVAL,
                    cfg.TECH_TF_PERIOD,
                    got,
                    min_need,
                )
                log.warning(
                    "TECH blocked symbol=%s category=%s reason=not_enough_candles:%s<%s",
                    internal_symbol,
                    ei.category,
                    got,
                    min_need,
                )
                return None, None

            verdict = self.tech_agent.evaluate(
                candles=candles,
                side=direction,
                event_score=score,
            )

            if not verdict.ok or verdict.sl is None:
                log.info(
                    "TECH blocked symbol=%s category=%s mode=%s notes=%s",
                    internal_symbol,
                    ei.category,
                    verdict.mode,
                    verdict.notes,
                )
                return None, None

            sl = float(verdict.sl)
            mode = verdict.mode
            tech_notes = f"{verdict.mode}:{verdict.notes}"

        # If technical disabled, fall back to your old multipliers (compat)
        if sl is None:
            tp_mult = float(spec_map.get("tp_mult", 1.003))
            sl_mult = float(spec_map.get("sl_mult", 0.997))
            if direction == "BUY":
                tp = entry * tp_mult
                sl = entry * sl_mult
            else:
                tp = entry * sl_mult
                sl = entry * tp_mult
            rr_notes = "rr_skip:tech_disabled"
        else:
            # ------------------------------------------------------------
            # Risk Manager Agent -> enforce min RR and compute TP
            # ------------------------------------------------------------
            min_rr = float(spec_map.get("min_rr", cfg.MIN_RR_DEFAULT))
            rr_dec = self.rr_agent.compute_tp(entry=entry, sl=sl, side=direction, min_rr=min_rr)
            if not rr_dec.ok or rr_dec.tp is None:
                log.info("RR blocked symbol=%s category=%s notes=%s", internal_symbol, ei.category, rr_dec.notes)
                return None, None
            tp = float(rr_dec.tp)
            rr_notes = rr_dec.notes

        # Regime (ATR%) using yfinance daily OHLC
        atrp = self.market.get_atr_percent(yf_symbol)
        eq_for_risk = self.equity.current_equity()
        log.info("equity_used_for_risk=%s equity_mode=realized_only", f"{eq_for_risk:.6f}")

        # Risk approval (position size + exposure + portfolio cap + regime filter)
        decision = self.risk.approve(
            symbol=internal_symbol,
            category=ei.category,
            entry=entry,
            sl=float(sl),
            open_trades=self.tracker.list_open(),
            atr_percent=atrp,
            equity_inr=eq_for_risk,
        )
        if not decision.ok:
            log.info("Trade blocked symbol=%s category=%s reason=%s", internal_symbol, ei.category, decision.reason)
            return None, None

        # ------------------------------------------------------------
        # Shock mode size scaling (RiskDecision is frozen; scale locally)
        # ------------------------------------------------------------
        lots_final = float(decision.lots)
        risk_inr_final = float(decision.risk_inr)
        if mode == "SHOCK":
            m = float(cfg.SHOCK_SIZE_MULTIPLIER)
            lots_final = lots_final * m
            risk_inr_final = risk_inr_final * m

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=self.confirm_window_minutes)

        trade = Trade(
            id=self._new_trade_id(),
            category=ei.category,
            symbol=internal_symbol,
            direction=direction,
            entry=entry,
            tp=float(tp),
            sl=float(sl),
            quality="RAW",
            expires_at=expires_at.isoformat(),
            evidence_sources=[ei.url],
            evidence_score_total=int(score),
            lots=float(lots_final),
            risk_inr=float(risk_inr_final),
            point_value_inr=float(inst.point_value_inr),
        )

        try:
            trade.evidence_sources.append(f"meta:MODE:{mode}")
            trade.evidence_sources.append(f"meta:TECH:{tech_notes}")
            trade.evidence_sources.append(f"meta:RR:{rr_notes}")
        except Exception:
            pass

        self.tracker.add(trade)
        return trade, None

    def _check_tp_sl_and_close_open_trades(self) -> List[Trade]:
        closed: List[Trade] = []
        for t in self.tracker.list_open():
            inst = self.instruments.get(t.symbol)
            if not inst:
                continue

            q = self.market.get_last_price(inst.yfinance_symbol)
            if not q.ok or q.price is None:
                continue
            px = float(q.price)

            if t.direction == "BUY":
                if px >= t.tp:
                    ct = self.tracker.close(t.id, "tp_hit", px, "TP")
                    if ct:
                        closed.append(ct)
                elif px <= t.sl:
                    ct = self.tracker.close(t.id, "sl_hit", px, "SL")
                    if ct:
                        closed.append(ct)
            else:
                if px <= t.tp:
                    ct = self.tracker.close(t.id, "tp_hit", px, "TP")
                    if ct:
                        closed.append(ct)
                elif px >= t.sl:
                    ct = self.tracker.close(t.id, "sl_hit", px, "SL")
                    if ct:
                        closed.append(ct)

        return closed

    def _new_trade_id(self) -> str:
        return f"T{uuid.uuid4().hex[:16]}"
