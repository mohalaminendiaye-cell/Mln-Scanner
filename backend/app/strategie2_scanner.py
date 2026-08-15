"""
Catégorie 6 / Stratégie 2 — Scalping ICT / VWAP (système de scoring pondéré)
-----------------------------------------------------------------------------
Scanne jusqu'à STRATEGIE2_TOP_N_SYMBOLS paires Binance Futures (volume 24h >=
STRATEGIE2_MIN_QUOTE_VOLUME, 15M$ par défaut) sur 1m/5m (structure/exécution)
et 15m (biais de tendance), pour du scalping visant une impulsion de ~1.5%
(1.2%-2.0%).

ÉLIGIBILITÉ STRICTE (conditions bloquantes, doivent TOUTES être vraies) :
  1. Volume 24h >= STRATEGIE2_MIN_QUOTE_VOLUME (déjà filtré à la sélection
     des symboles).
  2. Liquidity Sweep dans les STRATEGIE2_SWEEP_WINDOW dernières bougies
     (15 par défaut), détecté sur 1m OU 5m (5m prioritaire si les deux
     timeframes présentent un sweep valide).
  3. Market Structure Shift (CHoCH/MSS) validé sur ce même timeframe.
  4. Fair Value Gap présent dans le sens du setup, formé pendant l'impulsion
     de retournement (recherché sur la même fenêtre de 15 bougies).

Si ces 4 conditions sont réunies, un SCORE sur 100 est calculé (la divergence
SMT n'est PLUS une condition bloquante ici, contrairement à l'ancienne version
de cette stratégie — c'est un bonus de confirmation) :
  - VWAP (25 pts) : proximité au Session VWAP (bandes ±1 écart-type) + prix du
    bon côté (réintégration de bande cohérente avec la direction).
  - Qualité du FVG / Entry (25 pts) : entrée dans la zone du FVG = plein
    crédit, sinon crédit dégressif selon la distance en multiple d'ATR
    (STRATEGIE2_FVG_ATR_TOLERANCE).
  - Divergence SMT (20 pts, bonus) : vs BTCUSDT, puis ETHUSDT si BTC ne
    montre rien.
  - Volume d'expansion (15 pts) : RVOL sur la bougie de rupture, plein crédit
    à partir de STRATEGIE2_MIN_RVOL (1.3 par défaut).
  - Biais temporel 15m (15 pts) : la structure 1m/5m doit être alignée avec
    la tendance 15m (momentum simple sur les 10 dernières bougies 15m).

Seules les paires avec un score >= STRATEGIE2_MIN_SCORE (65/100 par défaut)
sont retenues pour le top 5. Si aucune paire n'atteint ce seuil, la Stratégie
retourne une liste vide avec le message spec : "Marché actuellement en phase
de consolidation/faible volatilité, peu de structures valides."

Le Stop Loss est placé juste au-delà de l'extrême du sweep (invalidation
ICT). Le Take Profit vise la liquidité structurelle opposée si elle tombe
dans la fourchette [STRATEGIE2_TARGET_MIN_PCT, STRATEGIE2_TARGET_MAX_PCT]
(1.2%-2.0%), sinon un objectif par défaut de STRATEGIE2_TARGET_PCT (1.5%).
Seuls les setups avec R:R >= STRATEGIE2_MIN_RR (2.0 par défaut) sont conservés.

⚠️ Filtre de sécurité BTC (garde-fou additionnel, hors spec) : signaux mis en
pause si le CHOP(1H) de BTC dépasse STRATEGIE2_BTC_PAUSE_CHOP ou si une mèche
15m violente est détectée — pour éviter les faux signaux pendant une cascade
de liquidations sur BTC.
"""
import asyncio
import logging

import pandas as pd

from .binance_client import BinanceFuturesClient
from .config import settings
from .indicators import (
    enrich_dataframe, choppiness_index, swing_points, detect_liquidity_sweep,
    detect_choch, detect_fvg, vwap_bands, smt_divergence, atr as atr_indicator,
)
from .models import AssetSignal

logger = logging.getLogger("strategie2_scanner")

NO_SETUP_MESSAGE = "Marché actuellement en phase de consolidation/faible volatilité, peu de structures valides."


def _clip01(x: float) -> float:
    return float(max(0.0, min(1.0, x)))


def _klines_to_df(raw: list[list]) -> pd.DataFrame:
    df = pd.DataFrame([row[:6] for row in raw], columns=["ts", "open", "high", "low", "close", "volume"])
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    return df


