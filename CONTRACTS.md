# Contracts

## TTL Upgrade Semantics

Decision: **extend from existing expiry**, not reset from current time.

- Implemented behavior (source of truth):
  - `core/trade_tracker.py` in `TradeTracker.upsert_evidence_scored(...)`
  - Current logic:
    - `exp = datetime.fromisoformat(trade.expires_at)`
    - `exp = exp + timedelta(minutes=int(extend_ttl_minutes))`
    - `trade.expires_at = exp.isoformat()`

Equivalent contract formula:

`trade.expires_at = trade.expires_at + timedelta(minutes=extend_ttl_minutes)`

Not used:

`trade.expires_at = now + timedelta(minutes=extend_ttl_minutes)`

### Guard Conditions (must all be true)

- `extend_ttl_minutes` is truthy/non-zero
- a new evidence URL was added (`url_added == True`)
- `trade.expires_at` is present/non-empty
- existing `trade.expires_at` parses as ISO datetime

If parsing fails, TTL is left unchanged.

### Call Site

- `core/news_engine.py` passes:
  - `extend_ttl_minutes=self.upgrade_extend_ttl_minutes`
  - when updating an existing open trade from a new event.

## Stale Feed Detection

Decision: **Option A**.

- `empty_streak[feed]`:
  - increments by 1 on `ok_empty`
  - resets to 0 on `ok`
  - remains unchanged on `failed`
- `fail_streak[feed]`:
  - increments by 1 on `failed`
  - resets to 0 on `ok` or `ok_empty`
- `stale_feeds`:
  - feed is stale when `empty_streak >= 10`

## Equity

- Equity apply idempotency uses `applied_trade_ids` with retention/prune of 14 days.
- Lifecycle + equity boundary contract:
  - `EXPIRED` is terminal and does not affect equity.
  - `CLOSED` is terminal and can affect equity via realized PnL.
  - Allowed transitions: `OPEN -> EXPIRED`, `OPEN -> CLOSED`.
  - Disallowed transitions: `EXPIRED -> *`, `CLOSED -> *`.

## Trade State Persistence

- Load policy: fail fast on `data/trades.json` parse/schema errors (bot startup should halt).
- Save policy: write `data/trades.json.tmp` + fsync, copy current file to `data/trades.json.bak`, then atomic replace to `data/trades.json`.
- Recovery policy: no automatic fallback to `.bak` during load; if load fails, operator may manually replace `trades.json` with `trades.json.bak`.

## Quantity Rounding

- Rounding mode used by risk sizing: `floor`.
- `round_qty_to_lot(qty_raw, lot_size, mode="floor")` invalid-input policy:
  - raises `ValueError` when `lot_size is None`
  - raises `ValueError` when `lot_size <= 0`
  - raises `ValueError` when `qty_raw` is NaN/inf
  - raises `ValueError` when `qty_raw < 0`
- For `floor` mode, if `0 < qty_raw < lot_size`, rounded quantity is `0`.
