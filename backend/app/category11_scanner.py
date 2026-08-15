"""
Catégorie 11 — Scalping IA (Grok)
-----------------------------------------------------------------------------
Stratégie de scalping sur Binance Futures (M1/M15), architecture alignée sur
les Stratégies 1/2 (Cat.6) et la Stratégie Fib (Cat.9) : UN SEUL filtre
bloquant (la tendance EMA200, identité de la stratégie), le reste est un
SCORING PONDÉRÉ sur 100 pts avec seuil minimum — au lieu d'une chaîne
tout-ou-rien où les 5 conditions devaient toutes se produire sur l'unique
bougie M1 la plus récente (beaucoup trop restrictif en pratique : 0 résultat
sur 800 scénarios synthétiques lors des tests, contre 6-9 pour les stratégies
à scoring équivalentes).

ÉLIGIBILITÉ STRICTE (bloquant) :
  - EMA200(M15) calculable et prix suffisamment éloigné pour donner un biais
    net (LE FILTRE de tendance — identité de la stratégie).

SCORING (100 pts), le déclencheur est cherché sur une FENÊTRE glissante des
CATEGORY11_TRIGGER_WINDOW dernières bougies M1 (5 par défaut) plutôt que sur
la seule bougie actuelle -> un setup qui s'est formé "il y a 2-3 minutes" et
dont on profite encore compte, comme pour les Sweeps ICT des autres stratégies :
  - [15 pts] Force de la tendance EMA200(M15) : distance raisonnable (ni
    collée à l'EMA -> signal faible, ni surétendue -> risque de retournement).
  - [25 pts] Point d'ancrage (M1) : proximité au VWAP (bandes ±1 écart-type)
    OU zone d'Order Block -> crédit dégressif selon la distance, pas un
    cutoff strict.
  - [20 pts] Volume d'expansion : meilleur RVOL sur la fenêtre récente.
  - [20 pts] Bougie de retournement : présente n'importe où dans la fenêtre
    récente (plus proche de maintenant = meilleur crédit).
  - [20 pts] Stochastique RSI : meilleur niveau de survente/surachat atteint
    sur la fenêtre récente.

Seules les paires avec un score >= CATEGORY11_MIN_SCORE (65/100) sont
éligibles au top 5. Si aucune ne l'atteint, repli sur le top 5 des scores
entre CATEGORY11_FALLBACK_MIN_SCORE et CATEGORY11_MIN_SCORE (40-65), marqué
is_fallback=True (même mécanisme que la Catégorie 10 et la Stratégie Fib).

⚠️ Toute cette détection reste MÉCANIQUE (déterministe, sur les vraies
données OHLCV) — un LLM n'est pas fiable pour calculer un EMA ou un
Stochastique RSI. Grok n'intervient QU'APRÈS, sur les setups déjà qualifiés
par le scoring mécanique ci-dessus : il reçoit leurs métriques exactes et
peut ajuster la note finale + donner une explication (voir grok_client.py).
Si GROK_API_KEY est absente ou l'appel échoue, le score mécanique est utilisé
tel quel (pas de score "local" séparé à recalculer : le scoring pondéré EST
déjà le score mécanique de référence).
"""
import asyncio
import logging

import pandas as pd

from .binance_client import BinanceFuturesClient
from .config import settings
from .grok_client import score_candidates_with_grok
from .indicators import (
    enrich_dataframe, ema, stochastic_rsi, reversal_candle, order_block, vwap_bands,
)
from .models import AssetSignal

logger = logging.getLogger("category11_scanner")

NO_SETUP_MESSAGE = "Marché actuellement en phase de consolidation/faible volatilité, peu de structures valides."


def _clip01(x: float) -> float:
    return float(max(0.0, min(1.0, x)))


def _klines_to_df(raw: list[list]) -> pd.DataFrame:
    df = pd.DataFrame([row[:6] for row in raw], columns=["ts", "open", "high", "low", "close", "volume"])
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    return df