async def _btc_pause_active(client: BinanceFuturesClient) -> tuple[bool, str]:
    """Garde-fou de sécurité additionnel (hors spec) : pause si BTC trop choppy ou
    mèche 15m violente, comme pour la Stratégie 1."""
    try:
        k1h, k15 = await asyncio.gather(
            client.get_klines("BTCUSDT", "1h", limit=30),
            client.get_klines("BTCUSDT", "15m", limit=30),
        )
    except Exception as e:
        logger.warning(f"Stratégie 2 : filtre BTC ignoré (erreur de fetch) -> {e}")
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

    if chop_btc > settings.STRATEGIE2_BTC_PAUSE_CHOP:
        return True, f"BTC CHOP(1H)={chop_btc:.1f} > seuil {settings.STRATEGIE2_BTC_PAUSE_CHOP}"
    if violent_wick:
        return True, f"BTC mèche 15m violente ({range_pct:.2f}% vs ATR moyen {atr_pct_recent:.2f}%)"
    return False, ""


async def _fetch_ref_swings(client: BinanceFuturesClient, symbol: str) -> tuple[list, list]:
    """Swing highs/lows d'un actif référent SMT, calculés une seule fois par scan
    sur 5m et réutilisés pour toutes les paires évaluées."""
    k5 = await client.get_klines(symbol, "5m", limit=150)
    df5 = _klines_to_df(k5)
    return swing_points(df5, lookback=settings.STRATEGIE2_SWING_LOOKBACK)


async def _fetch_bundle(client: BinanceFuturesClient, symbol: str) -> dict | None:
    try:
        k1, k5, k15 = await asyncio.gather(
            client.get_klines(symbol, "1m", limit=150),
            client.get_klines(symbol, "5m", limit=150),
            client.get_klines(symbol, "15m", limit=60),
        )
    except Exception as e:
        logger.warning(f"Stratégie 2 {symbol}: erreur de récupération ignorée -> {e}")
        return None

    if len(k1) < 100 or len(k5) < 100 or len(k15) < 30:
        return None

    df1 = enrich_dataframe(_klines_to_df(k1), settings)
    df5 = enrich_dataframe(_klines_to_df(k5), settings)
    df15 = enrich_dataframe(_klines_to_df(k15), settings)

    return {"symbol": symbol, "1m": df1, "5m": df5, "15m": df15}


def _find_structure(df: pd.DataFrame) -> dict | None:
    """Cherche la chaîne Sweep -> CHoCH -> FVG sur un timeframe donné (1m ou 5m).
    Retourne None si l'une des 3 conditions bloquantes manque."""
    swing_highs, swing_lows = swing_points(df, lookback=settings.STRATEGIE2_SWING_LOOKBACK)
    sweep = detect_liquidity_sweep(df, swing_highs, swing_lows, recent_window=settings.STRATEGIE2_SWEEP_WINDOW)
    if sweep is None:
        return None
    if not detect_choch(df, sweep, swing_highs, swing_lows):
        return None
    fvg = detect_fvg(df, sweep["direction"], lookback=settings.STRATEGIE2_SWEEP_WINDOW)
    if fvg is None:
        return None
    return {"sweep": sweep, "fvg": fvg, "swing_highs": swing_highs, "swing_lows": swing_lows}


def _bias_15m_score(df15: pd.DataFrame, direction: str) -> float:
    """15 pts : la structure 1m/5m doit être alignée avec la tendance 15m (momentum
    simple : clôture actuelle vs clôture 10 bougies plus tôt)."""
    if len(df15) < 11:
        return 7.5  # historique insuffisant -> ni pour ni contre
    now, past = float(df15["close"].iloc[-1]), float(df15["close"].iloc[-11])
    pct_change = (now - past) / past if past else 0
    trend_up = pct_change > 0.001
    trend_down = pct_change < -0.001
    if direction == "Long":
        return 15.0 if trend_up else (7.5 if not trend_down else 0.0)
    return 15.0 if trend_down else (7.5 if not trend_up else 0.0)


def _smt_score(direction: str, alt_swings: list, ref1_swings: list, ref2_swings: list,
               ref1_label: str, ref2_label: str) -> tuple[float, str]:
    """20 pts bonus : divergence SMT vs BTC en priorité, puis ETH si BTC ne montre rien."""
    if smt_divergence(alt_swings, ref1_swings, direction):
        return 20.0, f"Oui (vs {ref1_label})"
    if smt_divergence(alt_swings, ref2_swings, direction):
        return 20.0, f"Oui (vs {ref2_label})"
    return 0.0, "Non"


