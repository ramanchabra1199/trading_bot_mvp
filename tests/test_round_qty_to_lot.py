from __future__ import annotations

from core.risk_engine import InstrumentSpec, RiskEngine
from core.risk_utils import round_qty_to_lot


def test_round_qty_to_lot_modes_and_wiring():
    assert round_qty_to_lot(10.7, 1.0, mode="floor") == 10.0
    assert round_qty_to_lot(10.1, 1.0, mode="ceil") == 11.0
    assert round_qty_to_lot(10.49, 1.0, mode="nearest") == 10.0
    assert round_qty_to_lot(10.51, 1.0, mode="nearest") == 11.0
    assert round_qty_to_lot(0.0, 1.0, mode="floor") == 0.0

    instruments = {
        "USDINR": InstrumentSpec(
            yfinance_symbol="USDINR=X",
            point_value_inr=1.0,
            lot_step=0.25,
            lot_min=0.25,
            atr_low_pct=0.0,
            atr_high_pct=10.0,
            factors=tuple(),
            tier=1,
        )
    }
    risk = RiskEngine(
        equity_inr=100000.0,
        risk_pct_per_trade=0.001234,  # chosen to produce non-integer qty
        max_portfolio_risk_pct=0.03,
        max_factor_trades=1,
        instruments=instruments,
    )
    dec = risk.approve(
        symbol="USDINR",
        category="fx_usdinr",
        entry=100.0,
        sl=99.0,
        open_trades=[],
        atr_percent=1.0,
        equity_inr=100000.0,
    )

    assert dec.ok is True
    assert dec.qty_raw > dec.qty_rounded
    assert dec.lots == dec.qty_rounded
    assert dec.rounding_applied is True
