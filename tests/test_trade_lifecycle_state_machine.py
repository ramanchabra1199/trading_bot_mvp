from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from core.equity_engine import EquityEngine
from core.trade_tracker import Trade, TradeStateTransitionError, TradeTracker


def _mk_trade(trade_id: str, *, status: str = "OPEN", expires_at: str = "") -> Trade:
    t = Trade(
        id=trade_id,
        category="fx_usdinr",
        symbol="USDINR",
        direction="BUY",
        entry=100.0,
        tp=101.0,
        sl=99.0,
        expires_at=expires_at,
        status=status,
    )
    if status == "CLOSED":
        t.close_at = datetime.now(timezone.utc).isoformat()
        t.close_reason = "tp_hit"
        t.close_price = 101.0
        t.outcome = "TP"
    elif status == "EXPIRED":
        t.close_reason = "ttl_expired"
        t.outcome = None
        t.close_price = None
    return t


def test_terminal_states_do_not_transition(tmp_path):
    tracker = TradeTracker(path=str(tmp_path / "trades.json"))
    closed = _mk_trade("T_CLOSED", status="CLOSED")
    expired = _mk_trade("T_EXPIRED", status="EXPIRED")
    tracker.add(closed)
    tracker.add(expired)

    with pytest.raises(TradeStateTransitionError):
        tracker.close_trade("T_CLOSED", "tp_hit", 101.0, "TP")
    with pytest.raises(TradeStateTransitionError):
        tracker.close_trade("T_EXPIRED", "tp_hit", 101.0, "TP")


@pytest.mark.parametrize(
    ("old_status", "new_status", "ok"),
    [
        ("OPEN", "CLOSED", True),
        ("OPEN", "EXPIRED", True),
        ("OPEN", "OPEN", False),
        ("CLOSED", "CLOSED", False),
        ("CLOSED", "EXPIRED", False),
        ("EXPIRED", "CLOSED", False),
        ("EXPIRED", "EXPIRED", False),
    ],
)
def test_transition_matrix(old_status, new_status, ok, tmp_path):
    tracker = TradeTracker(path=str(tmp_path / "trades.json"))
    if ok:
        tracker._validate_transition(old_status, new_status)
    else:
        with pytest.raises(TradeStateTransitionError):
            tracker._validate_transition(old_status, new_status)


def test_expire_due_is_idempotent(tmp_path):
    tracker = TradeTracker(path=str(tmp_path / "trades.json"))
    now = datetime.now(timezone.utc)
    t = _mk_trade("T1", status="OPEN", expires_at=(now - timedelta(minutes=1)).isoformat())
    tracker.add(t)

    first = tracker.expire_due(now=now)
    second = tracker.expire_due(now=now)

    assert len(first) == 1
    assert first[0].status == "EXPIRED"
    assert second == []


def test_expire_due_boundary_now_equal_expires_at(tmp_path):
    tracker = TradeTracker(path=str(tmp_path / "trades.json"))
    now = datetime.now(timezone.utc)
    t = _mk_trade("T_BOUNDARY_EQ", status="OPEN", expires_at=now.isoformat())
    tracker.add(t)

    expired = tracker.expire_due(now=now)
    assert len(expired) == 1
    assert expired[0].status == "EXPIRED"


def test_expire_due_boundary_now_before_expires_at(tmp_path):
    tracker = TradeTracker(path=str(tmp_path / "trades.json"))
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(microseconds=1)
    t = _mk_trade("T_BOUNDARY_LT", status="OPEN", expires_at=expires_at.isoformat())
    tracker.add(t)

    expired = tracker.expire_due(now=now)
    assert expired == []
    still_open = tracker.find_open("fx_usdinr", "USDINR", direction="BUY")
    assert still_open is not None
    assert still_open.id == "T_BOUNDARY_LT"


