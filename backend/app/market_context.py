"""
Contexte marché affiché en tête de l'onglet "Vue d'ensemble" :
  1. Résumé macro/géopolitique (généré par Grok si GROK_API_KEY est
     configurée, sinon résumé automatique basé sur les données brutes)
  2. Dernières news crypto (flux RSS CoinDesk + Cointelegraph)
  3. Calendrier économique US / Europe / Asie (flux public ForexFactory)
  4. Marchés traditionnels : DXY, or, pétrole, S&P 500, Nasdaq 100, EUR/USD,
     avec niveaux techniques et classification squeeze/accumulation/prise de profit
  5. Crypto Fear & Greed Index
  6. Flux nets des ETF Bitcoin/ETH/Solana (⚠️ nécessite GROK_API_KEY, via
     ai_research.fetch_etf_flows)

Ce contexte est rafraîchi automatiquement à chaque scan planifié (08h45/13h15/
00h15, heure de Dakar), et peut aussi être rafraîchi à la demande par le
frontend avec un cache mémoire de secours (15 min) pour éviter de spammer les
flux externes entre deux scans.
"""
import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import httpx

from .config import settings
from . import fear_greed as fear_greed_module
from . import traditional_markets as traditional_markets_module
from . import ai_research
from . import grok_client

logger = logging.getLogger("market_context")

NEWS_FEEDS = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
]
CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# Regroupement des devises par région pour l'affichage US / Europe / Asie
REGION_MAP = {
    "USD": "us",
    "EUR": "europe",
    "GBP": "europe",
    "CHF": "europe",
    "JPY": "asie",
    "CNY": "asie",
    "AUD": "asie",
    "NZD": "asie",
}

_cache: dict = {"data": None, "expires_at": 0}
CACHE_TTL_SECONDS = 900  # 15 minutes


async def _fetch_rss(name: str, url: str, limit: int = 6) -> list[dict]:
    items = []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for item in root.findall(".//item")[:limit]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            if title:
                items.append({"source": name, "title": title, "link": link, "published": pub_date})
    except Exception as e:
        logger.warning(f"Impossible de charger le flux {name}: {e}")
    return items


async def fetch_crypto_news(limit_per_feed: int = 6) -> list[dict]:
    all_items = []
    for name, url in NEWS_FEEDS:
        all_items.extend(await _fetch_rss(name, url, limit_per_feed))
    return all_items[:12]


def _parse_event_datetime(raw_date: str, raw_time: str = "") -> datetime | None:
    """Essaie plusieurs formats de date/heure possibles, car le flux public ne
    garantit pas toujours le même format exact (ISO8601 complet, "MM-DD-YYYY"
    séparé de l'heure, etc.). Retourne None si aucun format ne correspond."""
    if not raw_date:
        return None
    candidates = [raw_date]
    if raw_time:
        candidates.append(f"{raw_date} {raw_time}")

    for candidate in candidates:
        # ISO8601 (ex: "2026-07-28T08:30:00-04:00" ou "2026-07-28T08:30:00Z")
        try:
            iso_candidate = candidate.replace("Z", "+00:00")
            return datetime.fromisoformat(iso_candidate)
        except ValueError:
            pass
        # Formats explicites courants sur les flux calendrier économique publics
        for fmt in (
            "%m-%d-%Y", "%m-%d-%Y %I:%M%p", "%m-%d-%Y %H:%M",
            "%Y-%m-%d", "%Y-%m-%d %H:%M", "%d-%m-%Y",
        ):
            try:
                return datetime.strptime(candidate, fmt)
            except ValueError:
                continue
    return None


