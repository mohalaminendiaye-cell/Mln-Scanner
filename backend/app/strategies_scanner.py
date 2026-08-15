"""
Catégorie 6 — Stratégies personnalisées
-----------------------------------------------------------------
Remplace l'ancienne Catégorie 6 (Unlocks, qui nécessitait ANTHROPIC_API_KEY et une
recherche web non déterministe). Cette catégorie contient deux modules indépendants :

  - Stratégie 1 (strategie1_scanner.py) : confluence Ichimoku + Volume Profile +
    Order Book sur 5m/15m/1H, implémentée.
  - Stratégie 2 : en attente des règles.

Chaque module retourne une liste d'AssetSignal (même format que les autres
catégories), pour réutiliser tel quel l'affichage frontend (AssetCard) et les
notifications (Telegram/Discord/Email) déjà en place.
"""
import asyncio
import logging

from .models import AssetSignal, Category6Strategies
from .strategie1_scanner import build_strategie1
from .strategie2_scanner import build_strategie2

logger = logging.getLogger("strategies_scanner")


async def _run_strategie_1(errors: list[str]) -> list[AssetSignal]:
    """Confluence Ichimoku + Volume Profile + Order Book (voir strategie1_scanner.py
    pour le détail complet des règles)."""
    try:
        signals, strat_errors = await build_strategie1()
        errors.extend(strat_errors)
        return signals
    except Exception as e:
        logger.exception("Échec complet de la Stratégie 1")
        errors.append(f"Stratégie 1: {e}")
        return []


async def _run_strategie_2(errors: list[str]) -> list[AssetSignal]:
    """Confluence ICT/SMT + VWAP (voir strategie2_scanner.py pour le détail complet
    des règles)."""
    try:
        signals, strat_errors = await build_strategie2()
        errors.extend(strat_errors)
        return signals
    except Exception as e:
        logger.exception("Échec complet de la Stratégie 2")
        errors.append(f"Stratégie 2: {e}")
        return []


async def build_category6_strategies(results: list[dict | None]) -> tuple[Category6Strategies, list[str]]:
    errors1: list[str] = []
    errors2: list[str] = []
    strategie1, strategie2 = await asyncio.gather(
        _run_strategie_1(errors1), _run_strategie_2(errors2)
    )
    return Category6Strategies(strategie1=strategie1, strategie2=strategie2), errors1 + errors2
