from __future__ import annotations

import pytest

from core.risk_utils import round_qty_to_lot


def test_round_qty_to_lot_invalid_lot_size():
    with pytest.raises(ValueError):
        round_qty_to_lot(1.0, 0.0, mode="floor")
    with pytest.raises(ValueError):
        round_qty_to_lot(1.0, -5.0, mode="floor")
