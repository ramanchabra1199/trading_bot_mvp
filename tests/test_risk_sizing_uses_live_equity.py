from __future__ import annotations

from math import isclose

from core.risk_engine import InstrumentSpec, RiskEngine


# Prevents regression: position sizing ignores live realized equity
def test_risk_sizing_uses_live_equity():
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

    hi = risk.approve(
        symbol="USDINR",
        category="fx_usdinr",
        entry=100.0,
        sl=99.0,
        open_trades=[],
        atr_percent=1.0,
        equity_inr=100000.0,
    )
    lo = risk.approve(
        symbol="USDINR",
        category="fx_usdinr",
        entry=100.0,
        sl=99.0,
        open_trades=[],
        atr_percent=1.0,
        equity_inr=50000.0,
    )

    assert hi.ok is True
    assert lo.ok is True
    assert isclose(hi.lots, 1000.0, rel_tol=1e-6)
    assert isclose(lo.lots, 500.0, rel_tol=1e-6)
