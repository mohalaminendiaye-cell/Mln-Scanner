"""
Client HTTP asynchrone pour l'API publique Binance Futures (USDT-M).
Gère :
  - le retry avec backoff exponentiel (tenacity)
  - les erreurs de rate limit (HTTP 429 / 418) via lecture du header Retry-After
  - la reconnexion automatique en cas de timeout réseau
  - un throttle PROACTIF basé sur le header X-MBX-USED-WEIGHT-1M, pour ralentir
    AVANT de se faire bannir plutôt que de réagir après coup
  - un gate de ban PARTAGÉ (module-level) entre toutes les instances du client,
    pour que toutes les requêtes en vol respectent un ban 429/418 dès qu'il est
    détecté par n'importe laquelle d'entre elles, au lieu de se prendre chacune
    leur propre 418 en rafale
"""
import asyncio
import logging
import time
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
 
# Limite de poids Binance Futures (par IP, endpoints publics REST) : 2400/min en
# réalité (voir /fapi/v1/exchangeInfo -> rateLimits), mais on se garde une marge
# de sécurité en visant un plafond "soft" bien en dessous pour ne jamais taper
# le mur. Ajuste WEIGHT_HARD_LIMIT si exchangeInfo indique une valeur différente
# pour ton compte / IP.
WEIGHT_SOFT_LIMIT = 950   # au-delà : on ralentit
WEIGHT_HARD_LIMIT = 1100  # au-delà : on met en pause jusqu'au reset de la minute
 
 
class BinanceRateLimitError(Exception):
    """Levée quand Binance renvoie 429/418 (rate limit dépassé)."""
 
 
class BinanceAPIError(Exception):
    """Erreur générique retournée par l'API Binance."""
 
 
class _RateGate:
    """État de rate-limit PARTAGÉ entre toutes les requêtes/instances de client.
 
    Binance bannit une IP, pas un objet Python : si le client est recréé à
    chaque cycle de scan (`async with BinanceFuturesClient() as client`), un
    état de ban stocké sur `self` repartirait à zéro à chaque cycle. On le
    garde donc au niveau du module.
    """
 
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.used_weight: int = 0
        self.weight_updated_at: float = 0.0
        self.banned_until: float = 0.0  # timestamp monotonic, 0 = pas banni
 
    async def wait_if_needed(self) -> None:
        """À appeler juste avant d'envoyer une requête. Bloque si on est banni,
        ou ralentit proactivement si le poids approche la limite."""
        async with self._lock:
            now = time.monotonic()
 
            # 1. Ban actif (429/418) : on attend qu'il expire, peu importe quelle
            #    requête a déclenché le ban.
            if self.banned_until > now:
                wait_s = self.banned_until - now
                logger.warning(f"Ban Binance actif, pause de {wait_s:.1f}s avant nouvelle requête.")
                await asyncio.sleep(wait_s)
                return
 
            # 2. Poids proche/au-dessus du seuil dur : on met en pause jusqu'à la
            #    prochaine fenêtre (le compteur Binance est glissant sur 1 min,
            #    donc ~60s après la dernière mesure connue est une approximation
            #    sûre pour laisser le poids redescendre).
            weight_age = now - self.weight_updated_at
            if self.used_weight >= WEIGHT_HARD_LIMIT and weight_age < 60:
                wait_s = 60 - weight_age
                logger.warning(
                    f"Poids API Binance au plafond ({self.used_weight}/1200), "
                    f"pause de {wait_s:.1f}s pour laisser la fenêtre se libérer."
                )
                await asyncio.sleep(wait_s)
                return
 
            # 3. Poids en zone d'alerte : on ralentit chaque requête d'un petit
            #    délai plutôt que de foncer, pour lisser le débit.
            if self.used_weight >= WEIGHT_SOFT_LIMIT and weight_age < 60:
                await asyncio.sleep(0.5)
 
    async def report_weight(self, weight: int) -> None:
        async with self._lock:
            self.used_weight = weight
            self.weight_updated_at = time.monotonic()
 
    async def report_ban(self, retry_after_s: float) -> None:
        async with self._lock:
            self.banned_until = max(self.banned_until, time.monotonic() + retry_after_s)
 
 
