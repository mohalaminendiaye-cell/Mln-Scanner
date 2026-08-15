"""
Client pour l'API Grok (xAI) — Catégorie 11 (Scalping IA).

⚠️ IMPORTANT sur le rôle de Grok ici : un LLM n'est PAS fiable pour calculer des
indicateurs techniques (EMA, Stochastique RSI, etc.) de façon déterministe et
reproductible. Toute la détection technique (filtre de tendance EMA200, pullback
VWAP/Order Block, déclencheur volume + bougie de retournement + StochRSI) est donc
calculée MÉCANIQUEMENT dans category11_scanner.py, comme les autres catégories.

Grok n'intervient qu'APRÈS cette détection mécanique, sur les candidats déjà
qualifiés : on lui fournit leurs métriques exactes (déjà calculées) et on lui
demande une note de confiance (0-100) + une explication synthétique en une phrase —
un rôle d'évaluation/synthèse, pas de calcul. Sans clé configurée, ou en cas
d'erreur, un score de repli est calculé localement (voir category11_scanner.py) et
la note l'indique clairement plutôt que d'inventer une réponse de Grok.

Modèle : configurable via GROK_MODEL (.env). ⚠️ xAI fait évoluer régulièrement ses
noms de modèles — vérifier la valeur actuelle sur https://docs.x.ai si les appels
échouent avec une erreur de type "modèle inconnu".
"""
import json
import logging
import re

import httpx

from .config import settings

logger = logging.getLogger("grok_client")

GROK_API_URL = "https://api.x.ai/v1/chat/completions"
GROK_RESPONSES_URL = "https://api.x.ai/v1/responses"


def extract_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"(\[.*\]|\{.*\})", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
    return None


async def generate_text_with_grok(prompt: str, max_tokens: int = 400, temperature: float = 0.3) -> str | None:
    """Génération de texte simple via Grok (Chat Completions, pas de recherche) —
    pour les cas où le contexte nécessaire est DÉJÀ fourni dans le prompt (ex:
    synthèse de news déjà récupérées), donc pas besoin de x_search/web_search.
    Retourne None si GROK_API_KEY absente ou en cas d'échec (l'appelant doit
    prévoir une dégradation gracieuse, ex: résumé simple sans IA)."""
    if not settings.GROK_API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                GROK_API_URL,
                headers={
                    "Authorization": f"Bearer {settings.GROK_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.GROK_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip() or None
    except Exception as e:
        logger.warning(f"Appel Grok (texte simple) échoué -> {e}")
        return None


async def call_grok_with_search(prompt: str, tools: list[str], max_output_tokens: int = 1500) -> str | None:
    """Appelle Grok via la nouvelle API Responses (https://api.x.ai/v1/responses),
    avec les outils serveur `x_search` (recherche X/Twitter native) et/ou
    `web_search` (recherche web générale). Utilisé pour le Bonus Trading (pics
    sociaux, flux ETF) — remplace l'ancien mécanisme "Live Search"
    (search_parameters), déprécié par xAI le 12 janvier 2026.

    `tools` : liste parmi ["x_search", "web_search"]. Retourne le texte de la
    réponse, ou None si GROK_API_KEY absente ou en cas d'échec (dégradation
    gracieuse gérée par l'appelant)."""
    if not settings.GROK_API_KEY:
        return None

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                GROK_RESPONSES_URL,
                headers={
                    "Authorization": f"Bearer {settings.GROK_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.GROK_MODEL,
                    "input": [{"role": "user", "content": prompt}],
                    "tools": [{"type": t} for t in tools],
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning(f"Appel Grok (Responses API, recherche) échoué -> {e}")
        return None

    # Format Responses API : {"output": [{"type": "message", "content": [{"type": "output_text", "text": "..."}]}]}
    # (structure différente de l'ancienne Chat Completions API — voir docs.x.ai)
    for item in data.get("output", []):
        if item.get("type") != "message":
            continue
        for block in item.get("content", []):
            if block.get("type") == "output_text" and block.get("text"):
                return block["text"].strip()
    logger.warning("Réponse Grok (Responses API) sans texte exploitable trouvé dans 'output'")
    return None


async def score_candidates_with_grok(candidates: list[dict]) -> dict[str, dict] | None:
    """Envoie les candidats déjà détectés mécaniquement à Grok pour notation +
    explication. `candidates` : liste de dicts avec au minimum symbol/direction/
    metrics. Retourne {symbol: {"score": int, "reason": str}} ou None si
    GROK_API_KEY absente ou en cas d'échec (dégradation gracieuse, gérée par
    l'appelant via un score de repli local)."""
    if not settings.GROK_API_KEY or not candidates:
        return None

    prompt = (
        "Tu es un analyste de trading crypto expérimenté. Voici une liste de setups de "
        "scalping DÉJÀ VALIDÉS mécaniquement (filtre de tendance EMA200 M15, pullback sur "
        "VWAP/Order Block, déclencheur volume + bougie de retournement + Stochastique RSI). "
        "Pour CHAQUE setup, donne une note de confiance de 0 à 100 (en tenant compte de la "
        "qualité relative des métriques fournies : plus le volume est élevé, plus le "
        "Stochastique RSI est extrême, plus le repli est proche de la zone institutionnelle, "
        "meilleure est la note) et une explication en UNE phrase courte. "
        "Ne recalcule AUCUNE métrique, base-toi uniquement sur les données fournies.\n\n"
        f"Setups :\n{json.dumps(candidates, ensure_ascii=False, indent=2)}\n\n"
        "Réponds STRICTEMENT en JSON (rien d'autre, pas de texte, pas de markdown), au format :\n"
        '{"SYMBOLE": {"score": 78, "reason": "..."}, ...}'
    )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                GROK_API_URL,
                headers={
                    "Authorization": f"Bearer {settings.GROK_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.GROK_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"},
                },
            )
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.warning(f"Appel Grok échoué (dégradation gracieuse vers score local) -> {e}")
        return None

    parsed = extract_json(text)
    if not isinstance(parsed, dict):
        logger.warning("Réponse Grok non-JSON exploitable, dégradation vers score local")
        return None
    return parsed
