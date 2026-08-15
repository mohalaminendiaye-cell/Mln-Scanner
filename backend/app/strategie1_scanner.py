"""
Catégorie 6 / Stratégie 1 — Confluence Ichimoku + Volume Profile + Order Book
-----------------------------------------------------------------------------
Scanne jusqu'à STRATEGIE1_TOP_N_SYMBOLS paires Binance Futures (155 par défaut,
volume 24h >= STRATEGIE1_MIN_QUOTE_VOLUME) sur les timeframes 5m/15m/1H.

Architecture alignée sur la Stratégie 2 : UN SEUL filtre structurel bloquant
(le biais Ichimoku, qui est l'identité même de cette stratégie), le reste est
un SYSTÈME DE SCORING PONDÉRÉ sur 100 pts avec seuil minimum d'éligibilité —
au lieu d'une chaîne de 6 filtres tout-ou-rien comme dans la version initiale.

ÉLIGIBILITÉ STRICTE (bloquant) :
  - Biais Ichimoku identique sur 15m ET 1H (prix vs nuage + pente Kijun +
    "Chikou libre") -> Long ou Short, sinon le symbole est écarté.
  - Volume 24h >= STRATEGIE1_MIN_QUOTE_VOLUME (déjà filtré à la sélection).

SCORING (100 pts) :
  - [25 pts] Proximité Volume Profile : distance au niveau clé (POC/VAH/VAL)
    le plus proche, plein crédit à 0%, dégressif jusqu'à 2x
    STRATEGIE1_VP_PROXIMITY_PCT.
  - [20 pts] Choppiness Index (15m) : tendance nette (plein crédit si très
    bas), crédit partiel en sortie de consolidation, faible sinon.
  - [15 pts] RSI(14, 15m) : plein crédit proche de 50, dégressif vers les
    bornes STRATEGIE1_RSI_MIN/MAX.
  - [20 pts] RVOL(5m) : plein crédit à partir de 1.5x STRATEGIE1_MIN_RVOL.
  - [20 pts] Carnet d'ordres : déséquilibre favorable à la direction (crédit
    continu selon l'intensité) + bonus si un mur protecteur est détecté.

Seules les paires avec un score >= STRATEGIE1_MIN_SCORE (65/100, même seuil
que la Stratégie 2) sont retenues pour le top 5.

Le Stop Loss est placé derrière la Kijun (1H) et/ou le mur d'ordres le plus
proche (le plus protecteur des deux), le Take Profit vise ~STRATEGIE1_TARGET_PCT
(5% par défaut). Seuls les setups avec R:R >= STRATEGIE1_MIN_RR sont conservés.

⚠️ Filtre de sécurité : si le Choppiness Index (1H) de BTC dépasse
STRATEGIE1_BTC_PAUSE_CHOP, ou si une mèche 15m "violente" est détectée sur BTC
(range de la bougie > 2x son ATR% moyen récent), TOUS les signaux Long/Short
altcoins sont mis en pause pour ce scan (liste vide retournée), pour éviter les
faux signaux pendant une cascade de liquidations sur BTC.
"""
import asyncio
import logging

import pandas as pd

from .binance_client import BinanceFuturesClient
from .config import settings
from .indicators import (
    enrich_dataframe, choppiness_index, ichimoku_signal, volume_profile, atr as atr_indicator,
)
from .models import AssetSignal

logger = logging.getLogger("strategie1_scanner")

NO_SETUP_MESSAGE = "Marché actuellement en phase de consolidation/faible volatilité, peu de structures valides."


def _clip01(x: float) -> float:
    return float(max(0.0, min(1.0, x)))


def _klines_to_df(raw: list[list]) -> pd.DataFrame:
    df = pd.DataFrame([row[:6] for row in raw], columns=["ts", "open", "high", "low", "close", "volume"])
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    return df


async def _btc_pause_active(client: BinanceFuturesClient) -> tuple[bool, str]:
    """Sécurité : pause tous les signaux altcoins si BTC est trop choppy ou vient
    de faire une mèche violente sur 15m (risque de cascade de liquidations)."""
    try:
        k1h, k15 = await asyncio.gather(
            client.get_klines("BTCUSDT", "1h", limit=30),
            client.get_klines("BTCUSDT", "15m", limit=30),
        )
    except Exception as e:
        logger.warning(f"Stratégie 1 : filtre BTC ignoré (erreur de fetch) -> {e}")
        return False, ""

    if len(k1h) < 20 or len(k15) < 20:
        return False, ""

    df1h, df15 = _klines_to_df(k1h), _klines_to_df(k15)
    chop_btc = float(choppiness_index(df1h, 14).iloc[-1])

    atr15 = atr_indicator(df15, 14)
    atr_pct_recent = float((atr15 / df15["close"] * 100).tail(10).mean())
    last15 = df15.iloc[-1]
    range_pct = float((last15["high"] - last15["low"]) / last15["close"] * 100)
    violent_wick = atr_pct_recent > 0 and range_pct > 2 * atr_pct_recent

    if chop_btc > settings.STRATEGIE1_BTC_PAUSE_CHOP:
        return True, f"BTC CHOP(1H)={chop_btc:.1f} > seuil {settings.STRATEGIE1_BTC_PAUSE_CHOP}"
    if violent_wick:
        return True, f"BTC mèche 15m violente ({range_pct:.2f}% vs ATR moyen {atr_pct_recent:.2f}%)"
    return False, ""