# Instance unique partagée par tout le process, quelle que soit la durée de vie
# des instances de BinanceFuturesClient.
_rate_gate = _RateGate()
 
 
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
 
    async def _track_used_weight(self, resp: httpx.Response, path: str) -> None:
        """Journalise le poids d'API consommé (header Binance) et met à jour le
        gate partagé afin que les prochaines requêtes puissent se throttler
        proactivement, avant de se faire bannir."""
        weight_header = resp.headers.get("X-MBX-USED-WEIGHT-1M") or resp.headers.get("x-mbx-used-weight-1m")
        if weight_header is None:
            return
        try:
            weight = int(weight_header)
        except ValueError:
            return
 
        await _rate_gate.report_weight(weight)
 
        if weight >= WEIGHT_HARD_LIMIT:
            logger.warning(f"Poids API Binance CRITIQUE: {weight}/1200 (après {path}).")
        elif weight >= WEIGHT_SOFT_LIMIT:
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
            # Throttle proactif AVANT d'envoyer quoi que ce soit : respecte un
            # ban en cours et ralentit si le poids approche la limite.
            await _rate_gate.wait_if_needed()
 
            try:
                resp = await self._client.get(path, params=params)
            except httpx.TransportError as e:
                logger.warning(f"Erreur réseau sur {path}: {e}. Nouvelle tentative...")
                raise
 
            await self._track_used_weight(resp, path)
 
            if resp.status_code in (429, 418):
                retry_after = int(resp.headers.get("Retry-After", "5"))
                # On publie le ban immédiatement dans le gate PARTAGÉ : toute
                # autre requête concurrente (même sur un autre symbole) qui
                # passera par wait_if_needed() attendra elle aussi, au lieu de
                # se prendre son propre 418 quelques ms plus tard.
                await _rate_gate.report_ban(retry_after)
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
 
    async def get_top_symbols_by_volume(
        self,
        n: int,
        min_quote_volume: float | None = None,
        perpetuals: set[str] | None = None,
        tickers: list[dict] | None = None,
    ) -> list[str]:
        """Sélectionne les N paires USDT perpétuelles les plus liquides, en excluant celles
        sous le seuil de volume 24h minimum (`min_quote_volume` si fourni, sinon
        settings.MIN_QUOTE_VOLUME_USDT par défaut).

        `perpetuals` et `tickers` sont des paramètres OPTIONNELS de cache : si le scanner
        appelant (scanner.py) les a déjà récupérés une fois pour le cycle en cours, on les
        réutilise ici plutôt que de refaire /fapi/v1/exchangeInfo (poids 1) et surtout
        /fapi/v1/ticker/24hr (poids 40) à chaque catégorie (Cat.1/2, Cat.10, Cat.11
        appellent chacune cette méthode -> sans cache, poids 41 x 3 = 123 gaspillés par
        cycle rien que pour lister les symboles).

        ⚠️ Avant correction, cette méthode n'acceptait qu'un seul argument (`n`) alors que
        category10_scanner.py l'appelait avec 2 arguments positionnels -> TypeError silencieusement
        avalé par le try/except -> 0 symbole Binance listé -> 0 signal Cat.10 côté Binance, à
        chaque scan, depuis le début. C'est très probablement LA cause principale du problème
        "Cat.10 ne trouve aucune crypto"."""
        threshold = min_quote_volume if min_quote_volume is not None else settings.MIN_QUOTE_VOLUME_USDT
        if perpetuals is None:
            perpetuals = set(await self.get_usdt_perpetual_symbols())
        if tickers is None:
            tickers = await self.get_24h_tickers()
        filtered = [
            t for t in tickers
            if t["symbol"] in perpetuals and float(t.get("quoteVolume", 0)) >= threshold
        ]
        filtered.sort(key=lambda t: float(t.get("quoteVolume", 0)), reverse=True)
        return [t["symbol"] for t in filtered[:n]]

    async def get_market_snapshot(self) -> tuple[set[str], list[dict]]:
        """Récupère UNE FOIS `perpetuals` (exchangeInfo) et `tickers` (ticker/24hr), à
        réutiliser pour tous les appels get_top_symbols_by_volume du cycle de scan en
        cours (Cat.1/2, Cat.10, Cat.11), au lieu de les refetcher à chaque catégorie."""
        perpetuals = set(await self.get_usdt_perpetual_symbols())
        tickers = await self.get_24h_tickers()
        return perpetuals, tickers
