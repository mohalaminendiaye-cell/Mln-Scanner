"""
Catégorie 11 — Scalping IA (Grok)
-----------------------------------------------------------------------------
Stratégie de scalping en 4 étapes, sur Binance Futures (M1/M15) :

  1. LE FILTRE (M15) : on ne trade que dans le sens de la tendance globale —
     prix au-dessus de l'EMA200(M15) pour un Long, en dessous pour un Short.
  2. LE POINT D'ANCRAGE (M1) : on attend un repli du prix (pullback) sur une
     zone institutionnelle forte — le VWAP (bandes ±1 écart-type) OU un Order
     Block (dernière bougie opposée avant l'impulsion).
  3. LE DÉCLENCHEUR (M1) : validation du rebond par un sursaut de volume
     (RVOL >= CATEGORY11_MIN_RVOL), une bougie de retournement (engulfing ou
     marteau/étoile filante), ET un Stochastique RSI en zone de
     survente/surachat cohérente avec la direction.
  4. L'EXÉCUTION : entrée par ordre Limite sur la zone d'ancrage, Stop-Loss
     serré sous/sur le dernier creux/sommet, R:R minimum
     CATEGORY11_MIN_RR (1:1.2 par défaut).

⚠️ Toute cette détection est MÉCANIQUE (déterministe, calculée sur les données
OHLCV réelles) — un LLM n'est pas fiable pour calculer un EMA ou un
Stochastique RSI. Grok n'intervient QU'APRÈS, sur les setups déjà validés par
les 4 étapes ci-dessus : il reçoit leurs métriques exactes et donne une note
de confiance (0-100) + une explication courte (voir grok_client.py). Si
GROK_API_KEY est absente ou l'appel échoue, un score de repli 100% local est
calculé à partir des mêmes métriques (RVOL, distance à la zone d'ancrage,
extrémité du Stochastique RSI), et c'est indiqué explicitement dans le
résultat plutôt que d'inventer une réponse de Grok.
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


def _mechanical_setup(bundle: dict) -> dict | None:
    """Applique les 4 étapes mécaniques. Retourne un dict de candidat si TOUTES
    les conditions sont réunies, sinon None."""
    symbol = bundle["symbol"]
    df15, df1 = bundle["15m"], bundle["1m"]
    price = float(df1["close"].iloc[-1])

    # 1. Filtre de tendance M15 : prix vs EMA200
    ema200 = ema(df15["close"], settings.CATEGORY11_EMA_PERIOD)
    ema_now = float(ema200.iloc[-1])
    if pd.isna(ema_now):
        return None
    direction = "Long" if price > ema_now else "Short"
    ema_distance_pct = abs(price - ema_now) / price * 100

    # 2. Point d'ancrage M1 : pullback sur VWAP ou Order Block
    vwb = vwap_bands(df1, period=100, band_mult=1.0)
    vwap_distance_pct = abs(price - vwb["vwap"]) / price * 100
    on_vwap = vwap_distance_pct <= settings.CATEGORY11_VWAP_PROXIMITY_PCT

    ob = order_block(df1, direction, lookback=settings.CATEGORY11_OB_LOOKBACK)
    on_ob = False
    if ob is not None:
        on_ob = ob["low"] <= price <= ob["high"] or abs(price - (ob["low"] if direction == "Long" else ob["high"])) / price * 100 <= 0.3

    if not (on_vwap or on_ob):
        return None
    anchor_label = "VWAP" if on_vwap else "Order Block"

    # 3. Déclencheur M1 : volume + bougie de retournement + StochRSI
    rvol = float(df1["vol_ratio"].iloc[-1]) if not pd.isna(df1["vol_ratio"].iloc[-1]) else 0.0
    if rvol < settings.CATEGORY11_MIN_RVOL:
        return None

    if not reversal_candle(df1, direction):
        return None

    stoch = stochastic_rsi(df1["close"])
    k_now = float(stoch["k"].iloc[-1]) if not pd.isna(stoch["k"].iloc[-1]) else 50.0
    if direction == "Long" and k_now > settings.CATEGORY11_STOCH_OVERSOLD:
        return None
    if direction == "Short" and k_now < settings.CATEGORY11_STOCH_OVERBOUGHT:
        return None

    # 4. Exécution : entrée limite sur la zone, SL sous/sur le dernier creux/sommet
    lookback_swing = df1.tail(10)
    if direction == "Long":
        entry = min(vwb["vwap"], price) if on_vwap else (ob["high"] if ob else price)
        sl = float(lookback_swing["low"].min())
        risk = entry - sl
        tp = entry + settings.CATEGORY11_MIN_RR * risk
    else:
        entry = max(vwb["vwap"], price) if on_vwap else (ob["low"] if ob else price)
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
        "rvol": round(rvol, 2), "stoch_rsi_k": round(k_now, 1),
        "atr_pct": round(float(df1["atr_pct"].iloc[-1]), 3) if not pd.isna(df1["atr_pct"].iloc[-1]) else 0.0,
        "rsi": round(float(df1["rsi"].iloc[-1]), 2) if not pd.isna(df1["rsi"].iloc[-1]) else 50.0,
        "sparkline": [round(v, 8) for v in df1["close"].tail(24).tolist()],
    }


def _local_fallback_score(c: dict) -> tuple[int, str]:
    """Score de repli 100% local (sans Grok), utilisé si GROK_API_KEY absente ou
    l'appel échoue. Combine RVOL, extrémité du StochRSI et proximité de la zone
    d'ancrage -> transparent, indiqué comme tel dans le résultat."""
    rvol_score = _clip01(c["rvol"] / 3)
    if c["direction"] == "Long":
        stoch_score = _clip01((settings.CATEGORY11_STOCH_OVERSOLD - c["stoch_rsi_k"]) / settings.CATEGORY11_STOCH_OVERSOLD)
    else:
        stoch_score = _clip01((c["stoch_rsi_k"] - settings.CATEGORY11_STOCH_OVERBOUGHT) / (100 - settings.CATEGORY11_STOCH_OVERBOUGHT))
    anchor_score = _clip01(1 - c["vwap_distance_pct"] / (settings.CATEGORY11_VWAP_PROXIMITY_PCT * 2)) if c["anchor_label"] == "VWAP" else 0.7
    score = round((0.4 * rvol_score + 0.3 * stoch_score + 0.3 * anchor_score) * 100)
    reason = (
        f"[Score local, Grok indisponible] RVOL x{c['rvol']}, StochRSI %K={c['stoch_rsi_k']}, "
        f"repli sur {c['anchor_label']} ({c['vwap_distance_pct']}% du VWAP)."
    )
    return score, reason


