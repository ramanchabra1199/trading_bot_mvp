from __future__ import annotations

from datetime import datetime, timezone

from core.equity_engine import EquityEngine
from core.kpi_engine import KPIEngine
from core.market_engine import MarketEngine
from core.news_engine import NewsEngine
from core.risk_engine import InstrumentSpec, RiskEngine
from core.trade_tracker import TradeTracker
from providers.news_provider_base import UnifiedNewsItem
from providers.rss_provider import RSSProvider


# Prevents regression: similarity_threshold knob not wired to dedup
def test_dedup_threshold_wiring(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    items = [
        UnifiedNewsItem(
            provider="FXStreet",
            title="Alpha beta gamma",
            url="https://example.com/a",
            summary="",
            published_at=now,
        ),
        UnifiedNewsItem(
            provider="FXStreet",
            title="Alpha beta delta",
            url="https://example.com/b",
            summary="",
            published_at=now,
        ),
    ]

    def fake_fetch_with_stats(self):
        return items, {
            "providers_ok": 1,
            "providers_ok_empty": 0,
            "providers_failed": 0,
            "empty_feeds": [],
            "failures": {},
        }

    monkeypatch.setattr(RSSProvider, "fetch_with_stats", fake_fetch_with_stats)

    instruments = {
        "USDINR": InstrumentSpec(
            yfinance_symbol="USDINR=X",
            point_value_inr=1.0,
            lot_step=1.0,
            lot_min=1.0,
            atr_low_pct=0.0,
            atr_high_pct=10.0,
            factors=tuple(),
            tier=1,
        )
    }
    risk = RiskEngine(
        equity_inr=100000.0,
        risk_pct_per_trade=0.01,
        max_portfolio_risk_pct=0.03,
        max_factor_trades=1,
        instruments=instruments,
    )

    def mk_news(name: str, threshold: float):
        base = tmp_path / name
        base.mkdir(parents=True, exist_ok=True)
        return NewsEngine(
            feeds={"FXStreet": "https://example.com/rss"},
            category_rules={},
            trade_map={},
            tier2_rules={},
            instruments=instruments,
            max_age_minutes=240,
            confirm_window_minutes=30,
            similarity_threshold=threshold,
            upgrade_extend_ttl_minutes=10,
            max_open_per_category=3,
            market=MarketEngine(cache_seconds=0),
            tracker=TradeTracker(path=str(base / "trades.json")),
            kpi=KPIEngine(path=str(base / "kpi.json")),
            risk=risk,
            equity=EquityEngine(path=str(base / "equity.json"), start_equity_inr=100000.0),
        )

    out_strict = mk_news("strict", 0.8).run_once()
    out_loose = mk_news("loose", 0.4).run_once()

    assert out_strict.items_after_filter == 2
    assert out_loose.items_after_filter == 1
