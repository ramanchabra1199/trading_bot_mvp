from __future__ import annotations

from core.equity_engine import EquityEngine
from core.kpi_engine import KPIEngine
from core.market_engine import MarketEngine
from core.news_engine import NewsEngine
from core.risk_engine import InstrumentSpec, RiskEngine
from core.trade_tracker import TradeTracker


# Prevents regression: timestamp-based ID collisions under burst
def test_trade_id_uniqueness(tmp_path):
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

    news = NewsEngine(
        feeds={},
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
        risk=risk,
        equity=EquityEngine(path=str(tmp_path / "equity.json"), start_equity_inr=100000.0),
    )

    ids = [news._new_trade_id() for _ in range(5000)]
    assert len(set(ids)) == len(ids)
