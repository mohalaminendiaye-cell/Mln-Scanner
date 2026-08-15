"""
Monitoring continu, exécuté toutes les MONITORING_INTERVAL_MINUTES (indépendamment
des 5 scans complets quotidiens) :

1. Backtesting : pour chaque signal "pending" (Cat.1/Cat.2 des scans précédents),
   vérifie si le prix courant a touché le Take Profit, le Stop Loss, ou si le signal
   a expiré (> BACKTEST_LOOKFORWARD_HOURS sans issue) → met à jour son statut.

2. Alertes de breakout : pour les signaux Cat.2 (range/chop) du dernier scan,
   vérifie si le prix a effectivement cassé la borne du range (entry) depuis le scan
   → envoie une notification immédiate si c'est le cas, sans attendre le prochain scan.
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta

from .binance_client import BinanceFuturesClient
from .bybit_client import BybitClient
from .config import settings
from .database import (
    get_pending_outcomes,
    close_outcome,
    get_latest_scan,
)
from .notifications import send_telegram, send_discord

logger = logging.getLogger("monitoring")

# Verrou partagé avec scheduler.scheduled_scan_job : évite qu'un scan complet (qui
# interroge déjà fortement l'API Binance sur ~120 symboles) et un cycle de monitoring
# tournent en même temps et cumulent leur charge sur le rate limit.
scan_lock = asyncio.Lock()

# Symboles déjà alertés pour un breakout sur ce scan (évite le spam de notifications)
_alerted_breakouts: set[str] = set()


async def check_pending_outcomes():
    """Backtesting : ferme les signaux dont le TP ou le SL a été touché, ou qui ont expiré.
    Route la récupération de prix vers le bon exchange (Binance ou Bybit) selon
    `outcome.exchange` — nécessaire depuis que la Cat.10 (multi-exchange) est suivie
    en plus des Cat.1/Cat.2 (Binance uniquement)."""
    pending = get_pending_outcomes()
    if not pending:
        return

    needs_bybit = any(o.exchange == "Bybit" for o in pending)

    async with BinanceFuturesClient() as binance_client:
        bybit_client = BybitClient() if (needs_bybit and settings.BYBIT_ENABLED) else None
        if bybit_client:
            await bybit_client.__aenter__()
        try:
            for outcome in pending:
                client = bybit_client if (outcome.exchange == "Bybit" and bybit_client) else binance_client
                try:
                    price = await client.get_ticker_price(outcome.symbol)
                except Exception as e:
                    logger.warning(f"Impossible de récupérer le prix de {outcome.symbol} ({outcome.exchange}): {e}")
                    continue
                if price is None:
                    continue

                hit_tp = (
                    (outcome.direction == "Long" and price >= outcome.take_profit)
                    or (outcome.direction == "Short" and price <= outcome.take_profit)
                )
                hit_sl = (
                    (outcome.direction == "Long" and price <= outcome.stop_loss)
                    or (outcome.direction == "Short" and price >= outcome.stop_loss)
                )
                lookforward_hours = (
                    settings.CATEGORY10_LOOKFORWARD_HOURS
                    if outcome.category == "gsb_breakout"
                    else settings.BACKTEST_LOOKFORWARD_HOURS
                )
                expired = datetime.utcnow() - outcome.opened_at > timedelta(hours=lookforward_hours)

                if hit_tp:
                    close_outcome(outcome.id, "win", price)
                elif hit_sl:
                    close_outcome(outcome.id, "loss", price)
                elif expired:
                    close_outcome(outcome.id, "expired", price)
        finally:
            if bybit_client:
                await bybit_client.__aexit__(None, None, None)


async def check_breakout_alerts():
    """Alerte immédiate si un actif de la Catégorie 2 (range/chop) casse sa borne d'entrée."""
    latest = get_latest_scan()
    if not latest:
        return
    payload = json.loads(latest.payload_json)
    cat2 = payload.get("category2", [])
    if not cat2:
        return

    async with BinanceFuturesClient() as client:
        for signal in cat2:
            symbol = signal["symbol"]
            if symbol in _alerted_breakouts:
                continue
            try:
                price = await client.get_ticker_price(symbol)
            except Exception:
                continue
            if price is None:
                continue

            direction_guess = "Long" if price >= signal["entry"] and signal["entry"] >= signal["price"] else None
            broke_up = signal["entry"] >= signal["price"] and price >= signal["entry"]
            broke_down = signal["entry"] <= signal["price"] and price <= signal["entry"]

            if broke_up or broke_down:
                sens = "haussière (résistance cassée)" if broke_up else "baissière (support cassé)"
                message = (
                    f"🚨 *Breakout détecté — {symbol}*\n"
                    f"Cassure {sens} de la zone de range identifiée au dernier scan.\n"
                    f"Prix actuel: {price} | Niveau de cassure: {signal['entry']} | "
                    f"TP indicatif: {signal['take_profit']}"
                )
                if settings.NOTIFY_TELEGRAM:
                    await send_telegram(message)
                if settings.NOTIFY_DISCORD:
                    await send_discord(message)
                _alerted_breakouts.add(symbol)
                logger.info(f"Alerte de breakout envoyée pour {symbol}")


def reset_breakout_alerts():
    """À appeler après chaque nouveau scan complet pour réarmer les alertes."""
    _alerted_breakouts.clear()


async def run_monitoring_cycle():
    if scan_lock.locked():
        logger.info("Cycle de monitoring ignoré : un scan complet est déjà en cours.")
        return
    async with scan_lock:
        try:
            await check_pending_outcomes()
            await check_breakout_alerts()
        except Exception as e:
            logger.exception(f"Erreur pendant le cycle de monitoring: {e}")