async def _fetch_bundle(client: BinanceFuturesClient, symbol: str) -> dict | None:
    try:
        k15, k1 = await asyncio.gather(
            client.get_klines(symbol, "15m", limit=max(settings.CATEGORY11_EMA_PERIOD + 20, 220)),
            client.get_klines(symbol, "1m", limit=120),
        )
    except Exception as e:
        logger.warning(f"Cat.11 {symbol}: erreur de récupération ignorée -> {e}")
        return None

    if len(k15) < settings.CATEGORY11_EMA_PERIOD + 5 or len(k1) < 60:
        return None

    df15 = _klines_to_df(k15)
    df1 = enrich_dataframe(_klines_to_df(k1), settings)
    return {"symbol": symbol, "15m": df15, "1m": df1}


def _best_reversal_recency(df1: pd.DataFrame, direction: str, window: int) -> int | None:
    """Cherche une bougie de retournement dans les `window` dernières bougies M1.
    Retourne l'ancienneté (0 = bougie actuelle, 1 = précédente, ...) de la plus
    récente trouvée, ou None si aucune."""
    n = len(df1)
    for offset in range(window):
        idx = n - offset
        if idx < 2:
            break
        if reversal_candle(df1.iloc[:idx], direction):
            return offset
    return None


def _mechanical_score(bundle: dict) -> dict | None:
    """Filtre bloquant (tendance EMA200) + scoring pondéré (100 pts). Retourne un
    dict de candidat si le filtre bloquant passe (le score peut être bas -> géré
    par l'appelant pour le seuil qualifié/repli), sinon None."""
    symbol = bundle["symbol"]
    df15, df1 = bundle["15m"], bundle["1m"]
    price = float(df1["close"].iloc[-1])
    window = settings.CATEGORY11_TRIGGER_WINDOW

    # --- Éligibilité stricte : tendance EMA200(M15) ---
    ema200 = ema(df15["close"], settings.CATEGORY11_EMA_PERIOD)
    ema_now = float(ema200.iloc[-1])
    if pd.isna(ema_now):
        return None
    direction = "Long" if price > ema_now else "Short"
    ema_distance_pct = abs(price - ema_now) / price * 100

    # --- Scoring (100 pts) ---
    # 1. Force de la tendance (15 pts) : plein crédit autour de 0.3%-1.5% d'écart,
    #    dégressif si trop proche (signal faible) ou trop loin (surétendu)
    if ema_distance_pct < 0.1:
        trend_score = 15.0 * (ema_distance_pct / 0.1) * 0.5
    elif ema_distance_pct <= 1.5:
        trend_score = 15.0
    else:
        trend_score = 15.0 * _clip01(1 - (ema_distance_pct - 1.5) / 3)

    # 2. Point d'ancrage (25 pts) : proximité VWAP ou Order Block, crédit dégressif
    vwb = vwap_bands(df1, period=100, band_mult=1.0)
    vwap_distance_pct = abs(price - vwb["vwap"]) / price * 100
    vwap_score = _clip01(1 - vwap_distance_pct / (2 * settings.CATEGORY11_VWAP_PROXIMITY_PCT))

    ob = order_block(df1, direction, lookback=settings.CATEGORY11_OB_LOOKBACK)
    ob_score = 0.0
    if ob is not None:
        if ob["low"] <= price <= ob["high"]:
            ob_score = 1.0
        else:
            ob_edge = ob["low"] if direction == "Long" else ob["high"]
            ob_distance_pct = abs(price - ob_edge) / price * 100
            ob_score = _clip01(1 - ob_distance_pct / 1.0)

    anchor_score = 25.0 * max(vwap_score, ob_score)
    anchor_label = "VWAP" if vwap_score >= ob_score else "Order Block"

    # 3. Volume d'expansion (20 pts) : meilleur RVOL sur la fenêtre récente
    recent_vol_ratio = df1["vol_ratio"].tail(window)
    rvol = float(recent_vol_ratio.max()) if not recent_vol_ratio.isna().all() else 0.0
    rvol_score = 20.0 * _clip01(rvol / (settings.CATEGORY11_MIN_RVOL * 1.5))

    # 4. Bougie de retournement (20 pts) : présente dans la fenêtre récente, plus
    #    proche de maintenant = meilleur crédit
    reversal_recency = _best_reversal_recency(df1, direction, window)
    reversal_score = 20.0 * (1 - reversal_recency / window) if reversal_recency is not None else 0.0

    # 5. Stochastique RSI (20 pts) : meilleur niveau extrême atteint sur la fenêtre récente
    stoch = stochastic_rsi(df1["close"])
    recent_k = stoch["k"].tail(window)
    if direction == "Long":
        k_best = float(recent_k.min()) if not recent_k.isna().all() else 50.0
        stoch_score = 20.0 * _clip01((settings.CATEGORY11_STOCH_OVERSOLD - k_best) / settings.CATEGORY11_STOCH_OVERSOLD + 0.3)
    else:
        k_best = float(recent_k.max()) if not recent_k.isna().all() else 50.0
        stoch_score = 20.0 * _clip01((k_best - settings.CATEGORY11_STOCH_OVERBOUGHT) / (100 - settings.CATEGORY11_STOCH_OVERBOUGHT) + 0.3)

    total_score = round(trend_score + anchor_score + rvol_score + reversal_score + stoch_score, 2)

    # --- Plan de trade (calculé quel que soit le score -> l'appelant décide qualifié/repli/rejeté) ---
    lookback_swing = df1.tail(10)
    if direction == "Long":
        entry = min(vwb["vwap"], price) if anchor_label == "VWAP" else (ob["high"] if ob else price)
        sl = float(lookback_swing["low"].min())
        risk = entry - sl
        tp = entry + settings.CATEGORY11_MIN_RR * risk
    else:
        entry = max(vwb["vwap"], price) if anchor_label == "VWAP" else (ob["low"] if ob else price)
        sl = float(lookback_swing["high"].max())
        risk = sl - entry
        tp = entry - settings.CATEGORY11_MIN_RR * risk

    if risk <= 0:
        return None
    rr = round(abs(tp - entry) / risk, 2)
    if rr < settings.CATEGORY11_MIN_RR:
        return None

    return {
        "symbol": symbol, "direction": direction, "entry": round(entry, 8),
        "stop_loss": round(sl, 8), "take_profit": round(tp, 8), "risk_reward": rr,
        "price": price, "ema_distance_pct": round(ema_distance_pct, 3),
        "anchor_label": anchor_label, "vwap_distance_pct": round(vwap_distance_pct, 3),
        "rvol": round(rvol, 2), "stoch_rsi_k": round(k_best, 1),
        "mechanical_score": total_score,
        "score_breakdown": {
            "trend": round(trend_score, 1), "anchor": round(anchor_score, 1),
            "rvol": round(rvol_score, 1), "reversal": round(reversal_score, 1), "stoch": round(stoch_score, 1),
        },
        "atr_pct": round(float(df1["atr_pct"].iloc[-1]), 3) if not pd.isna(df1["atr_pct"].iloc[-1]) else 0.0,
        "rsi": round(float(df1["rsi"].iloc[-1]), 2) if not pd.isna(df1["rsi"].iloc[-1]) else 50.0,
        "sparkline": [round(v, 8) for v in df1["close"].tail(24).tolist()],
    }


