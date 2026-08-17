"""
Point d'entrée FastAPI.

Endpoints :
  GET  /api/health            -> vérification de service
  GET  /api/scan/latest        -> dernier scan effectué
  GET  /api/scan/history       -> historique des scans (résumé)
  GET  /api/scan/{scan_id}     -> détail d'un scan précis
  POST /api/scan/run           -> déclenche un scan manuel (protégé par API_KEY si définie)

⚠️ SÉCURITÉ : les endpoints qui coûtent de l'argent ou modifient des données
(POST /api/scan/run, POST /api/notifications/test, POST/DELETE /api/watchlist/*)
sont protégés par la dépendance `require_api_key` si API_KEY est définie dans le
.env. Sans clé configurée, ces endpoints restent ouverts (comportement historique,
pour ne pas casser un déploiement local existant) mais un avertissement est loggé
au démarrage — à corriger avant toute exposition publique de l'API.
"""
import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import (
    init_db, save_scan, get_history, get_scan_by_id, get_latest_scan,
    add_to_watchlist, remove_from_watchlist, get_watchlist,
    get_backtest_stats, get_recent_outcomes, get_backtest_categories,
    record_signal_outcomes, purge_old_scans,
)
from .binance_client import BinanceFuturesClient
from .scanner import run_scan
from .notifications import send_all_notifications, send_test_message
from .market_context import get_market_context
from .scheduler import start_scheduler
from .monitoring import scan_lock
from . import ai_research

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("main")

_last_manual_scan_at: float = 0.0


async def require_api_key(x_api_key: str | None = Header(default=None)):
    """Dépendance FastAPI : si API_KEY est configurée, exige un header
    `X-API-Key` correspondant. Si API_KEY n'est PAS configurée, laisse passer
    (comportement historique) — voir l'avertissement loggé au démarrage."""
    if not settings.API_KEY:
        return
    if x_api_key != settings.API_KEY:
        raise HTTPException(status_code=401, detail="Clé API invalide ou manquante (header X-API-Key).")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    if settings.ALLOWED_ORIGINS == ["*"]:
        logger.warning(
            "SÉCURITÉ: ALLOWED_ORIGINS='*' autorise n'importe quel site web à appeler "
            "cette API. Restreignez-le au(x) domaine(s) réel(s) de votre frontend en "
            "production (variable d'env ALLOWED_ORIGINS dans backend/.env)."
        )
    if not settings.API_KEY:
        logger.warning(
            "SÉCURITÉ: API_KEY n'est pas configurée. Les endpoints de déclenchement de "
            "scan, de test de notifications et de gestion de la watchlist sont "
            "accessibles SANS AUTHENTIFICATION. Définissez API_KEY dans backend/.env "
            "avant toute exposition publique de cette API (ex: déploiement Railway/Render)."
        )
    logger.info("Application démarrée.")
    yield
    logger.info("Arrêt de l'application.")


