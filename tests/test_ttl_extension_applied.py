from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.equity_engine import EquityEngine
from core.event_engine import EventItem
from core.kpi_engine import KPIEngine
from core.market_engine import MarketEngine
from core.news_engine import NewsEngine
from core.risk_engine import InstrumentSpec, RiskEngine
from core.trade_tracker import Trade, TradeTracker


# Prevents regression: upgrade_extend_ttl_minutes ignored / hardcoded 0
def test_ttl_extension_applied(tmp_path):
    tracker = TradeTracker(path=str(tmp_path / "trades.json"))
    kpi = KPIEngine(path=str(tmp_path / "kpi.json"))
    equity = EquityEngine(path=str(tmp_path / "equity.json"), start_equity_inr=100000.0)

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

    old_exp = datetime.now(timezone.utc) + timedelta(minutes=10)
    existing = Trade(
        id="T1",
        category="fx_usdinr",
        symbol="USDINR",
        direction="BUY",
        entry=100.0,
        tp=102.0,
        sl=99.0,
        quality="RAW",
        expires_at=old_exp.isoformat(),
        evidence_sources=["https://old"],
        evidence_score_total=1,
    )
    tracker.add(existing)

    news = NewsEngine(
        feeds={},
        category_rules={},
        trade_map={
            "fx_usdinr": {
                "symbol": "USDINR",
                "direction": "BUY",
                "create_score": 1,
                "confirm_score": 3,
                "min_rr": 2.0,
            }
        },
        tier2_rules={},
        instruments=instruments,
        max_age_minutes=240,
        confirm_window_minutes=30,
        similarity_threshold=0.5,
        upgrade_extend_ttl_minutes=25,
        max_open_per_category=3,
        market=MarketEngine(cache_seconds=0),
        tracker=tracker,
        kpi=kpi,
        risk=risk,
        equity=equity,
    )

    event = EventItem(
        category="fx_usdinr",
        title="USDINR moving",
        url="https://new",
        provider="p",
        published_at=datetime.now(timezone.utc),
        reason="kw",
    )

    created, upgraded = news._maybe_create_or_upgrade_trade_from_event(event)
    assert created is None
    assert upgraded is None

    updated = tracker.find_open("fx_usdinr", "USDINR", direction="BUY")
    assert updated is not None
    new_exp = datetime.fromisoformat(updated.expires_at)
    assert (new_exp - old_exp) >= timedelta(minutes=24, seconds=59)