async def build_category11() -> tuple[list[AssetSignal], list[str]]:
    errors: list[str] = []
    async with BinanceFuturesClient() as client:
        try:
            symbols = await client.get_top_symbols_by_volume(
                settings.CATEGORY11_TOP_N_SYMBOLS, settings.CATEGORY11_MIN_QUOTE_VOLUME
            )
        except Exception as e:
            errors.append(f"Cat.11: impossible de lister les symboles -> {e}")
            return [], errors

        sem = asyncio.Semaphore(settings.MAX_CONCURRENT_REQUESTS)

        async def fetch(sym):
            async with sem:
                return await _fetch_bundle(client, sym)

        bundles = [b for b in await asyncio.gather(*(fetch(s) for s in symbols)) if b is not None]

        candidates = []
        for b in bundles:
            try:
                c = _mechanical_score(b)
            except Exception as e:
                errors.append(f"Cat.11 {b['symbol']}: {e}")
                continue
            if c and c["mechanical_score"] >= settings.CATEGORY11_FALLBACK_MIN_SCORE:
                candidates.append(c)

        if not candidates:
            logger.info(f"Cat.11 : {len(bundles)} paires exploitables, aucune même au seuil de repli -> {NO_SETUP_MESSAGE}")
            errors.append(f"Cat.11 : {NO_SETUP_MESSAGE}")
            return [], errors

        qualified = [c for c in candidates if c["mechanical_score"] >= settings.CATEGORY11_MIN_SCORE]
        pool = qualified if qualified else candidates
        pool = sorted(pool, key=lambda c: c["mechanical_score"], reverse=True)[:5]

        # Grok affine la note et explique les setups déjà qualifiés mécaniquement
        # (pas de calcul d'indicateur par Grok, uniquement évaluation/synthèse)
        grok_payload = [
            {
                "symbol": c["symbol"], "direction": c["direction"], "rvol": c["rvol"],
                "stoch_rsi_k": c["stoch_rsi_k"], "zone_ancrage": c["anchor_label"],
                "distance_vwap_pct": c["vwap_distance_pct"], "distance_ema200_pct": c["ema_distance_pct"],
                "risk_reward": c["risk_reward"], "score_mecanique": c["mechanical_score"],
            }
            for c in pool
        ]
        try:
            grok_scores = await score_candidates_with_grok(grok_payload)
        except Exception as e:
            errors.append(f"Cat.11 (Grok): {e}")
            grok_scores = None

        signals = []
        for c in pool:
            is_fallback = c["mechanical_score"] < settings.CATEGORY11_MIN_SCORE
            grok_result = (grok_scores or {}).get(c["symbol"])
            if grok_result and "score" in grok_result and not is_fallback:
                score = round(float(grok_result["score"]), 2)
                reason_ai = grok_result.get("reason", "")
                source_tag = "Grok"
            else:
                score = c["mechanical_score"]
                reason_ai = "Score mécanique (Grok indisponible)." if not grok_scores else ""
                source_tag = "mécanique" if not grok_scores else "mécanique+repli"

            bd = c["score_breakdown"]
            reason = (
                f"[{source_tag}] EMA200(M15) {c['direction']} ({c['ema_distance_pct']}% de distance) + "
                f"ancrage {c['anchor_label']} + RVOL x{c['rvol']} + StochRSI %K={c['stoch_rsi_k']} | "
                f"Score: {c['mechanical_score']}/100 (Tendance:{bd['trend']} Ancrage:{bd['anchor']} "
                f"RVOL:{bd['rvol']} Retournement:{bd['reversal']} StochRSI:{bd['stoch']}) | {reason_ai}"
            )
            signals.append(AssetSignal(
                symbol=c["symbol"], category="scalping_grok", score=score,
                trigger_type="technique+fondamental" if source_tag == "Grok" else "technique",
                trigger_reason=reason, direction=c["direction"], entry=c["entry"],
                stop_loss=c["stop_loss"], take_profit=c["take_profit"], risk_reward=c["risk_reward"],
                price=c["price"], atr_pct=c["atr_pct"], rsi_h1=c["rsi"], chop_h4=0.0,
                volume_ratio=c["rvol"], funding_rate=None, sparkline=c["sparkline"],
                is_fallback=is_fallback,
            ))

        top5 = sorted(signals, key=lambda s: s.score, reverse=True)[:5]
        if not qualified:
            logger.warning(
                f"Cat.11 : aucun candidat n'a atteint {settings.CATEGORY11_MIN_SCORE} -> repli sur le top "
                f"{len(top5)} des scores {settings.CATEGORY11_FALLBACK_MIN_SCORE}-{settings.CATEGORY11_MIN_SCORE} : "
                f"{', '.join(f'{s.symbol}({s.score})' for s in top5)}"
            )
        logger.info(
            f"Cat.11 : {len(symbols)} symboles ciblés, {len(bundles)} exploitables, "
            f"{len(candidates)} au-dessus du seuil de repli, {len(qualified)} qualifiés "
            f"(>= {settings.CATEGORY11_MIN_SCORE}), {len(top5)} retenus."
        )
        return top5, errors
