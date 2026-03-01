from __future__ import annotations

import math

import pytest

from core.risk_utils import round_qty_to_lot


def test_round_qty_to_lot_invalid_qty():
    with pytest.raises(ValueError):
        round_qty_to_lot(-1.0, 1.0, mode="floor")
    with pytest.raises(ValueError):
        round_qty_to_lot(math.nan, 1.0, mode="floor")
