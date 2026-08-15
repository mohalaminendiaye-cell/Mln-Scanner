"""
Planification des scans automatiques.
Horaires par défaut (heure de Dakar = GMT, fixe toute l'année, pas de DST) :
08h45, 13h15, 00h15
"""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import settings
from .scanner import run_scan
from .database import save_scan, record_signal_outcomes, purge_old_scans
from .notifications import send_all_notifications, send_scan_failure_alert
from .monitoring import run_monitoring_cycle, reset_breakout_alerts, scan_lock
from .market_context import get_market_context

logger = logging.getLogger("scheduler")

scheduler = AsyncIOScheduler(timezone=settings.SCAN_TIMEZONE)


async def scheduled_scan_job():
    if scan_lock.locked():
        logger.warning(
            "Scan planifié différé : un cycle de monitoring est en cours. "
            "APScheduler retentera au prochain déclenchement."
        )
        return

    async with scan_lock:
        logger.info("Démarrage du scan planifié...")
        try:
            result = await run_scan()
            scan_id = save_scan(
                result.model_dump_json(),
                result.timestamp,
                result.symbols_analyzed,
            )
            # Enregistre les signaux Cat.1, Cat.2 et Cat.10 pour le suivi de performance (backtest)
            record_signal_outcomes(scan_id, result.category1, "probabilite_mouvement")
            record_signal_outcomes(scan_id, result.category2, "chop_eleve")
            # Les signaux de repli (score 40-60, is_fallback=True) ne sont PAS des setups
            # qualifiés -> exclus du backtest pour ne pas fausser le taux de réussite affiché
            cat10_qualified = [s for s in result.category10 if not s.is_fallback]
            record_signal_outcomes(scan_id, cat10_qualified, "gsb_breakout")
            reset_breakout_alerts()  # réarme les alertes de breakout pour les nouveaux signaux Cat.2

            # Purge les scans trop anciens (DB_RETENTION_DAYS) pour éviter une
            # croissance illimitée de la table des scans.
            try:
                purge_old_scans()
            except Exception as e:
                logger.warning(f"Purge DB échouée: {e}")

            # Rafraîchit le contexte marché (macro, news, calendrier, marchés traditionnels,
            # Fear&Greed, flux ETF) exactement aux mêmes 5 horaires que le scan crypto.
            # Les sous-fonctions IA (pics sociaux/flux ETF) ont leur propre cache
            # 20h et ne rappellent PAS réellement Claude à chaque scan.
            try:
                await get_market_context(force_refresh=True)
            except Exception as e:
                logger.warning(f"Rafraîchissement du contexte marché échoué: {e}")

            await send_all_notifications(result)
            logger.info(
                f"Scan terminé: {len(result.category1)} signaux Cat.1, "
                f"{len(result.category2)} signaux Cat.2, {len(result.category4)} Cat.4, "
                f"{len(result.category6.strategie1) + len(result.category6.strategie2)} Cat.6 (stratégies), "
                f"{len(result.category11)} Cat.11 (scalping IA), "
                f"{len(result.category10)} Cat.10 (GSB, suivis en backtest)."
            )
        except Exception as e:
            logger.exception(f"Le scan planifié a échoué: {e}")
            try:
                await send_scan_failure_alert(e)
            except Exception as notify_error:
                logger.error(f"Échec de l'alerte de scan raté elle-même: {notify_error}")


def start_scheduler():
    for hour, minute in settings.SCAN_TIMES:
        scheduler.add_job(
            scheduled_scan_job,
            CronTrigger(hour=hour, minute=minute, timezone=settings.SCAN_TIMEZONE),
            id=f"scan_{hour:02d}h{minute:02d}",
            replace_existing=True,
            misfire_grace_time=300,
        )
    # Monitoring continu : backtesting + alertes de breakout, indépendant des scans complets
    scheduler.add_job(
        run_monitoring_cycle,
        "interval",
        minutes=settings.MONITORING_INTERVAL_MINUTES,
        id="monitoring_cycle",
        replace_existing=True,
    )
    scheduler.start()
    times_str = ", ".join(f"{h:02d}h{m:02d}" for h, m in settings.SCAN_TIMES)
    logger.info(
        f"Scheduler démarré pour les horaires: {times_str} ({settings.SCAN_TIMEZONE}), "
        f"monitoring toutes les {settings.MONITORING_INTERVAL_MINUTES} min."
    )