def _evaluate(bundle: dict, ref_swings: dict) -> AssetSignal | None:
    symbol = bundle["symbol"]
    df1, df5, df15 = bundle["1m"], bundle["5m"], bundle["15m"]

    # --- Éligibilité stricte : Sweep + CHoCH + FVG, sur 5m en priorité, sinon 1m ---
    struct_5m = _find_structure(df5)
    struct_1m = _find_structure(df1)
    if struct_5m is not None:
        struct, df_struct, tf_label = struct_5m, df5, "5m"
    elif struct_1m is not None:
        struct, df_struct, tf_label = struct_1m, df1, "1m"
    else:
        return None

    sweep, fvg = struct["sweep"], struct["fvg"]
    direction = sweep["direction"]
    price = float(df_struct["close"].iloc[-1])

    # --- Scoring (100 pts) ---
    # 1. VWAP (25 pts)
    vwb = vwap_bands(df_struct, period=100, band_mult=1.0)
    band_half_width_pct = max(abs(vwb["upper"] - vwb["lower"]) / price * 100 / 2, 0.15)
    vwap_distance_pct = abs(price - vwb["vwap"]) / price * 100
    vwap_proximity = _clip01(1 - vwap_distance_pct / band_half_width_pct)
    side_ok = price >= vwb["lower"] if direction == "Long" else price <= vwb["upper"]
    vwap_score = 25.0 * vwap_proximity * (1.0 if side_ok else 0.4)

    # 2. Qualité du FVG / Entry (25 pts)
    if fvg["price_in_gap"]:
        fvg_score = 25.0
    else:
        atr_val = float(df_struct["atr"].iloc[-1]) if not pd.isna(df_struct["atr"].iloc[-1]) else 0
        gap_distance = min(abs(price - fvg["gap_low"]), abs(price - fvg["gap_high"]))
        fvg_proximity = _clip01(1 - gap_distance / (settings.STRATEGIE2_FVG_ATR_TOLERANCE * atr_val)) if atr_val > 0 else 0
        fvg_score = 25.0 * fvg_proximity

    # 3. Divergence SMT vs BTC puis ETH (20 pts, bonus)
    alt_swings = struct["swing_lows"] if direction == "Long" else struct["swing_highs"]
    ref1_swings = ref_swings["ref1_lows"] if direction == "Long" else ref_swings["ref1_highs"]
    ref2_swings = ref_swings["ref2_lows"] if direction == "Long" else ref_swings["ref2_highs"]
    smt_score, smt_detail = _smt_score(
        direction, alt_swings, ref1_swings, ref2_swings,
        settings.STRATEGIE2_SMT_REFERENCE, settings.STRATEGIE2_SMT_SECONDARY_REFERENCE,
    )

    # 4. Volume d'expansion / RVOL (15 pts)
    rvol = float(df_struct["vol_ratio"].iloc[-1]) if not pd.isna(df_struct["vol_ratio"].iloc[-1]) else 0.0
    rvol_score = 15.0 * _clip01(rvol / settings.STRATEGIE2_MIN_RVOL)

    # 5. Biais temporel 15m (15 pts)
    bias_score = _bias_15m_score(df15, direction)

    total_score = round(vwap_score + fvg_score + smt_score + rvol_score + bias_score, 2)
    if total_score < settings.STRATEGIE2_MIN_SCORE:
        return None

    # --- Plan de trade ---
    entry = round((fvg["gap_low"] + fvg["gap_high"]) / 2, 8)
    buffer = price * 0.0015
    sl = sweep["sweep_extreme"] - buffer if direction == "Long" else sweep["sweep_extreme"] + buffer

    opposite_swings = struct["swing_highs"] if direction == "Long" else struct["swing_lows"]
    beyond = [p for i, p in opposite_swings if (p > entry if direction == "Long" else p < entry)]
    structural_target = (min(beyond) if direction == "Long" else max(beyond)) if beyond else None
    default_tp = entry * (1 + settings.STRATEGIE2_TARGET_PCT if direction == "Long" else 1 - settings.STRATEGIE2_TARGET_PCT)
    if structural_target is not None:
        dist_pct = abs(structural_target - entry) / entry
        tp = structural_target if settings.STRATEGIE2_TARGET_MIN_PCT <= dist_pct <= settings.STRATEGIE2_TARGET_MAX_PCT else default_tp
    else:
        tp = default_tp

    risk = abs(entry - sl)
    reward = abs(tp - entry)
    rr = round(reward / risk, 2) if risk else 0
    if rr < settings.STRATEGIE2_MIN_RR:
        return None

    reason = (
        f"[{tf_label}] Liquidity Sweep {'sell-side' if direction == 'Long' else 'buy-side'} à "
        f"{sweep['swept_level']:.6g} (mèche à {sweep['sweep_extreme']:.6g}) + CHoCH confirmé + FVG "
        f"{'retesté' if fvg['price_in_gap'] else 'proche'} [{fvg['gap_low']:.6g}-{fvg['gap_high']:.6g}] + "
        f"VWAP Test ({vwap_distance_pct:.2f}% du VWAP) + SMT {smt_detail} | "
        f"Score: {total_score}/100 (VWAP:{vwap_score:.0f} FVG:{fvg_score:.0f} SMT:{smt_score:.0f} "
        f"RVOL:{rvol_score:.0f} Biais15m:{bias_score:.0f}) | RVOL={rvol:.2f} | Distance VWAP: {vwap_distance_pct:.2f}%"
    )

    return AssetSignal(
        symbol=symbol,
        category="strategie2",
        score=total_score,
        trigger_type="technique",
        trigger_reason=reason,
        direction=direction,
        entry=entry,
        stop_loss=round(sl, 8),
        take_profit=round(tp, 8),
        risk_reward=rr,
        price=price,
        atr_pct=round(float(df_struct["atr_pct"].iloc[-1]), 3),
        rsi_h1=round(float(df_struct["rsi"].iloc[-1]), 2),  # RSI(TF structure) — champ réutilisé
        chop_h4=0.0,  # non utilisé dans cette version de la Stratégie 2 (pas de filtre CHOP dans la spec)
        volume_ratio=round(rvol, 2),
        funding_rate=None,
        sparkline=[round(v, 8) for v in df_struct["close"].tail(24).tolist()],
    )


