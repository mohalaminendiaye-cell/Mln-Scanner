"""Envoi des résultats de scan par Telegram, Discord et/ou Email."""
import logging
import smtplib
from email.mime.text import MIMEText

import httpx

from .config import settings
from .models import ScanResult, AssetSignal, SocialSpikeSignal, DerivativesAltcoin

logger = logging.getLogger("notifications")

# Limite officielle Telegram par message ; on garde une marge de sécurité
TELEGRAM_MAX_CHARS = 3800


def _format_asset(a: AssetSignal, index: int) -> str:
    return (
        f"{index}. *{a.symbol}* — {a.direction} (score {a.score}/100)\n"
        f"   Déclencheur : {a.trigger_type} — {a.trigger_reason}\n"
        f"   Entrée: {a.entry} | SL: {a.stop_loss} | TP: {a.take_profit} | R:R = 1:{a.risk_reward}\n"
        f"   RSI(H1): {a.rsi_h1} | CHOP(H4): {a.chop_h4} | ATR%: {a.atr_pct} | Vol x{a.volume_ratio}\n"
    )


def _format_social_spike(s: SocialSpikeSignal, index: int) -> str:
    vol = f" | Volume 24h: {s.volume_change_24h_pct:+.1f}%" if s.volume_change_24h_pct is not None else ""
    return f"{index}. *{s.symbol}* — {s.behavior}{vol}\n   Cause : {s.cause}\n   {s.summary}"


def _format_derivative(d: DerivativesAltcoin, index: int) -> str:
    oi = f"{d.oi_change_24h_pct:+.1f}%" if d.oi_change_24h_pct is not None else "n/d"
    funding = f"{d.funding_rate*100:.3f}%" if d.funding_rate is not None else "n/d"
    return (
        f"{index}. *{d.symbol}* — {d.zone_side} (zone: {d.nearest_liquidation_zone}, "
        f"{d.zone_distance_pct}% du prix)\n   OI 24h: {oi} | Funding: {funding}"
    )


def _format_multi_exchange(s, index: int, show_fib: bool = False) -> str:
    oi = f", OI {s.oi_change_24h_pct:+.1f}%" if s.oi_change_24h_pct is not None else ""
    extra = ""
    if show_fib:
        extra = f" | {s.fib_level_label}"
    return (
        f"{index}. *[{s.exchange}] {s.symbol}* — {s.direction} (score {s.score}/100){extra}\n"
        f"   Entrée: {s.entry} | SL: {s.stop_loss} | TP: {s.take_profit} | R:R 1:{s.risk_reward}\n"
        f"   Vol: {s.volume_trend_pct:+.0f}%{oi}"
    )


def _format_gsb(s, index: int) -> str:
    oi = f", OI {s.oi_change_pct:+.1f}%" if s.oi_change_pct is not None else ""
    tag = " ⚠️ REPLI (<60)" if s.is_fallback else ""
    return (
        f"{index}. *[{s.exchange}] {s.symbol}* — {s.direction} (GSB {s.gsb_score}/100){tag}\n"
        f"   VSI:{s.vsi_score} RVOL:{s.rvol_score} OIFD:{s.oifd_score} MSD:{s.msd_score} CORR:{s.corr_score}\n"
        f"   Entrée: {s.entry} | SL: {s.stop_loss} | TP: {s.take_profit} | R:R 1:{s.risk_reward}{oi}"
    )


