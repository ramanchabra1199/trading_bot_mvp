from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.risk_engine import InstrumentSpec, RiskEngine
from core.trade_tracker import Trade


# Prevents regression: portfolio risk cap uses startup equity instead of live equity
def test_risk_caps_use_live_equity():
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

    open_trade = Trade(
        id="O1",
        category="fx_usdinr",
        symbol="USDINR",
        direction="BUY",
        entry=100.0,
        tp=102.0,
        sl=99.0,
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
        risk_inr=1200.0,
        lots=100.0,
        point_value_inr=1.0,
    )

    hi = risk.approve(
        symbol="USDINR",
        category="fx_usdinr",
        entry=100.0,
        sl=99.0,
        open_trades=[open_trade],
        atr_percent=1.0,
        equity_inr=100000.0,
    )
    lo = risk.approve(
        symbol="USDINR",
        category="fx_usdinr",
        entry=100.0,
        sl=99.0,
        open_trades=[open_trade],
        atr_percent=1.0,
        equity_inr=50000.0,
    )

    assert hi.ok is True
    assert lo.ok is False
    assert "portfolio_risk_cap" in lo.reason
