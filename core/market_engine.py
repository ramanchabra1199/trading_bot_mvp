from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import yfinance as yf

log = logging.getLogger(__name__)


@dataclass
class QuoteResult:
    ok: bool
    price: Optional[float] = None
    error: str = ""


@dataclass(frozen=True)
class Candle:
    ts: int
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0


class MarketEngine:
    """
    Price fetcher with:
      - small in-memory cache
      - multiple fallback quote paths
      - configurable sanity ranges

    NEW:
      - get_candles() for technical indicators (EMA/RSI/ATR)
    """

    def __init__(self, cache_seconds: int = 20) -> None:
        self.cache_seconds = int(cache_seconds)
        self._cache: Dict[str, Tuple[float, float]] = {}  # symbol -> (ts, price)

        # Candle cache: (symbol, period, interval) -> (ts, candles)
        self._candle_cache: Dict[Tuple[str, str, str], Tuple[float, List[Candle]]] = {}

        # Loose guardrails; add more as needed.
        self.sanity_ranges = {
            "GC=F": (500.0, 20000.0),
            "CL=F": (5.0, 500.0),
            "SI=F": (1.0, 500.0),
            "EURUSD=X": (0.5, 2.0),
            "GBPUSD=X": (0.5, 3.0),
            "USDINR=X": (30.0, 200.0),
            "^NSEI": (1000.0, 50000.0),
            "^NSEBANK": (1000.0, 200000.0),
            "^CNXIT": (1000.0, 100000.0),
            "^CNXMETAL": (1000.0, 100000.0),
            "^CNXENERGY": (1000.0, 100000.0),
        }

    def get_last_price(self, symbol: str) -> QuoteResult:
        symbol = (symbol or "").strip()
        if not symbol:
            return QuoteResult(False, None, "empty_symbol")

        now = time.time()
        cached = self._cache.get(symbol)
        if cached:
            ts, px = cached
            if now - ts <= self.cache_seconds:
                return QuoteResult(True, px, "cache")

        try:
            t = yf.Ticker(symbol)

            price = self._try_fast_info(t)
            if price is None:
                price = self._try_intraday(t)
            if price is None:
                price = self._try_daily(t)

            if price is None:
                return QuoteResult(False, None, "no_price")

            ok, err = self._sanity_check(symbol, float(price))
            if not ok:
                return QuoteResult(False, None, err)

            self._cache[symbol] = (now, float(price))
            return QuoteResult(True, float(price), "")
        except Exception as e:
            log.exception("Quote fetch failed symbol=%s", symbol)
            return QuoteResult(False, None, f"exception:{type(e).__name__}")

    def get_candles(
        self,
        symbol: str,
        *,
        period: str = "5d",
        interval: str = "15m",
        limit: int = 300,
        cache_seconds: int = 30,
    ) -> List[Candle]:
        """
        NEW: Fetch OHLCV candles via yfinance for technical indicators.
        """
        symbol = (symbol or "").strip()
        if not symbol:
            return []

        key = (symbol, period, interval)
        now = time.time()

        cached = self._candle_cache.get(key)
        if cached:
            ts, candles = cached
            if now - ts <= cache_seconds:
                return candles[-limit:] if limit else candles

        try:
            t = yf.Ticker(symbol)
            hist = t.history(period=period, interval=interval)
            if hist is None or hist.empty:
                return []

            candles: List[Candle] = []
            # hist index is datetime; convert to unix seconds
            for idx, row in hist.iterrows():
                try:
                    ts = int(idx.to_pydatetime().timestamp())
                    candles.append(
                        Candle(
                            ts=ts,
                            o=float(row.get("Open", 0.0)),
                            h=float(row.get("High", 0.0)),
                            l=float(row.get("Low", 0.0)),
                            c=float(row.get("Close", 0.0)),
                            v=float(row.get("Volume", 0.0) or 0.0),
                        )
                    )
                except Exception:
                    continue

            self._candle_cache[key] = (now, candles)
            return candles[-limit:] if limit else candles
        except Exception:
            return []

    def get_atr_percent(self, symbol: str, period_days: int = 40, atr_len: int = 14) -> Optional[float]:
        """
        ATR% = ATR / last_close * 100
        Uses DAILY OHLC from yfinance.
        """
        symbol = (symbol or "").strip()
        if not symbol:
            return None
        try:
            t = yf.Ticker(symbol)
            hist = t.history(period=f"{int(period_days)}d", interval="1d")
            if hist is None or hist.empty or len(hist) < 2:
                return None

            high = hist["High"]
            low = hist["Low"]
            close = hist["Close"]

            prev_close = close.shift(1)

            tr1 = (high - low).abs()
            tr2 = (high - prev_close).abs()
            tr3 = (low - prev_close).abs()

            tr = tr1.copy()
            tr = tr.where(tr >= tr2, tr2)
            tr = tr.where(tr >= tr3, tr3)

            atr = tr.rolling(int(atr_len)).mean().iloc[-1]
            last = float(close.iloc[-1])

            if last <= 0:
                return None
            if atr != atr:
                return None

            return float(atr) / last * 100.0
        except Exception:
            return None

    def _try_fast_info(self, t: yf.Ticker) -> Optional[float]:
        try:
            fi = getattr(t, "fast_info", None)
            if not fi:
                return None

            for key in ("last_price", "lastPrice", "regularMarketPrice"):
                if key in fi and fi[key] is not None:
                    return float(fi[key])

            lp = getattr(fi, "last_price", None)
            if lp is not None:
                return float(lp)
        except Exception:
            return None
        return None

    def _try_intraday(self, t: yf.Ticker) -> Optional[float]:
        try:
            hist = t.history(period="1d", interval="1m")
            if hist is None or hist.empty:
                return None
            return float(hist["Close"].iloc[-1])
        except Exception:
            return None

    def _try_daily(self, t: yf.Ticker) -> Optional[float]:
        try:
            hist = t.history(period="5d", interval="1d")
            if hist is None or hist.empty:
                return None
            return float(hist["Close"].iloc[-1])
        except Exception:
            return None

    def _sanity_check(self, symbol: str, price: float) -> Tuple[bool, str]:
        if price <= 0 or price != price:
            return False, f"invalid_price:{symbol}:{price}"

        rng = self.sanity_ranges.get(symbol)
        if rng:
            lo, hi = rng
            if not (lo <= price <= hi):
                return False, f"sanity_reject:{symbol}:price={price}:range=({lo},{hi})"

        return True, ""