def format_message_sections(result: ScanResult) -> list[str]:
    """Construit le message complet SOUS FORME DE SECTIONS séparées (une par
    catégorie), pour permettre un envoi en plusieurs messages Telegram sans
    dépasser la limite de 4096 caractères par message, tout en gardant
    TOUTES les catégories (1 à 6 + Bonus Trading), pas seulement les 3
    premières."""
    ts = result.timestamp.strftime("%Y-%m-%d %H:%M UTC")
    sections = []

    header = f"📊 *MLN Scan — {ts}*\n_{result.symbols_analyzed} paires analysées_"
    sections.append(header)

    cat1 = ["🎯 *Catégorie 1 — Probabilité de mouvement (±5%/24h)*"]
    cat1 += [_format_asset(a, i) for i, a in enumerate(result.category1, 1)] or ["Aucun setup qualifié."]
    sections.append("\n".join(cat1))

    cat2 = ["🌀 *Catégorie 2 — Choppiness Index élevé (H4 > 60)*"]
    cat2 += [_format_asset(a, i) for i, a in enumerate(result.category2, 1)] or ["Aucun setup qualifié."]
    sections.append("\n".join(cat2))

    cat4 = ["🔗 *Catégorie 4 — Divergence de corrélation BTC*"]
    cat4 += [_format_asset(a, i) for i, a in enumerate(result.category4, 1)] or ["Aucune divergence significative."]
    sections.append("\n".join(cat4))

    cat6 = ["🧩 *Catégorie 6 — Stratégies*", "*Stratégie 1 :*"]
    cat6 += [_format_asset(a, i) for i, a in enumerate(result.category6.strategie1, 1)] or ["Aucun setup qualifié."]
    cat6.append("*Stratégie 2 :*")
    cat6 += [_format_asset(a, i) for i, a in enumerate(result.category6.strategie2, 1)] or ["Aucun setup qualifié."]
    sections.append("\n".join(cat6))

    cat11 = ["🤖 *Catégorie 11 — Scalping IA (Grok)*"]
    cat11 += [_format_asset(a, i) for i, a in enumerate(result.category11, 1)] or ["Aucun setup qualifié."]
    sections.append("\n".join(cat11))

    if result.bonus_trading:
        bt = ["🔥 *Bonus Trading*", "*1. Pics d'activité sociale (6h)*"]
        if result.bonus_trading.social_spikes:
            bt.append("⚠️ _Recherche X/web via Grok, non déterministe._")
            bt += [_format_social_spike(s, i) for i, s in enumerate(result.bonus_trading.social_spikes, 1)]
        else:
            bt.append("Aucun pic social recensé (ou GROK_API_KEY non configurée).")
        sections.append("\n".join(bt))

        bt2 = ["*2. Dérivés — Top OI en hausse + funding extrême*"]
        if result.bonus_trading.derivatives_top3:
            bt2 += [_format_derivative(d, i) for i, d in enumerate(result.bonus_trading.derivatives_top3, 1)]
        else:
            bt2.append("Aucun candidat détecté pour ce scan.")
        sections.append("\n".join(bt2))

    # --- Catégorie 7 : multi-exchange (Bybit + OKX) ---
    for exchange, signals in (result.category7 or {}).items():
        block = [f"🚀 *Catégorie 7 [{exchange}] — Mouvements imminents (4h)*"]
        block += [_format_multi_exchange(s, i) for i, s in enumerate(signals, 1)] or ["Aucun signal qualifié."]
        sections.append("\n".join(block))

    # --- Catégorie 10 : Global Breakout Score (Binance + Bybit) — au-dessus de la Cat.9 ---
    cat10_block = ["🎯 *Catégorie 10 — Global Breakout Score (GSB ≥ 60)*"]
    cat10_block += [_format_gsb(s, i) for i, s in enumerate(result.category10, 1)] or ["Aucune paire au-dessus du seuil GSB pour ce scan."]
    sections.append("\n".join(cat10_block))

    for exchange, cat9 in (result.category9 or {}).items():
        block = [f"📐 *Catégorie 9 [{exchange}] — Stratégie Fib*", "*Retracement 0.50 :*"]
        block += [_format_multi_exchange(s, i, show_fib=True) for i, s in enumerate(cat9.retracement_050, 1)] or ["Aucun signal."]
        block.append("*Golden Pocket (0.618-0.786) :*")
        block += [_format_multi_exchange(s, i, show_fib=True) for i, s in enumerate(cat9.golden_pocket, 1)] or ["Aucun signal."]
        sections.append("\n".join(block))

    return sections


def format_message(result: ScanResult) -> str:
    """Version 'un seul bloc' (utilisée par Discord/Email, sans limite stricte
    de longueur pratique pour ces canaux)."""
    return "\n\n".join(format_message_sections(result))


def _pack_sections_into_chunks(sections: list[str], max_chars: int) -> list[str]:
    """Regroupe les sections dans des messages de max_chars caractères max,
    sans jamais couper une section au milieu (une section trop longue à elle
    seule est quand même coupée en dernier recours, pour ne jamais bloquer
    l'envoi)."""
    chunks: list[str] = []
    current = ""
    for section in sections:
        candidate = f"{current}\n\n{section}" if current else section
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(section) <= max_chars:
            current = section
        else:
            # Section individuelle trop longue : découpage brut en dernier recours
            for i in range(0, len(section), max_chars):
                chunks.append(section[i : i + max_chars])
            current = ""
    if current:
        chunks.append(current)
    return chunks