def test_restart_reconciliation_only_expires_open(tmp_path):
    path = tmp_path / "trades.json"
    now = datetime.now(timezone.utc)
    rows = [
        {
            "id": "T_OPEN_EXPIRED",
            "category": "fx_usdinr",
            "symbol": "USDINR",
            "direction": "BUY",
            "entry": 100.0,
            "tp": 101.0,
            "sl": 99.0,
            "status": "OPEN",
            "expires_at": (now - timedelta(minutes=1)).isoformat(),
        },
        {
            "id": "T_OPEN_FRESH",
            "category": "fx_usdinr",
            "symbol": "USDINR",
            "direction": "BUY",
            "entry": 100.0,
            "tp": 101.0,
            "sl": 99.0,
            "status": "OPEN",
            "expires_at": (now + timedelta(minutes=30)).isoformat(),
        },
        {
            "id": "T_CLOSED",
            "category": "fx_usdinr",
            "symbol": "USDINR",
            "direction": "BUY",
            "entry": 100.0,
            "tp": 101.0,
            "sl": 99.0,
            "status": "CLOSED",
            "close_at": now.isoformat(),
            "close_reason": "tp_hit",
            "close_price": 101.0,
            "outcome": "TP",
        },
        {
            "id": "T_EXPIRED",
            "category": "fx_usdinr",
            "symbol": "USDINR",
            "direction": "BUY",
            "entry": 100.0,
            "tp": 101.0,
            "sl": 99.0,
            "status": "EXPIRED",
            "close_reason": "ttl_expired",
            "outcome": None,
            "close_price": None,
            "expires_at": (now - timedelta(minutes=5)).isoformat(),
        },
    ]
    path.write_text(json.dumps(rows), encoding="utf-8")

    tracker = TradeTracker(path=str(path))
    expired = tracker.expire_due(now=now)
    assert len(expired) == 1
    assert expired[0].id == "T_OPEN_EXPIRED"

    by_id = {t.id: t for t in tracker._trades}
    assert by_id["T_OPEN_EXPIRED"].status == "EXPIRED"
    assert by_id["T_OPEN_FRESH"].status == "OPEN"
    assert by_id["T_CLOSED"].status == "CLOSED"
    assert by_id["T_EXPIRED"].status == "EXPIRED"


def test_close_then_expire_and_expire_then_close_ordering(tmp_path):
    tracker = TradeTracker(path=str(tmp_path / "trades.json"))
    now = datetime.now(timezone.utc)

    first = _mk_trade("T_ORDER_1", status="OPEN", expires_at=(now + timedelta(minutes=5)).isoformat())
    tracker.add(first)
    tracker.close_trade("T_ORDER_1", "tp_hit", 101.0, "TP")
    assert tracker.expire_due(now=now) == []

    second = _mk_trade("T_ORDER_2", status="OPEN", expires_at=(now - timedelta(seconds=1)).isoformat())
    tracker.add(second)
    tracker.expire_due(now=now)
    with pytest.raises(TradeStateTransitionError):
        tracker.close_trade("T_ORDER_2", "tp_hit", 101.0, "TP")


def test_equity_boundary_expired_no_effect_closed_applies(tmp_path):
    equity = EquityEngine(path=str(tmp_path / "equity.json"), start_equity_inr=100000.0)

    expired = _mk_trade("T_EXPIRED_EQ", status="EXPIRED")
    expired.realized_pnl = 5000.0
    expired.pnl_inr = 5000.0
    equity.apply_closed_trades([expired])
    assert equity.current_equity() == 100000.0

    closed = _mk_trade("T_CLOSED_EQ", status="CLOSED")
    closed.realized_pnl = 1000.0
    closed.pnl_inr = 1000.0
    equity.apply_closed_trades([closed])
    assert equity.current_equity() == 101000.0


def test_equity_idempotency_persists_across_restart(tmp_path):
    equity_path = tmp_path / "equity.json"
    eq1 = EquityEngine(path=str(equity_path), start_equity_inr=100000.0)
    closed = _mk_trade("T_RESTART_EQ", status="CLOSED")
    closed.realized_pnl = 1200.0
    closed.pnl_inr = 1200.0

    eq1.apply_closed_trades([closed])
    assert eq1.current_equity() == 101200.0

    eq2 = EquityEngine(path=str(equity_path), start_equity_inr=100000.0)
    eq2.apply_closed_trades([closed])
    assert eq2.current_equity() == 101200.0
