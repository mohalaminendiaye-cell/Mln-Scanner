"""
Fonctionnalités nécessitant une recherche web/X + synthèse (pas de simple appel API) :
  - Bonus Trading (partie 1) : pics d'activité/recherche/mentions X sur 6h
  - Flux nets des ETF Bitcoin/ETH/Solana

⚠️ IMPORTANT : il n'existe pas d'API publique gratuite et fiable pour ces
deux besoins (sentiment/volume de recherche X, flux ETF quotidiens). On
utilise donc l'API Grok (xAI), qui a un accès natif à X (Twitter) via l'outil
serveur `x_search` — plus pertinent que Claude pour du sentiment social — et
`web_search` pour les flux ETF (données financières publiques).

Cela nécessite une clé GROK_API_KEY valide. Sans clé configurée, ces
fonctions retournent une liste/objet vide avec un indicateur explicite —
AUCUNE donnée n'est inventée.

Les résultats de recherche étant par nature non déterministes, traitez ces
sections comme des pistes de recherche à vérifier, pas des faits garantis
(contrairement aux catégories basées sur l'API Binance directe).

MAÎTRISE DES COÛTS (recommandations appliquées) :
  - Chaque fonction est mise en cache AI_RESEARCH_CACHE_HOURS heures (20h par
    défaut) : même si un scan a lieu 5x/jour, l'appel Grok réel n'est fait
    qu'une fois par jour environ, pas à chaque scan.
  - Un plafond AI_RESEARCH_MAX_DAILY_CALLS (15 par défaut) empêche toute
    dérive de coût si le cache est vidé (redémarrage, force_refresh répétés) :
    au-delà, on retourne le dernier résultat en cache (même expiré) plutôt
    que d'appeler l'API à nouveau.
  - Ceci complète, sans le remplacer, le budget de dépense à configurer sur
    console.x.ai.
"""
import logging
import time
from datetime import datetime, timezone

from . import grok_client
from .config import settings
from .models import SocialSpikeSignal, ETFFlows, ETFFlowDay

logger = logging.getLogger("ai_research")

# Cache mémoire par fonction : {"social_spikes": {"data": ..., "expires_at": ts}, ...}
_cache: dict[str, dict] = {}
# Compteur d'appels Grok réels (recherche) par jour calendaire UTC
_daily_calls = {"date": None, "count": 0}


def ai_features_enabled() -> bool:
    return bool(settings.GROK_API_KEY)


def get_daily_call_stats() -> dict:
    _reset_daily_counter_if_needed()
    return {
        "date": _daily_calls["date"],
        "count": _daily_calls["count"],
        "max": settings.AI_RESEARCH_MAX_DAILY_CALLS,
    }


def _reset_daily_counter_if_needed():
    today = datetime.now(timezone.utc).date().isoformat()
    if _daily_calls["date"] != today:
        _daily_calls["date"] = today
        _daily_calls["count"] = 0


def _can_make_call() -> bool:
    _reset_daily_counter_if_needed()
    return _daily_calls["count"] < settings.AI_RESEARCH_MAX_DAILY_CALLS


def _register_call():
    _reset_daily_counter_if_needed()
    _daily_calls["count"] += 1


async def _call_grok_research(prompt: str, tools: list[str]) -> str | None:
    """Wrapper autour de grok_client.call_grok_with_search() qui applique le
    plafond quotidien d'appels (logique partagée entre social_spikes et
    etf_flows, indépendante de la logique de notation de la Catégorie 11)."""
    if not settings.GROK_API_KEY:
        return None
    if not _can_make_call():
        logger.warning(
            f"Plafond quotidien d'appels IA atteint ({settings.AI_RESEARCH_MAX_DAILY_CALLS}/jour). "
            "Appel ignoré, un résultat en cache (même expiré) sera utilisé si disponible."
        )
        return None
    _register_call()
    return await grok_client.call_grok_with_search(prompt, tools=tools)