async def send_telegram(message: str):
    """Envoie un message Telegram. Si `message` dépasse la limite Telegram
    (4096 caractères), il est automatiquement découpé en plusieurs messages
    envoyés à la suite (numérotés), sans jamais tronquer le contenu."""
    if not (settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID):
        logger.warning("Telegram non configuré, envoi ignoré.")
        return
    chunks = _pack_sections_into_chunks(message.split("\n\n"), TELEGRAM_MAX_CHARS)
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=15) as client:
        for i, chunk in enumerate(chunks, 1):
            text = chunk if len(chunks) == 1 else f"({i}/{len(chunks)})\n{chunk}"
            try:
                resp = await client.post(
                    url,
                    json={
                        "chat_id": settings.TELEGRAM_CHAT_ID,
                        "text": text,
                        "parse_mode": "Markdown",
                    },
                )
                resp.raise_for_status()
            except Exception as e:
                logger.error(f"Échec envoi Telegram (partie {i}/{len(chunks)}): {e}")


async def send_discord(message: str):
    if not settings.DISCORD_WEBHOOK_URL:
        logger.warning("Discord non configuré, envoi ignoré.")
        return
    # Discord limite les messages à 2000 caractères -> découpage si nécessaire
    chunks = _pack_sections_into_chunks(message.split("\n\n"), 1900)
    async with httpx.AsyncClient(timeout=10) as client:
        for chunk in chunks:
            try:
                resp = await client.post(settings.DISCORD_WEBHOOK_URL, json={"content": chunk})
                resp.raise_for_status()
            except Exception as e:
                logger.error(f"Échec envoi Discord: {e}")


def send_email(message: str, subject: str = "MLN Scan"):
    if not (settings.SMTP_HOST and settings.EMAIL_TO):
        logger.warning("Email non configuré, envoi ignoré.")
        return
    try:
        msg = MIMEText(message, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = settings.EMAIL_FROM or settings.SMTP_USER
        msg["To"] = settings.EMAIL_TO

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            if settings.SMTP_USER:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(msg["From"], [settings.EMAIL_TO], msg.as_string())
    except Exception as e:
        logger.error(f"Échec envoi Email: {e}")


async def send_test_message() -> dict:
    """Envoie un message de test sur tous les canaux configurés (pour vérifier la config)."""
    message = (
        "✅ *Test de connexion — MLN Scan*\n"
        "Si tu reçois ce message, les notifications sont bien configurées."
    )
    sent = {"telegram": False, "discord": False, "email": False}
    if settings.NOTIFY_TELEGRAM:
        await send_telegram(message)
        sent["telegram"] = True
    if settings.NOTIFY_DISCORD:
        await send_discord(message)
        sent["discord"] = True
    if settings.NOTIFY_EMAIL:
        send_email(message, subject="Test — MLN Scan")
        sent["email"] = True
    return sent


async def send_scan_failure_alert(error: Exception):
    """Alerte dédiée en cas d'échec COMPLET d'un scan planifié (le scheduler a levé
    une exception non gérée), à distinguer des erreurs par symbole déjà tolérées et
    listées dans ScanResult.errors[]."""
    if not settings.NOTIFY_ON_SCAN_FAILURE:
        return
    message = (
        "🚨 *MLN Scan — ÉCHEC du scan planifié*\n"
        f"Le scan n'a pas pu s'exécuter : `{type(error).__name__}: {error}`\n"
        "Vérifiez les logs du serveur et la connectivité à l'API Binance."
    )
    if settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID:
        await send_telegram(message)
    if settings.DISCORD_WEBHOOK_URL:
        await send_discord(message)
    if settings.SMTP_HOST and settings.EMAIL_TO:
        send_email(message, subject="⚠️ MLN Scan — Échec du scan planifié")


async def send_all_notifications(result: ScanResult):
    message = format_message(result)
    if settings.NOTIFY_TELEGRAM:
        await send_telegram(message)
    if settings.NOTIFY_DISCORD:
        await send_discord(message)
    if settings.NOTIFY_EMAIL:
        send_email(message)
