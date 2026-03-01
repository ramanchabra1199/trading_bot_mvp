from __future__ import annotations

import json

import pytest

from core.trade_tracker import Trade, TradeStateLoadError, TradeTracker


def test_trade_tracker_malformed_json_fails_fast(tmp_path):
    path = tmp_path / "trades.json"
    path.write_text("{bad json", encoding="utf-8")

    with pytest.raises(TradeStateLoadError):
        TradeTracker(path=str(path))


def test_trade_tracker_atomic_save_and_reload(tmp_path):
    path = tmp_path / "trades.json"
    tracker = TradeTracker(path=str(path))
    trade = Trade(
        id="T1",
        category="fx_usdinr",
        symbol="USDINR",
        direction="BUY",
        entry=100.0,
        tp=101.0,
        sl=99.0,
    )
    tracker.add(trade)

    reloaded = TradeTracker(path=str(path))
    rows = json.loads(path.read_text(encoding="utf-8"))

    assert isinstance(rows, list)
    assert len(rows) == 1
    assert reloaded.list_open()[0].id == "T1"


def test_trade_tracker_creates_backup_on_second_save(tmp_path):
    path = tmp_path / "trades.json"
    bak = tmp_path / "trades.json.bak"
    tracker = TradeTracker(path=str(path))

    tracker.add(
        Trade(
            id="T1",
            category="fx_usdinr",
            symbol="USDINR",
            direction="BUY",
            entry=100.0,
            tp=101.0,
            sl=99.0,
        )
    )
    assert not bak.exists()

    tracker.add(
        Trade(
            id="T2",
            category="fx_usdinr",
            symbol="USDINR",
            direction="BUY",
            entry=101.0,
            tp=102.0,
            sl=100.0,
        )
    )

    assert bak.exists()
    bak_rows = json.loads(bak.read_text(encoding="utf-8"))
    assert isinstance(bak_rows, list)
    assert len(bak_rows) >= 1
