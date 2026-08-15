"""
Client REST pour l'API publique OKX v5 (Swap perpétuels USDT-margés).
Documentation officielle : https://www.okx.com/docs-v5/en/

⚠️ Intégration non testée en conditions réelles : mon environnement de
développement n'a pas d'accès internet vers okx.com. Le code suit la
documentation publique officielle, mais vérifiez les premiers scans en
production et signalez toute erreur de schéma de réponse.

Tous les endpoints utilisés ici sont publics (pas de clé API requise) :
  - GET /api/v5/public/instruments   : liste des contrats swap
  - GET /api/v5/market/tickers       : prix + volume 24h de tous les swaps
  - GET /api/v5/market/candles       : bougies OHLCV
  - GET /api/v5/public/open-interest : Open Interest
  - GET /api/v5/public/funding-rate  : funding rate
  - GET /api/v5/market/books         : carnet d'ordres (pour le spread)
"""
import asyncio
import logging
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .config import settings

logger = logging.getLogger("okx_client")

BASE_URL = "https://www.okx.com"
# OKX utilise "1H"/"4H"/"1Dutc" pour des bougies alignées sur le jour UTC
BAR_MAP = {"1h": "1H", "4h": "4H", "1d": "1Dutc"}


class OKXAPIError(Exception):
    pass


class OKXClient:
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
    async def _get(self, path: str, params: dict[str, Any] | None = None) -> list:
        assert self._client is not None
        async with self._semaphore:
            resp = await self._client.get(path, params=params)
            if resp.status_code == 429:
                await asyncio.sleep(2)
                raise httpx.TransportError("rate limited")
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("code") not in ("0", 0):
                raise OKXAPIError(f"{path}: {payload.get('msg', payload)}")
            return payload.get("data", [])

    async def get_top_symbols_by_volume(self, n: int, min_quote_volume: float = 0.0) -> list[str]:
        """Retourne les N instId (ex: 'BTC-USDT-SWAP') les plus liquides."""
        tickers = await self._get("/api/v5/market/tickers", params={"instType": "SWAP"})
        usdt_swaps = [
            t for t in tickers
            if t.get("instId", "").endswith("-USDT-SWAP")
            and float(t.get("volCcy24h", 0) or 0) >= min_quote_volume
        ]
        usdt_swaps.sort(key=lambda t: float(t.get("volCcy24h", 0) or 0), reverse=True)
        return [t["instId"] for t in usdt_swaps[:n]]

    async def get_klines(self, inst_id: str, interval: str, limit: int = 200) -> list[list]:
        bar = BAR_MAP.get(interval, interval)
        raw = await self._get(
            "/api/v5/market/candles",
            params={"instId": inst_id, "bar": bar, "limit": min(limit, 300)},
        )
        # ⚠️ OKX retourne 9 champs par bougie : [ts, o, h, l, c, vol, volCcy,
        # volCcyQuote, confirm]. On ne garde que les 6 premiers (ts,o,h,l,c,vol)
        # pour être cohérent avec le format commun attendu par le reste de l'app.
        # OKX retourne aussi du plus récent au plus ancien -> on inverse.
        trimmed = [row[:6] for row in raw]
        return list(reversed(trimmed))

    async def get_funding_rate(self, inst_id: str) -> float | None:
        try:
            data = await self._get("/api/v5/public/funding-rate", params={"instId": inst_id})
            return float(data[0]["fundingRate"]) if data else None
        except Exception:
            return None

    async def get_open_interest(self, inst_id: str) -> tuple[float | None, float | None]:
        """Retourne (OI en valeur notionnelle USD, None) — OKX ne fournit pas
        nativement l'historique d'OI sur cet endpoint public simple, donc la
        variation 24h n'est pas calculable ici (contrairement à Binance)."""
        try:
            data = await self._get(
                "/api/v5/public/open-interest", params={"instType": "SWAP", "instId": inst_id}
            )
            if not data:
                return None, None
            oi_usd = float(data[0].get("oiCcy", 0) or 0)
            return oi_usd, None
        except Exception:
            return None, None

    async def get_spread_pct(self, inst_id: str) -> float | None:
        try:
            data = await self._get("/api/v5/market/books", params={"instId": inst_id, "sz": 1})
            if not data:
                return None
            best_ask = float(data[0]["asks"][0][0])
            best_bid = float(data[0]["bids"][0][0])
            mid = (best_ask + best_bid) / 2
            return round((best_ask - best_bid) / mid * 100, 4) if mid else None
        except Exception:
            return None