async def _fetch_bundle(client: BinanceFuturesClient, symbol: str) -> dict | None:
    try:
        k5, k15, k1h = await asyncio.gather(
            client.get_klines(symbol, "5m", limit=60),
            client.get_klines(symbol, "15m", limit=130),
            client.get_klines(symbol, "1h", limit=130),
        )
        imbalance = await client.get_orderbook_imbalance(symbol)
        walls = await client.get_orderbook_walls(symbol)
    except Exception as e:
        logger.warning(f"Stratégie 1 {symbol}: erreur de récupération ignorée -> {e}")
        return None

    if len(k5) < 25 or len(k15) < 90 or len(k1h) < 90:
        return None

    df5 = enrich_dataframe(_klines_to_df(k5), settings)
    df15 = enrich_dataframe(_klines_to_df(k15), settings)
    df1h = enrich_dataframe(_klines_to_df(k1h), settings)
    df15["chop"] = choppiness_index(df15, 14)

    return {
        "symbol": symbol, "5m": df5, "15m": df15, "1h": df1h,
        "imbalance": imbalance, "walls": walls or {},
    }


def _evaluate(bundle: dict) -> AssetSignal | None:
    symbol = bundle["symbol"]
    df15, df1h, df5 = bundle["15m"], bundle["1h"], bundle["5m"]

    # --- Éligibilité stricte : biais Ichimoku confirmé sur 15m ET 1H (identité de la stratégie) ---
    ich_15 = ichimoku_signal(df15)
    ich_1h = ichimoku_signal(df1h)
    if not ich_15 or not ich_1h or ich_15["bias"] == "Neutre" or ich_15["bias"] != ich_1h["bias"]:
        return None
    direction = ich_15["bias"]
    price = float(df15["close"].iloc[-1])

    # --- Scoring (100 pts) ---
    # 1. Proximité Volume Profile (25 pts) : plein crédit à 0%, dégressif jusqu'à 2x la référence
    vp = volume_profile(df15, period=100, bins=24)
    levels = {"POC": vp["poc"], "VAH": vp["vah"], "VAL": vp["val"]}
    vp_label, vp_price = min(levels.items(), key=lambda kv: abs(price - kv[1]))
    vp_distance_pct = abs(price - vp_price) / price * 100
    vp_score = 25.0 * _clip01(1 - vp_distance_pct / (2 * settings.STRATEGIE1_VP_PROXIMITY_PCT))

    # 2. Choppiness Index 15m (20 pts) : tendance nette > sortie de range > consolidation
    chop_now = float(df15["chop"].iloc[-1])
    chop_prev = float(df15["chop"].iloc[-4]) if len(df15) > 4 else chop_now
    trending = chop_now < settings.STRATEGIE1_CHOP_TREND_MAX
    exiting_range = chop_prev >= settings.STRATEGIE1_CHOP_RANGE_MIN and chop_now < chop_prev
    if trending:
        chop_score = 20.0 * _clip01(1 - chop_now / settings.STRATEGIE1_CHOP_TREND_MAX)
    elif exiting_range:
        chop_score = 20.0 * 0.6
    else:
        chop_score = 20.0 * 0.15

    # 3. RSI(14, 15m) (15 pts) : plein crédit proche de 50, dégressif vers les bornes configurées
    rsi_15 = float(df15["rsi"].iloc[-1])
    rsi_band_half_width = max((settings.STRATEGIE1_RSI_MAX - settings.STRATEGIE1_RSI_MIN) / 2, 1.0)
    rsi_score = 15.0 * _clip01(1 - abs(rsi_15 - 50) / rsi_band_half_width)

    # 4. RVOL(5m) (20 pts) : plein crédit à 1.5x le seuil configuré
    rvol = float(df5["vol_ratio"].iloc[-1]) if not pd.isna(df5["vol_ratio"].iloc[-1]) else 0.0
    rvol_score = 20.0 * _clip01(rvol / (settings.STRATEGIE1_MIN_RVOL * 1.5))

    # 5. Carnet d'ordres (20 pts) : intensité du déséquilibre + bonus si mur protecteur détecté
    imbalance = bundle["imbalance"]
    walls = bundle["walls"]
    if direction == "Long":
        imb_score = _clip01((imbalance - 0.5) * 2) if imbalance is not None else 0.3
        has_wall = walls.get("bid_wall_price") is not None
    else:
        imb_score = _clip01((0.5 - imbalance) * 2) if imbalance is not None else 0.3
        has_wall = walls.get("ask_wall_price") is not None
    ob_score = 20.0 * _clip01(max(imb_score, 0.6 if has_wall else 0.0))

    total_score = round(vp_score + chop_score + rsi_score + rvol_score + ob_score, 2)
    if total_score < settings.STRATEGIE1_MIN_SCORE:
        return None

    # --- Plan de trade ---
    kijun_1h = ich_1h["kijun"]
    buffer = price * 0.0015
    if direction == "Long":
        wall_price = walls.get("bid_wall_price")
        protective_candidates = [v for v in (kijun_1h, wall_price) if v is not None and v < price]
        sl = (min(protective_candidates) if protective_candidates else price * 0.985) - buffer
        tp = price * (1 + settings.STRATEGIE1_TARGET_PCT)
        wall_used = wall_price
    else:
        wall_price = walls.get("ask_wall_price")
        protective_candidates = [v for v in (kijun_1h, wall_price) if v is not None and v > price]
        sl = (max(protective_candidates) if protective_candidates else price * 1.015) + buffer
        tp = price * (1 - settings.STRATEGIE1_TARGET_PCT)
        wall_used = wall_price

    risk = abs(price - sl)
    reward = abs(tp - price)
    rr = round(reward / risk, 2) if risk else 0
    if rr < settings.STRATEGIE1_MIN_RR:
        return None

    wall_txt = f", mur détecté à {wall_used:.6g}" if wall_used else ""
    imbalance_txt = f"{imbalance:.2f}" if imbalance is not None else "n/d"
    reason = (
        f"Ichimoku {direction} confirmé (15m+1H) : prix "
        f"{'au-dessus du nuage' if direction == 'Long' else 'sous le nuage'}, Kijun "
        f"{'haussière' if ich_1h['kijun_slope_up'] else 'baissière'} | "
        f"Zone de valeur : {vp_label} à {vp_price:.6g} ({vp_distance_pct:.2f}% du prix) | "
        f"CHOP(15m)={chop_now:.1f} ({'tendance nette' if trending else ('sortie de range' if exiting_range else 'consolidation')}) | "
        f"RSI(15m)={rsi_15:.1f} | RVOL(5m)=x{rvol:.2f} | "
        f"Carnet : imbalance={imbalance_txt}{wall_txt} | "
        f"Score: {total_score}/100 (VP:{vp_score:.0f} CHOP:{chop_score:.0f} RSI:{rsi_score:.0f} "
        f"RVOL:{rvol_score:.0f} Carnet:{ob_score:.0f})"
    )

    return AssetSignal(
        symbol=symbol,
        category="strategie1",
        score=total_score,
        trigger_type="technique",
        trigger_reason=reason,
        direction=direction,
        entry=round(price, 8),
        stop_loss=round(sl, 8),
        take_profit=round(tp, 8),
        risk_reward=rr,
        price=price,
        atr_pct=round(float(df1h["atr_pct"].iloc[-1]), 3),
        rsi_h1=round(rsi_15, 2),  # RSI(15m) — champ réutilisé (nom hérité du modèle générique)
        chop_h4=round(chop_now, 2),  # CHOP(15m) — champ réutilisé
        volume_ratio=round(rvol, 2),
        funding_rate=None,
        sparkline=[round(v, 8) for v in df15["close"].tail(24).tolist()],
    )