async def _cached(key: str, force_refresh: bool, fetcher):
    """Wrapper générique de cache : ne rappelle `fetcher()` que si le cache pour
    `key` a expiré (AI_RESEARCH_CACHE_HOURS) ou que force_refresh=True. Si l'appel
    échoue ou est bloqué par le plafond quotidien, retourne le dernier résultat
    connu (même expiré) plutôt qu'un résultat vide, quand c'est possible."""
    now = time.time()
    entry = _cache.get(key)
    if not force_refresh and entry and now < entry["expires_at"]:
        return entry["data"]

    fresh = await fetcher()
    is_empty = (
        fresh is None
        or (hasattr(fresh, "__len__") and len(fresh) == 0)
        or getattr(fresh, "available", True) is False
    )

    if is_empty and entry is not None:
        # Échec/plafond atteint mais on a une valeur précédente : on la garde
        # plutôt que d'écraser par du vide (meilleure expérience utilisateur).
        return entry["data"]

    _cache[key] = {
        "data": fresh,
        "expires_at": now + settings.AI_RESEARCH_CACHE_HOURS * 3600,
    }
    return fresh


async def fetch_social_spikes(force_refresh: bool = False) -> list[SocialSpikeSignal]:
    async def _fetch():
        if not settings.GROK_API_KEY:
            return []
        prompt = (
            "Recherche sur X (Twitter) et sur le web l'actualité crypto des 6 dernières "
            "heures. Identifie 5 cryptomonnaies du Top 200 par capitalisation (hors "
            "stablecoins) qui semblent connaître le plus fort pic d'activité, de volume de "
            "recherche, ou de mentions positives sur X récemment (partenariat, annonce, "
            "rumeur, listing...). "
            "Retourne UNIQUEMENT un tableau JSON (rien d'autre), au format exact :\n"
            '[{"symbol": "XXXUSDT", "cause": "description courte de la cause du pic", '
            '"volume_change_24h_pct": 45.2, "behavior": "Compression|Accumulation|Prise de profit|Indéterminé", '
            '"summary": "résumé en une phrase"}]\n'
            "Le champ behavior doit être ton évaluation de si le mouvement ressemble à une "
            "compression suivie d'un short squeeze (positions à découvert forcées de racheter), une accumulation "
            "(achat discret avant un mouvement) ou une prise de profit (vente après hausse)."
        )
        text = await _call_grok_research(prompt, tools=["x_search", "web_search"])
        if not text:
            return []
        parsed = grok_client.extract_json(text)
        if not isinstance(parsed, list):
            return []
        signals = []
        for item in parsed[:5]:
            try:
                signals.append(SocialSpikeSignal(**item))
            except Exception as e:
                logger.warning(f"Entrée pic social invalide ignorée: {e}")
        return signals

    return await _cached("social_spikes", force_refresh, _fetch)


async def fetch_etf_flows(force_refresh: bool = False) -> ETFFlows:
    async def _fetch():
        if not settings.GROK_API_KEY:
            return ETFFlows(available=False)
        prompt = (
            "Recherche sur le web les flux nets quotidiens (net flows) des 10 derniers jours "
            "de trading pour les ETF spot Bitcoin, Ethereum et Solana aux États-Unis "
            "(sources: SoSoValue, Farside Investors...). Retourne UNIQUEMENT un objet JSON "
            "(rien d'autre), montants en millions de dollars (positif = entrée, négatif = sortie), "
            "au format exact :\n"
            '{"btc": [{"date": "AAAA-MM-JJ", "net_flow_usd_m": 120.5}, ...], '
            '"eth": [...], "sol": [...]}'
        )
        text = await _call_grok_research(prompt, tools=["web_search"])
        if not text:
            return ETFFlows(available=False)
        parsed = grok_client.extract_json(text)
        if not isinstance(parsed, dict):
            return ETFFlows(available=False)
        try:
            return ETFFlows(
                btc=[ETFFlowDay(**d) for d in parsed.get("btc", [])],
                eth=[ETFFlowDay(**d) for d in parsed.get("eth", [])],
                sol=[ETFFlowDay(**d) for d in parsed.get("sol", [])],
                available=True,
            )
        except Exception as e:
            logger.warning(f"Réponse flux ETF invalide: {e}")
            return ETFFlows(available=False)

    result = await _cached("etf_flows", force_refresh, _fetch)
    return result if result is not None else ETFFlows(available=False)
