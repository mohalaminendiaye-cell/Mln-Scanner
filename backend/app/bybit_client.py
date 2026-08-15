"""
Client REST pour l'API publique Bybit v5 (Futures linéaires USDT-margés).
Documentation officielle : https://bybit-exchange.github.io/docs/v5/intro

⚠️ Intégration non testée en conditions réelles : mon environnement de
développement n'a pas d'accès internet vers bybit.com. Le code suit la
documentation publique officielle, mais vérifiez les premiers scans en
production et signalez toute erreur de schéma de réponse.

Tous les endpoints utilisés sont publics (pas de clé API requise) :
  - GET /v5/market/tickers        : prix + volume 24h
  - GET /v5/market/kline          : bougies OHLCV
  - GET /v5/market/open-interest  : historique d'Open Interest
  - GET /v5/market/funding/history: funding rate
  - GET /v5/market/orderbook      : carnet d'ordres (spread + déséquilibre)
"""
import asyncio
import logging
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .config import settings

logger = logging.getLogger("bybit_client")

BASE_URL = "https://api.bybit.com"
# Bybit utilise des intervalles en minutes ("60"=1h, "240"=4h, "D"=1jour)
INTERVAL_MAP = {"1h": "60", "4h": "240", "1d": "D"}


class BybitAPIError(Exception):
    pass


class BybitClient:
    def __init__(self):
        self._client: httpx.AsyncClient | None = None
        self._semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_REQUESTS)

    async def __aenter__(self):
        self._client = httpx.AsyncClient(base_url=BASE_URL, timeout=15.0)
        return self

    async def __aexit__(self, *exc):
        if self._client:
            await self._client.aclose()

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        retry=retry_if_exception_type(httpx.TransportError),
    )
    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        assert self._client is not None
        async with self._semaphore:
            resp = await self._client.get(path, params=params)
            if resp.status_code == 429:
                await asyncio.sleep(2)
                raise httpx.TransportError("rate limited")
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("retCode") != 0:
                raise BybitAPIError(f"{path}: {payload.get('retMsg', payload)}")
            return payload.get("result", {})

    async def get_top_symbols_by_volume(self, n: int, min_quote_volume: float = 0.0) -> list[str]:
        result = await self._get("/v5/market/tickers", params={"category": "linear"})
        tickers = result.get("list", [])
        usdt_pairs = [
            t for t in tickers
            if t.get("symbol", "").endswith("USDT")
            and float(t.get("turnover24h", 0) or 0) >= min_quote_volume
        ]
        usdt_pairs.sort(key=lambda t: float(t.get("turnover24h", 0) or 0), reverse=True)
        return [t["symbol"] for t in usdt_pairs[:n]]

    async def get_klines(self, symbol: str, interval: str, limit: int = 200) -> list[list]:
        bybit_interval = INTERVAL_MAP.get(interval, interval)
        result = await self._get(
            "/v5/market/kline",
            params={"category": "linear", "symbol": symbol, "interval": bybit_interval, "limit": min(limit, 1000)},
        )
        raw = result.get("list", [])
        # ⚠️ Bybit retourne 7 champs par bougie : [start, open, high, low,
        # close, volume, turnover]. On ne garde que les 6 premiers pour être
        # cohérent avec le format commun attendu par le reste de l'app.
        trimmed = [row[:6] for row in raw]
        return list(reversed(trimmed))

    async def get_funding_rate(self, symbol: str) -> float | None:
        try:
            result = await self._get(
                "/v5/market/funding/history",
                params={"category": "linear", "symbol": symbol, "limit": 1},
            )
            rows = result.get("list", [])
            return float(rows[0]["fundingRate"]) if rows else None
        except Exception:
            return None

    async def get_ticker_price(self, symbol: str) -> float | None:
        """Prix courant léger, utilisé pour le monitoring (backtest) et la watchlist.
        Analogue à BinanceFuturesClient.get_ticker_price, pour permettre le suivi des
        signaux Cat.10 ouverts sur Bybit."""
        try:
            result = await self._get("/v5/market/tickers", params={"category": "linear", "symbol": symbol})
            rows = result.get("list", [])
            return float(rows[0]["lastPrice"]) if rows else None
        except Exception:
            return None

    async def get_open_interest_change_pct(self, symbol: str, hours: int = 4) -> tuple[float | None, float | None]:
        """Retourne (OI courant en valeur, variation % sur ~`hours` heures)."""
        try:
            result = await self._get(
                "/v5/market/open-interest",
                params={"category": "linear", "symbol": symbol, "intervalTime": "1h", "limit": max(hours + 1, 2)},
            )
            rows = result.get("list", [])
            if len(rows) < 2:
                return (float(rows[0]["openInterest"]) if rows else None), None
            latest = float(rows[0]["openInterest"])
            oldest = float(rows[-1]["openInterest"])
            change_pct = round((latest / oldest - 1) * 100, 2) if oldest else None
            return latest, change_pct
        except Exception:
            return None, None

    async def get_orderbook_imbalance(self, symbol: str, pct_range: float = 2.0) -> float | None:
        """Ratio de déséquilibre du carnet d'ordres à ±pct_range% du mid-price.
        > 0.5 = pression acheteuse dominante, < 0.5 = pression vendeuse dominante."""
        try:
            result = await self._get("/v5/market/orderbook", params={"category": "linear", "symbol": symbol, "limit": 50})
            bids, asks = result.get("b", []), result.get("a", [])
            if not bids or not asks:
                return None
            mid = (float(bids[0][0]) + float(asks[0][0])) / 2
            lo, hi = mid * (1 - pct_range / 100), mid * (1 + pct_range / 100)
            bid_vol = sum(float(p) * float(q) for p, q in bids if float(p) >= lo)
            ask_vol = sum(float(p) * float(q) for p, q in asks if float(p) <= hi)
            total = bid_vol + ask_vol
            return round(bid_vol / total, 4) if total else None
        except Exception:
            return None

    async def get_spread_pct(self, symbol: str) -> float | None:
        try:
            result = await self._get("/v5/market/orderbook", params={"category": "linear", "symbol": symbol, "limit": 1})
            bids, asks = result.get("b", []), result.get("a", [])
            if not bids or not asks:
                return None
            best_bid, best_ask = float(bids[0][0]), float(asks[0][0])
            mid = (best_bid + best_ask) / 2
            return round((best_ask - best_bid) / mid * 100, 4) if mid else None
        except Exception:
            return None
