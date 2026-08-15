"""
Client REST pour l'API publique Hyperliquid (endpoint unique /info, POST JSON).
Documentation officielle : https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api

⚠️ Intégration non testée en conditions réelles : mon environnement de
développement n'a pas d'accès internet vers hyperliquid.xyz. Le code suit la
documentation publique officielle, mais vérifiez les premiers scans en
production et signalez toute erreur de schéma de réponse.

Hyperliquid n'a pas de paire "quote asset" comme USDT : chaque actif (ex: "BTC")
est coté contre USD directement sur son DEX de perpétuels. Pas de clé API
requise pour les données de marché publiques (endpoint "info").
"""
import asyncio
import logging
import time

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from .config import settings

logger = logging.getLogger("hyperliquid_client")

BASE_URL = "https://api.hyperliquid.xyz"
INTERVAL_MAP = {"1h": "1h", "4h": "4h", "1d": "1d"}
INTERVAL_MS = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


class HyperliquidClient:
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
    async def _info(self, body: dict) -> any:
        assert self._client is not None
        async with self._semaphore:
            resp = await self._client.post("/info", json=body)
            if resp.status_code == 429:
                await asyncio.sleep(2)
                raise httpx.TransportError("rate limited")
            resp.raise_for_status()
            return resp.json()

    async def get_top_symbols_by_volume(self, n: int, min_quote_volume: float = 0.0) -> list[str]:
        """Retourne les N noms d'actifs (ex: 'BTC') les plus liquides sur le DEX perp."""
        meta_and_ctxs = await self._info({"type": "metaAndAssetCtxs"})
        universe = meta_and_ctxs[0]["universe"]
        ctxs = meta_and_ctxs[1]
        pairs = []
        for asset, ctx in zip(universe, ctxs):
            vol = float(ctx.get("dayNtlVlm", 0) or 0)
            if vol >= min_quote_volume:
                pairs.append((asset["name"], vol))
        pairs.sort(key=lambda p: p[1], reverse=True)
        return [p[0] for p in pairs[:n]]

    async def get_klines(self, coin: str, interval: str, limit: int = 200) -> list[list]:
        bar = INTERVAL_MAP.get(interval, interval)
        span_ms = INTERVAL_MS.get(interval, 3_600_000) * limit
        end_time = int(time.time() * 1000)
        start_time = end_time - span_ms
        raw = await self._info(
            {
                "type": "candleSnapshot",
                "req": {"coin": coin, "interval": bar, "startTime": start_time, "endTime": end_time},
            }
        )
        # Format Hyperliquid: [{"t":open_time,"T":close_time,"o":..,"h":..,"l":..,"c":..,"v":..}, ...]
        return [
            [c["t"], c["o"], c["h"], c["l"], c["c"], c["v"]]
            for c in (raw or [])
        ]

    async def get_funding_and_oi(self, coin: str) -> tuple[float | None, float | None]:
        """Retourne (funding_rate, open_interest_usd) pour un actif donné, à
        partir du contexte de marché global (moins coûteux que N appels)."""
        try:
            meta_and_ctxs = await self._info({"type": "metaAndAssetCtxs"})
            universe = meta_and_ctxs[0]["universe"]
            ctxs = meta_and_ctxs[1]
            for asset, ctx in zip(universe, ctxs):
                if asset["name"] == coin:
                    funding = float(ctx.get("funding", 0) or 0)
                    mark_px = float(ctx.get("markPx", 0) or 0)
                    oi_coins = float(ctx.get("openInterest", 0) or 0)
                    oi_usd = oi_coins * mark_px if mark_px else None
                    return funding, oi_usd
            return None, None
        except Exception:
            return None, None

    async def get_spread_pct(self, coin: str) -> float | None:
        try:
            book = await self._info({"type": "l2Book", "coin": coin})
            levels = book.get("levels", [[], []])
            if not levels[0] or not levels[1]:
                return None
            best_bid = float(levels[0][0]["px"])
            best_ask = float(levels[1][0]["px"])
            mid = (best_ask + best_bid) / 2
            return round((best_ask - best_bid) / mid * 100, 4) if mid else None
        except Exception:
            return None
