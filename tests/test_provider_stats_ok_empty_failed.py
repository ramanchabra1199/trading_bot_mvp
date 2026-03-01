from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import feedparser

from core.equity_engine import EquityEngine
from core.news_engine import NewsEngine
from core.risk_engine import InstrumentSpec, RiskEngine
from core.trade_tracker import TradeTracker
from core.kpi_engine import KPIEngine
from core.market_engine import MarketEngine
from providers.news_provider_base import UnifiedNewsItem
from providers.rss_provider import RSSProvider


# Prevents regression: provider health always reports OK and hides dead/empty feeds
def test_provider_stats_ok_empty_failed(tmp_path, monkeypatch):
    def fake_parse(url):
        if "ok" in url:
            entry = SimpleNamespace(
                title="War headline",
                link="https://example.com/a",
                summary="",
                published_parsed=(2026, 1, 1, 0, 0, 0, 0, 0, 0),
            )
            return SimpleNamespace(entries=[entry])
        if "empty" in url:
            return SimpleNamespace(entries=[])
        raise RuntimeError("boom")

    monkeypatch.setattr(feedparser, "parse", fake_parse)

    provider = RSSProvider({"ok_feed": "ok", "empty_feed": "empty", "bad_feed": "bad"})
    items, stats = provider.fetch_with_stats()

    assert len(items) == 1
    assert stats["providers_ok"] == 1
    assert stats["providers_ok_empty"] == 1
    assert stats["providers_failed"] == 1
    assert stats["empty_feeds"] == ["empty_feed"]
    assert "bad_feed" in stats["failures"]

    # Also ensure NewsEngine exposes the same fields in run result.
    fixed_items = [
        UnifiedNewsItem(
            provider="ok_feed",
            title="War headline",
            url="https://example.com/a",
            summary="",
            published_at=datetime.now(timezone.utc),
        )
    ]

    def fake_fetch_with_stats(self):
        return fixed_items, stats

    monkeypatch.setattr(RSSProvider, "fetch_with_stats", fake_fetch_with_stats)

    instruments = {
        "GOLD": InstrumentSpec(
            yfinance_symbol="GC=F",
            point_value_inr=1.0,
            lot_step=1.0,
            lot_min=1.0,
            atr_low_pct=0.0,
            atr_high_pct=10.0,
            factors=tuple(),
            tier=1,
        )
    }

    news = NewsEngine(
        feeds={"ok_feed": "ok", "empty_feed": "empty", "bad_feed": "bad"},
        category_rules={},
        trade_map={},
        tier2_rules={},
        instruments=instruments,
        max_age_minutes=240,
        confirm_window_minutes=30,
        similarity_threshold=0.5,
        upgrade_extend_ttl_minutes=10,
        max_open_per_category=3,
        market=MarketEngine(cache_seconds=0),
        tracker=TradeTracker(path=str(tmp_path / "trades.json")),
        kpi=KPIEngine(path=str(tmp_path / "kpi.json")),
        risk=RiskEngine(
            equity_inr=100000.0,
            risk_pct_per_trade=0.01,
            max_portfolio_risk_pct=0.03,
            max_factor_trades=1,
            instruments=instruments,
        ),
        equity=EquityEngine(path=str(tmp_path / "equity.json"), start_equity_inr=100000.0),
    )

    out = news.run_once()
    assert out.providers_ok == 1
    assert out.providers_ok_empty == 1
    assert out.providers_failed == 1
    assert out.empty_feeds == ["empty_feed"]
    assert "bad_feed" in (out.failures or {})