async def build_category11(
    perpetuals: set | None = None,
    tickers: list | None = None,
) -> tuple[list[AssetSignal], list[str]]:
    """`perpetuals`/`tickers` : snapshot déjà récupéré par scanner.run_scan() pour ce
    cycle -> évite de refaire exchangeInfo + ticker/24hr (poids ~41). Cat.11 utilise
    des timeframes (15m/1m) différents de Cat.1/2 et Cat.10, donc aucune klines n'est
    partageable ici -- seule la liste de symboles bénéficie du cache."""
    errors: list[str] = []
    async with BinanceFuturesClient() as client:
        try:
            symbols = await client.get_top_symbols_by_volume(
                settings.CATEGORY11_TOP_N_SYMBOLS, settings.CATEGORY11_MIN_QUOTE_VOLUME,
                perpetuals=perpetuals, tickers=tickers,
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
                c = _mechanical_setup(b)
            except Exception as e:
                errors.append(f"Cat.11 {b['symbol']}: {e}")
                continue
            if c:
                candidates.append(c)

        if not candidates:
            logger.info(f"Cat.11 : {len(bundles)} paires exploitables, aucun setup mécanique validé (4 étapes).")
            return [], errors

        # Grok note et explique les setups déjà validés mécaniquement (pas de calcul d'indicateur par Grok)
        grok_payload = [
            {
                "symbol": c["symbol"], "direction": c["direction"], "rvol": c["rvol"],
                "stoch_rsi_k": c["stoch_rsi_k"], "zone_ancrage": c["anchor_label"],
                "distance_vwap_pct": c["vwap_distance_pct"], "distance_ema200_pct": c["ema_distance_pct"],
                "risk_reward": c["risk_reward"],
            }
            for c in candidates
        ]
        try:
            grok_scores = await score_candidates_with_grok(grok_payload)
        except Exception as e:
            errors.append(f"Cat.11 (Grok): {e}")
            grok_scores = None

        signals = []
        for c in candidates:
            grok_result = (grok_scores or {}).get(c["symbol"])
            if grok_result and "score" in grok_result:
                score = round(float(grok_result["score"]), 2)
                reason_ai = grok_result.get("reason", "")
                source_tag = "Grok"
            else:
                score, reason_ai = _local_fallback_score(c)
                source_tag = "local"

            reason = (
                f"[{source_tag}] Filtre EMA200(M15) {c['direction']} confirmé ({c['ema_distance_pct']}% de "
                f"distance) + pullback sur {c['anchor_label']} + RVOL x{c['rvol']} + bougie de retournement + "
                f"StochRSI %K={c['stoch_rsi_k']} | {reason_ai}"
            )
            signals.append(AssetSignal(
                symbol=c["symbol"], category="scalping_grok", score=score,
                trigger_type="technique+fondamental" if source_tag == "Grok" else "technique",
                trigger_reason=reason, direction=c["direction"], entry=c["entry"],
                stop_loss=c["stop_loss"], take_profit=c["take_profit"], risk_reward=c["risk_reward"],
                price=c["price"], atr_pct=c["atr_pct"], rsi_h1=c["rsi"], chop_h4=0.0,
                volume_ratio=c["rvol"], funding_rate=None, sparkline=c["sparkline"],
            ))

        top5 = sorted(signals, key=lambda s: s.score, reverse=True)[:5]
        logger.info(
            f"Cat.11 : {len(symbols)} symboles ciblés, {len(bundles)} exploitables, "
            f"{len(candidates)} setups validés mécaniquement, {len(top5)} retenus "
            f"(Grok {'utilisé' if grok_scores else 'indisponible -> repli local'})."
        )
        return top5, errors