async def fetch_economic_calendar() -> dict[str, list[dict]]:
    """Retourne les événements macro à impact Haut/Moyen des 7 prochains jours,
    regroupés par région (us / europe / asie), avec date/heure toujours formatées
    de façon lisible (JJ/MM/AAAA + HH:MMh) quel que soit le format source."""
    result = {"us": [], "europe": [], "asie": []}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(CALENDAR_URL, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
        raw_events = resp.json()
    except Exception as e:
        logger.warning(f"Impossible de charger le calendrier économique: {e}")
        return result

    if raw_events:
        logger.info(f"Calendrier économique: exemple d'événement brut reçu -> {raw_events[0]}")

    now = datetime.now(timezone.utc)
    for ev in raw_events:
        impact = str(ev.get("impact", "")).lower()
        if impact not in ("high", "medium"):
            continue
        country = ev.get("country", "")
        region = REGION_MAP.get(country)
        if not region:
            continue

        raw_date = str(ev.get("date", "") or "")
        raw_time = str(ev.get("time", "") or "")
        parsed = _parse_event_datetime(raw_date, raw_time)

        if parsed is not None:
            event_date_naive = parsed.replace(tzinfo=None)
            now_naive = now.replace(tzinfo=None)
            if event_date_naive < now_naive - timedelta(days=1) or event_date_naive > now_naive + timedelta(days=7):
                continue
            display_date = parsed.strftime("%d/%m/%Y")
            display_time = parsed.strftime("%Hh%M") if (parsed.hour or parsed.minute) else raw_time
        else:
            # On ne perd jamais l'info brute même si le format n'a pas été reconnu :
            # mieux vaut afficher une date "brute" que rien du tout.
            display_date = raw_date or "Date inconnue"
            display_time = raw_time

        result[region].append(
            {
                "title": ev.get("title", ""),
                "country": country,
                "date": display_date,
                "time": display_time,
                "impact": impact,
                "forecast": ev.get("forecast", ""),
                "previous": ev.get("previous", ""),
            }
        )

    for region in result:
        result[region] = result[region][:8]
    return result


def _fallback_macro_summary(news: list[dict], calendar: dict) -> str:
    """Résumé simple sans IA, basé sur le nombre d'événements macro à venir."""
    nb_events = sum(len(v) for v in calendar.values())
    parts = [
        f"{len(news)} actualités crypto récentes recensées.",
        f"{nb_events} événements macroéconomiques à impact élevé/moyen prévus sur 7 jours "
        f"(US: {len(calendar.get('us', []))}, Europe: {len(calendar.get('europe', []))}, "
        f"Asie: {len(calendar.get('asie', []))}).",
        "Configurez GROK_API_KEY dans le .env pour obtenir un résumé "
        "géopolitique et macroéconomique rédigé automatiquement.",
    ]
    return " ".join(parts)


async def _generate_macro_summary_via_grok(news: list[dict], calendar: dict) -> str | None:
    if not settings.GROK_API_KEY:
        return None
    headlines = "\n".join(f"- {n['title']}" for n in news[:10])
    events = "\n".join(
        f"- [{region.upper()}] {e['title']} ({e['date']} {e['time']}, impact {e['impact']})"
        for region, evs in calendar.items()
        for e in evs[:5]
    )
    prompt = (
        "Tu es un analyste macro. À partir des titres d'actualités crypto et des "
        "événements économiques ci-dessous, rédige un résumé de 3 à 4 phrases maximum "
        "en français, présentant la situation géopolitique et macroéconomique mondiale "
        "actuelle et son impact potentiel sur les marchés crypto. Sois factuel et concis, "
        "sans recommandation d'investissement.\n\n"
        f"Actualités crypto récentes:\n{headlines}\n\n"
        f"Événements macro à venir:\n{events}"
    )
    return await grok_client.generate_text_with_grok(prompt, max_tokens=300)


async def get_market_context(force_refresh: bool = False) -> dict:
    """Point d'entrée unique : résumé macro, news, calendrier, marchés traditionnels,
    Fear & Greed Index et flux ETF. Cache mémoire 15 min entre deux scans planifiés."""
    now = time.time()
    if not force_refresh and _cache["data"] and now < _cache["expires_at"]:
        return _cache["data"]

    news = await fetch_crypto_news()
    calendar = await fetch_economic_calendar()
    summary = await _generate_macro_summary_via_grok(news, calendar)
    if not summary:
        summary = _fallback_macro_summary(news, calendar)

    fear_greed = await fear_greed_module.fetch_fear_greed()
    traditional_markets = await traditional_markets_module.fetch_all_traditional_markets()
    etf_flows = await ai_research.fetch_etf_flows()

    data = {
        # Le résumé macro est placé en premier : c'est le point d'entrée du contexte marché.
        "macro_summary": summary,
        "news": news,
        "calendar": calendar,
        "traditional_markets": [m.model_dump() for m in traditional_markets],
        "fear_greed": fear_greed.model_dump() if fear_greed else None,
        "etf_flows": etf_flows.model_dump(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _cache["data"] = data
    _cache["expires_at"] = now + CACHE_TTL_SECONDS
    return data
