from __future__ import annotations

import logging

from core.equity_engine import EquityEngine
from core.trade_tracker import Trade


def test_equity_double_apply_ignored(tmp_path, caplog):
    eq = EquityEngine(path=str(tmp_path / "equity.json"), start_equity_inr=100000.0)
    t = Trade(
        id="T1",
        category="x",
        symbol="USDINR",
        direction="BUY",
        entry=100.0,
        tp=101.0,
        sl=99.0,
        status="CLOSED",
        pnl_inr=250.0,
    )

    with caplog.at_level(logging.INFO):
        eq.apply_closed_trades([t])
        eq.apply_closed_trades([t])

    assert eq.current_equity() == 100250.0
    assert "equity_apply_skipped trade_id=T1 reason=already_applied" in caplog.text
