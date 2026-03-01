from __future__ import annotations

import math


def round_qty_to_lot(qty: float, lot_size: float, mode: str = "floor") -> float:
    qty_f = float(qty)
    if not math.isfinite(qty_f):
        raise ValueError("qty must be finite")
    if qty_f < 0.0:
        raise ValueError("qty must be >= 0")
    if qty_f == 0.0:
        return 0.0
    if lot_size is None:
        raise ValueError("lot_size is required")
    lot_f = float(lot_size)
    if not math.isfinite(lot_f):
        raise ValueError("lot_size must be finite")
    if lot_f <= 0.0:
        raise ValueError("lot_size must be > 0")

    units = qty_f / lot_f
    m = (mode or "floor").strip().lower()
    if m == "ceil":
        n = math.ceil(units)
    elif m == "nearest":
        n = int(round(units))
    else:
        n = math.floor(units)

    out = float(n) * lot_f
    if out < 0.0:
        return 0.0
    return float(out)
