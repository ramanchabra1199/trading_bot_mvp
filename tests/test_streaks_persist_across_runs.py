from __future__ import annotations

from core.equity_engine import EquityEngine
from core.kpi_engine import KPIEngine
from core.market_engine import MarketEngine
from core.news_engine import NewsEngine
from core.risk_engine import InstrumentSpec, RiskEngine
from core.trade_tracker import TradeTracker
from providers.rss_provider import RSSProvider


def test_streaks_persist_across_runs(tmp_path, monkeypatch):
    feed = "feed_a"
    runs = [
        (
            [],
            {
                "providers_ok": 0,
                "providers_ok_empty": 1,
                "providers_failed": 0,
                "ok_feeds": [],
                "empty_feeds": [feed],
                "failed_feeds": [],
                "failures": {},
            },
        ),
        (
            [],
            {
                "providers_ok": 0,
                "providers_ok_empty": 1,
                "providers_failed": 0,
                "ok_feeds": [],
                "empty_feeds": [feed],
                "failed_feeds": [],
                "failures": {},
            },
        ),
        (
            [],
            {
                "providers_ok": 0,
                "providers_ok_empty": 1,
                "providers_failed": 0,
                "ok_feeds": [],
                "empty_feeds": [feed],
                "failed_feeds": [],
                "failures": {},
            },
        ),
        (
            [],
            {
                "providers_ok": 1,
                "providers_ok_empty": 0,
                "providers_failed": 0,
                "ok_feeds": [feed],
                "empty_feeds": [],
                "failed_feeds": [],
                "failures": {},
            },
        ),
    ]

    def fake_fetch_with_stats(self):
        return runs.pop(0)

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

    engine = NewsEngine(
        feeds={feed: "https://example.com/rss"},
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

    engine.run_once()
    engine.run_once()
    engine.run_once()
    assert engine.empty_streak[feed] == 3

    engine.run_once()
    assert engine.empty_streak[feed] == 0