async def build_strategie1() -> tuple[list[AssetSignal], list[str]]:
    errors: list[str] = []
    async with BinanceFuturesClient() as client:
        try:
            paused, reason = await _btc_pause_active(client)
        except Exception as e:
            paused, reason = False, ""
            errors.append(f"Stratégie 1 (filtre BTC): {e}")

        if paused:
            logger.warning(f"Stratégie 1 : signaux mis en pause pour ce scan -> {reason}")
            errors.append(f"Stratégie 1 en pause (marché BTC instable) : {reason}")
            return [], errors

        try:
            symbols = await client.get_top_symbols_by_volume(
                settings.STRATEGIE1_TOP_N_SYMBOLS, settings.STRATEGIE1_MIN_QUOTE_VOLUME
            )
        except Exception as e:
            errors.append(f"Stratégie 1: impossible de lister les symboles -> {e}")
            return [], errors

        sem = asyncio.Semaphore(settings.MAX_CONCURRENT_REQUESTS)

        async def fetch(sym):
            async with sem:
                return await _fetch_bundle(client, sym)

        bundles = [b for b in await asyncio.gather(*(fetch(s) for s in symbols)) if b is not None]

        candidates = []
        for b in bundles:
            try:
                result = _evaluate(b)
            except Exception as e:
                errors.append(f"Stratégie 1 {b['symbol']}: {e}")
                continue
            if result:
                candidates.append(result)

        top5 = sorted(candidates, key=lambda c: c.score, reverse=True)[:5]
        if not top5:
            logger.info(f"Stratégie 1 : {NO_SETUP_MESSAGE}")
            errors.append(f"Stratégie 1 : {NO_SETUP_MESSAGE}")
        logger.info(
            f"Stratégie 1 : {len(symbols)} symboles ciblés, {len(bundles)} paires exploitables, "
            f"{len(candidates)} qualifiées (score >= {settings.STRATEGIE1_MIN_SCORE}), {len(top5)} retenues."
        )
        return top5, errors
