from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.equity_engine import EquityEngine


def test_equity_applied_trade_ids_prune(tmp_path):
    eq = EquityEngine(path=str(tmp_path / "equity.json"), start_equity_inr=100000.0)
    now = datetime.now(timezone.utc)

    eq.state.applied_trade_ids = {
        "old_trade": now - timedelta(days=15),
        "recent_trade": now - timedelta(days=1),
    }

    eq.prune_applied_trade_ids(now=now, retention_days=14)

    assert "old_trade" not in eq.state.applied_trade_ids
    assert "recent_trade" in eq.state.applied_trade_ids