app = FastAPI(title="MLN Scan API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    scan_times = [f"{h:02d}:{m:02d}" for h, m in settings.SCAN_TIMES]
    return {"status": "ok", "scan_times": scan_times, "timezone": settings.SCAN_TIMEZONE}


@app.get("/api/config")
async def get_config():
    """Expose au frontend les capacités actives, pour afficher les bannières
    d'information adéquates (ex: fonctionnalités IA désactivées sans clé)."""
    return {
        "ai_features_enabled": ai_research.ai_features_enabled(),
        "ai_daily_calls": ai_research.get_daily_call_stats(),
        "scan_times": [f"{h:02d}:{m:02d}" for h, m in settings.SCAN_TIMES],
        "timezone": settings.SCAN_TIMEZONE,
        "min_quote_volume_usdt": settings.MIN_QUOTE_VOLUME_USDT,
        "top_n_symbols": settings.TOP_N_SYMBOLS,
        "api_key_required": bool(settings.API_KEY),
    }


@app.get("/api/scan/latest")
async def latest_scan():
    record = get_latest_scan()
    if not record:
        raise HTTPException(status_code=404, detail="Aucun scan disponible pour le moment.")
    return {
        "id": record.id,
        "timestamp": record.timestamp,
        "symbols_analyzed": record.symbols_analyzed,
        **json.loads(record.payload_json),
    }


@app.get("/api/scan/history")
async def history(limit: int = 30):
    limit = max(1, min(limit, 200))  # borne défensive : évite une requête DB arbitrairement large
    records = get_history(limit)
    return [
        {
            "id": r.id,
            "timestamp": r.timestamp,
            "symbols_analyzed": r.symbols_analyzed,
        }
        for r in records
    ]


@app.get("/api/scan/{scan_id}")
async def scan_detail(scan_id: int):
    record = get_scan_by_id(scan_id)
    if not record:
        raise HTTPException(status_code=404, detail="Scan introuvable.")
    return {
        "id": record.id,
        "timestamp": record.timestamp,
        "symbols_analyzed": record.symbols_analyzed,
        **json.loads(record.payload_json),
    }


@app.get("/api/context")
async def market_context(refresh: bool = False):
    """News crypto + calendrier économique (US/Europe/Asie) + résumé macro, cache 15 min."""
    return await get_market_context(force_refresh=refresh)


@app.post("/api/scan/run", dependencies=[Depends(require_api_key)])
async def trigger_scan():
    """Déclenche un scan manuel immédiat (hors planning) — pratique pour les tests.
    Protégé par API_KEY (si définie) + cooldown anti-abus (MANUAL_SCAN_COOLDOWN_SECONDS)
    + scan_lock partagé avec le scheduler (évite qu'un scan manuel tourne EN MÊME TEMPS
    qu'un scan planifié, ce qui doublerait la charge sur les APIs externes)."""
    global _last_manual_scan_at
    elapsed = time.monotonic() - _last_manual_scan_at
    if elapsed < settings.MANUAL_SCAN_COOLDOWN_SECONDS:
        retry_after = round(settings.MANUAL_SCAN_COOLDOWN_SECONDS - elapsed)
        raise HTTPException(
            status_code=429,
            detail=f"Scan manuel déjà déclenché récemment. Réessayez dans {retry_after}s.",
        )
    if scan_lock.locked():
        raise HTTPException(
            status_code=409,
            detail="Un autre scan (planifié ou manuel) est déjà en cours. Réessayez dans quelques instants.",
        )
    _last_manual_scan_at = time.monotonic()

    async with scan_lock:
        try:
            result = await run_scan()
        except Exception:
            logger.exception("Échec du scan manuel")
            # Message générique côté client : ne pas exposer le détail de l'exception
            # (pourrait révéler des informations internes) — le détail complet est loggé.
            raise HTTPException(status_code=502, detail="Erreur lors du scan (API externe indisponible). Voir les logs serveur.")

        scan_id = save_scan(result.model_dump_json(), result.timestamp, result.symbols_analyzed)
        record_signal_outcomes(scan_id, result.category1, "probabilite_mouvement")
        record_signal_outcomes(scan_id, result.category2, "chop_eleve")
        cat10_qualified = [s for s in result.category10 if not s.is_fallback]
        record_signal_outcomes(scan_id, cat10_qualified, "gsb_breakout")
        await send_all_notifications(result)
        return {"id": scan_id, **result.model_dump()}


@app.post("/api/notifications/test", dependencies=[Depends(require_api_key)])
async def test_notifications():
    """Envoie un message de test sur Telegram/Discord/Email pour valider la configuration."""
    sent = await send_test_message()
    if not any(sent.values()):
        raise HTTPException(
            status_code=400,
            detail="Aucun canal de notification n'est activé dans le .env (NOTIFY_TELEGRAM/DISCORD/EMAIL=true).",
        )
    return {"sent": sent}


# ---------------------------------------------------------------- Watchlist
@app.get("/api/watchlist")
async def watchlist():
    items = get_watchlist()
    if not items:
        return []
    async with BinanceFuturesClient() as client:
        results = []
        for item in items:
            price = await client.get_ticker_price(item.symbol)
            results.append({"symbol": item.symbol, "added_at": item.added_at, "price": price})
        return results


def _validate_symbol(symbol: str) -> str:
    """Valide et normalise un symbole avant toute utilisation (DB ou appel exchange) :
    évite de stocker/traiter une chaîne arbitraire (longueur non bornée, caractères
    spéciaux) reçue directement depuis un path parameter non validé."""
    symbol = symbol.strip().upper()
    if not (1 < len(symbol) <= 20) or not symbol.replace("-", "").isalnum():
        raise HTTPException(status_code=422, detail="Symbole invalide (attendu: lettres/chiffres, ex: BTCUSDT).")
    return symbol


@app.post("/api/watchlist/{symbol}", dependencies=[Depends(require_api_key)])
async def watchlist_add(symbol: str):
    symbol = _validate_symbol(symbol)
    record = add_to_watchlist(symbol)
    return {"symbol": record.symbol, "added_at": record.added_at}


@app.delete("/api/watchlist/{symbol}", dependencies=[Depends(require_api_key)])
async def watchlist_remove(symbol: str):
    symbol = _validate_symbol(symbol)
    removed = remove_from_watchlist(symbol)
    if not removed:
        raise HTTPException(status_code=404, detail="Symbole absent de la watchlist.")
    return {"removed": symbol}


# ---------------------------------------------------------------- Backtest / Performance
@app.get("/api/backtest/stats")
async def backtest_stats(category: str | None = None, period: str | None = None):
    """period: 'day' | 'week' | 'month' | 'all' (ou omis = toutes périodes)."""
    return get_backtest_stats(category=category, period=period)


@app.get("/api/backtest/recent")
async def backtest_recent(limit: int = 20, category: str | None = None, period: str | None = None):
    limit = max(1, min(limit, 200))  # borne défensive
    return get_recent_outcomes(limit, category=category, period=period)


@app.get("/api/backtest/categories")
async def backtest_categories():
    """Catégories effectivement présentes dans l'historique, pour peupler le filtre frontend."""
    return get_backtest_categories()
