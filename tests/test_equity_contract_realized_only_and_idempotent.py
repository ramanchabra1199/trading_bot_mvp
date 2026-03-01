from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.equity_engine import EquityEngine
from core.trade_tracker import Trade, TradeStateTransitionError, TradeTracker


# Prevents regression: equity includes MTM or double-counts PnL on repeated close
def test_equity_contract_realized_only_and_idempotent(tmp_path):
    trades_path = tmp_path / "trades.json"
    equity_path = tmp_path / "equity.json"

    tracker = TradeTracker(path=str(trades_path))
    equity = EquityEngine(path=str(equity_path), start_equity_inr=100000.0)

    trade = Trade(
        id="T1",
        category="geopolitics",
        symbol="GOLD",
        direction="BUY",
        entry=100.0,
        tp=105.0,
        sl=95.0,
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
        lots=2.0,
        point_value_inr=10.0,
        risk_inr=100.0,
    )
    tracker.add(trade)

    eq0 = equity.current_equity()
    assert eq0 == 100000.0

    # Open/create does not realize PnL.
    assert equity.current_equity() == eq0

    closed = tracker.close_trade("T1", "tp_hit", 103.0, "TP")
    assert closed is not None
    equity.apply_closed_trades([closed])
    eq1 = equity.current_equity()
    assert eq1 == 100060.0

    # Repeated close must fail loudly under terminal-state contract.
    with pytest.raises(TradeStateTransitionError):
        tracker.close_trade("T1", "tp_hit", 103.0, "TP")
    assert equity.current_equity() == eq1

    # Repeated reads are side-effect free.
    assert equity.current_equity() == eq1
    assert equity.current_equity() == eq1