async def build_strategie2() -> tuple[list[AssetSignal], list[str]]:
    errors: list[str] = []
    async with BinanceFuturesClient() as client:
        try:
            paused, reason = await _btc_pause_active(client)
        except Exception as e:
            paused, reason = False, ""
            errors.append(f"Stratégie 2 (filtre BTC): {e}")

        if paused:
            logger.warning(f"Stratégie 2 : signaux mis en pause pour ce scan -> {reason}")
            errors.append(f"Stratégie 2 en pause (marché BTC instable) : {reason}")
            return [], errors

        try:
            ref1_highs, ref1_lows = await _fetch_ref_swings(client, settings.STRATEGIE2_SMT_REFERENCE)
            ref2_highs, ref2_lows = await _fetch_ref_swings(client, settings.STRATEGIE2_SMT_SECONDARY_REFERENCE)
        except Exception as e:
            errors.append(f"Stratégie 2 (référence SMT): {e}")
            return [], errors
        ref_swings = {
            "ref1_highs": ref1_highs, "ref1_lows": ref1_lows,
            "ref2_highs": ref2_highs, "ref2_lows": ref2_lows,
        }

        try:
            symbols = await client.get_top_symbols_by_volume(
                settings.STRATEGIE2_TOP_N_SYMBOLS, settings.STRATEGIE2_MIN_QUOTE_VOLUME
            )
        except Exception as e:
            errors.append(f"Stratégie 2: impossible de lister les symboles -> {e}")
            return [], errors

        sem = asyncio.Semaphore(settings.MAX_CONCURRENT_REQUESTS)

        async def fetch(sym):
            async with sem:
                return await _fetch_bundle(client, sym)

        bundles = [b for b in await asyncio.gather(*(fetch(s) for s in symbols)) if b is not None]

        candidates = []
        for b in bundles:
            try:
                result = _evaluate(b, ref_swings)
            except Exception as e:
                errors.append(f"Stratégie 2 {b['symbol']}: {e}")
                continue
            if result:
                candidates.append(result)

        top5 = sorted(candidates, key=lambda c: c.score, reverse=True)[:5]
        if not top5:
            logger.info(f"Stratégie 2 : {NO_SETUP_MESSAGE}")
            errors.append(f"Stratégie 2 : {NO_SETUP_MESSAGE}")
        logger.info(
            f"Stratégie 2 : {len(symbols)} symboles ciblés, {len(bundles)} paires exploitables, "
            f"{len(candidates)} qualifiées (score >= {settings.STRATEGIE2_MIN_SCORE}), {len(top5)} retenues."
        )
        return top5, errors
