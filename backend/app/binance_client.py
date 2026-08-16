"""
Client HTTP asynchrone pour l'API publique Binance Futures (USDT-M).
Gère :
  - le retry avec backoff exponentiel (tenacity)
  - les erreurs de rate limit (HTTP 429 / 418) via lecture du header Retry-After
  - la reconnexion automatique en cas de timeout réseau
"""
import asyncio
import logging
from typing import Any

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from .config import settings

logger = logging.getLogger("binance_client")


class BinanceRateLimitError(Exception):
    """Levée quand Binance renvoie 429/418 (rate limit dépassé)."""


class BinanceAPIError(Exception):
    """Erreur générique retournée par l'API Binance."""


class BinanceFuturesClient:
    def __init__(self, base_url: str = settings.BINANCE_BASE_URL):
        self.base_url = base_url
        self._client: httpx.AsyncClient | None = None
        self._semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_REQUESTS)

    async def __aenter__(self):
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=15.0)
        return self

    async def __aexit__(self, *exc):
        if self._client:
            await self._client.aclose()

    def _track_used_weight(self, resp: httpx.Response, path: str) -> None:
        """Journalise le poids d'API consommé (header Binance) et avertit si on
        approche la limite (1200/min sur la plupart des endpoints Futures publics),
        pour repérer un TOP_N_SYMBOLS trop élevé avant de se faire bannir."""
        weight_header = resp.headers.get("X-MBX-USED-WEIGHT-1M") or resp.headers.get("x-mbx-used-weight-1m")
        if weight_header is None:
            return
        try:
            weight = int(weight_header)
        except ValueError:
            return
        if weight >= 1000:
            logger.warning(
                f"Poids API Binance élevé: {weight}/1200 sur la dernière minute "
                f"(après {path}). Envisagez de réduire TOP_N_SYMBOLS ou "
                f"MAX_CONCURRENT_REQUESTS si ce message est fréquent."
            )
        elif weight >= 700:
            logger.info(f"Poids API Binance: {weight}/1200 sur la dernière minute.")

    @retry(
        reraise=True,
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        retry=retry_if_exception_type((httpx.TransportError, BinanceRateLimitError)),
    )
    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        assert self._client is not None, "Utiliser le client via 'async with'"
        async with self._semaphore:
            try:
                resp = await self._client.get(path, params=params)
            except httpx.TransportError as e:
                logger.warning(f"Erreur réseau sur {path}: {e}. Nouvelle tentative...")
                raise

            self._track_used_weight(resp, path)

            if resp.status_code in (429, 418):
                retry_after = int(resp.headers.get("Retry-After", "5"))
                logger.warning(
                    f"Rate limit Binance atteint ({resp.status_code}). "
                    f"Pause de {retry_after}s avant retry."
                )
                await asyncio.sleep(retry_after)
                raise BinanceRateLimitError(f"Rate limited on {path}")

            if resp.status_code >= 400:
                raise BinanceAPIError(f"{resp.status_code} sur {path}: {resp.text[:200]}")

            return resp.json()

    # ------------------------------------------------------------------ #
    # Endpoints publics utilisés par le scanner
    # ------------------------------------------------------------------ #

    async def get_exchange_info(self) -> dict:
        return await self._get("/fapi/v1/exchangeInfo")

    async def get_24h_tickers(self) -> list[dict]:
        """Ticker 24h pour TOUTES les paires (volume, variation, etc.)."""
        return await self._get("/fapi/v1/ticker/24hr")

    async def get_klines(self, symbol: str, interval: str, limit: int = 200) -> list[list]:
        """Bougies OHLCV. interval ex: '1h', '4h'."""
        return await self._get(
            "/fapi/v1/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
        )

    async def get_funding_rate(self, symbol: str) -> float | None:
        data = await self._get("/fapi/v1/premiumIndex", params={"symbol": symbol})
        try:
            return float(data["lastFundingRate"])
        except (KeyError, TypeError, ValueError):
            return None

    async def get_open_interest(self, symbol: str) -> float | None:
        data = await self._get("/fapi/v1/openInterest", params={"symbol": symbol})
        try:
            return float(data["openInterest"])
        except (KeyError, TypeError, ValueError):
            return None

    async def get_open_interest_change_24h_pct(self, symbol: str) -> float | None:
        """Variation de l'Open Interest sur ~24h, via l'historique horaire (endpoint
        /futures/data/openInterestHist). Retourne None si l'historique est insuffisant."""
        return await self._open_interest_change_pct(symbol, points=25)

    async def get_open_interest_change_pct(self, symbol: str, hours: int = 4) -> float | None:
        """Variation de l'OI sur une fenêtre de `hours` heures — utilisée par la Cat.10
        (GSB/OIFD) pour rester cohérente avec la fenêtre de comparaison du prix (4h),
        contrairement à get_open_interest_change_24h_pct (24h) qui créerait un décalage
        de fenêtre temporelle dans le calcul de disparité OI/prix."""
        return await self._open_interest_change_pct(symbol, points=hours + 1)

    async def _open_interest_change_pct(self, symbol: str, points: int) -> float | None:
        try:
            data = await self._get(
                "/futures/data/openInterestHist",
                params={"symbol": symbol, "period": "1h", "limit": points},
            )
            if not data or len(data) < 2:
                return None
            first = float(data[0]["sumOpenInterest"])
            last = float(data[-1]["sumOpenInterest"])
            if first == 0:
                return None
            return round((last / first - 1) * 100, 2)
        except Exception:
            return None

    async def get_ticker_price(self, symbol: str) -> float | None:
        """Prix courant léger (poids API minimal), utilisé pour le monitoring et la watchlist."""
        data = await self._get("/fapi/v1/ticker/price", params={"symbol": symbol})
        try:
            return float(data["price"])
        except (KeyError, TypeError, ValueError):
            return None

    async def get_orderbook_imbalance(self, symbol: str, pct_range: float = 2.0) -> float | None:
        """Ratio de déséquilibre du carnet d'ordres à ±pct_range% du mid-price.
        > 0.5 = pression acheteuse dominante, < 0.5 = pression vendeuse dominante."""
        try:
            data = await self._get("/fapi/v1/depth", params={"symbol": symbol, "limit": 100})
            bids, asks = data.get("bids", []), data.get("asks", [])
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

    async def get_orderbook_walls(self, symbol: str, pct_range: float = 2.0) -> dict | None:
        """Détecte le plus gros mur d'ordres (Buy Wall / Sell Wall) à ±pct_range% du
        mid-price — utilisé par la Stratégie 1 (Cat.6) pour la protection d'entrée /
        le placement du Stop Loss. Retourne le niveau le plus gros côté bid et côté
        ask, avec leur taille en USD (prix × quantité)."""
        try:
            data = await self._get("/fapi/v1/depth", params={"symbol": symbol, "limit": 100})
            bids, asks = data.get("bids", []), data.get("asks", [])
            if not bids or not asks:
                return None
            mid = (float(bids[0][0]) + float(asks[0][0])) / 2
            lo, hi = mid * (1 - pct_range / 100), mid * (1 + pct_range / 100)

            bid_levels = [(float(p), float(p) * float(q)) for p, q in bids if float(p) >= lo]
            ask_levels = [(float(p), float(p) * float(q)) for p, q in asks if float(p) <= hi]
            bid_wall = max(bid_levels, key=lambda x: x[1]) if bid_levels else None
            ask_wall = max(ask_levels, key=lambda x: x[1]) if ask_levels else None
            return {
                "bid_wall_price": bid_wall[0] if bid_wall else None,
                "bid_wall_usd": bid_wall[1] if bid_wall else None,
                "ask_wall_price": ask_wall[0] if ask_wall else None,
                "ask_wall_usd": ask_wall[1] if ask_wall else None,
            }
        except Exception:
            return None

    async def get_spread_pct(self, symbol: str) -> float | None:
        try:
            data = await self._get("/fapi/v1/depth", params={"symbol": symbol, "limit": 5})
            bids, asks = data.get("bids", []), data.get("asks", [])
            if not bids or not asks:
                return None
            best_bid, best_ask = float(bids[0][0]), float(asks[0][0])
            mid = (best_bid + best_ask) / 2
            return round((best_ask - best_bid) / mid * 100, 4) if mid else None
        except Exception:
            return None

    async def get_usdt_perpetual_symbols(self) -> list[str]:
        info = await self.get_exchange_info()
        symbols = [
            s["symbol"]
            for s in info["symbols"]
            if s.get("quoteAsset") == settings.QUOTE_ASSET
            and s.get("contractType") == "PERPETUAL"
            and s.get("status") == "TRADING"
        ]
        return symbols

    async def get_top_symbols_by_volume(self, n: int, min_quote_volume: float | None = None) -> list[str]:
        """Sélectionne les N paires USDT perpétuelles les plus liquides, en excluant celles
        sous le seuil de volume 24h minimum (`min_quote_volume` si fourni, sinon
        settings.MIN_QUOTE_VOLUME_USDT par défaut).
        ⚠️ Avant correction, cette méthode n'acceptait qu'un seul argument (`n`) alors que
        category10_scanner.py l'appelait avec 2 arguments positionnels -> TypeError silencieusement
        avalé par le try/except -> 0 symbole Binance listé -> 0 signal Cat.10 côté Binance, à
        chaque scan, depuis le début. C'est très probablement LA cause principale du problème
        "Cat.10 ne trouve aucune crypto"."""
        threshold = min_quote_volume if min_quote_volume is not None else settings.MIN_QUOTE_VOLUME_USDT
        perpetuals = set(await self.get_usdt_perpetual_symbols())
        tickers = await self.get_24h_tickers()
        filtered = [
            t for t in tickers
            if t["symbol"] in perpetuals and float(t.get("quoteVolume", 0)) >= threshold
        ]
        filtered.sort(key=lambda t: float(t.get("quoteVolume", 0)), reverse=True)
        return [t["symbol"] for t in filtered[:n]]
