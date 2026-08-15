"""
Logique de scan du marché.

Catégorie 1 - Probabilité de mouvement significatif (±5% / 24h)
-----------------------------------------------------------------
Score composite pondéré à partir de :
  - Volatilité (ATR% en H1)                         -> poids 0.25
  - Volume anormal (volume / SMA20 volume)           -> poids 0.20
  - Momentum (extrémité RSI + pente MACD histogram)  -> poids 0.20
  - Compression -> expansion des Bandes de Bollinger  -> poids 0.15
    (un "squeeze" qui commence à se relâcher precède souvent un move fort)
  - Funding rate extrême (positionnement excessif)    -> poids 0.10
  - Corrélation / divergence avec BTC                 -> poids 0.10

Catégorie 2 - Choppiness Index élevé (H4 > 60)
-----------------------------------------------------------------
Parmi les paires avec CHOP(H4) > seuil, on classe par potentiel de sortie
de range : compression des Bandes de Bollinger (bb_width bas) + volume en
déclin (marché qui "respire" avant un breakout) + proximité des bornes du
range récent.

Chaque actif retenu reçoit un plan de trade complet (entrée, SL, TP, R:R).
"""
import asyncio
import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .binance_client import BinanceFuturesClient, BinanceAPIError
from .config import settings
from .indicators import enrich_dataframe, choppiness_index, liquidation_zones_multi, closest_liquidation_zone
from .models import (
    AssetSignal, ScanResult,
    DerivativesAltcoin, BonusTrading, LiquidationZone,
)
from . import ai_research
from .multi_exchange_scanner import build_categories_7_9
from .category10_scanner import build_category10
from .category11_scanner import build_category11
from .strategies_scanner import build_category6_strategies

logger = logging.getLogger("scanner")


def _klines_to_df(raw: list[list]) -> pd.DataFrame:
    cols = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore",
    ]
    df = pd.DataFrame(raw, columns=cols)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    return df


def _clip01(x: float) -> float:
    return float(max(0.0, min(1.0, x)))


async def _fetch_symbol_data(client: BinanceFuturesClient, symbol: str) -> dict | None:
    """Récupère H1 (pour momentum/volatilité) + H4 (pour Choppiness) + funding + OI."""
    try:
        h1_raw, h4_raw, funding, open_interest, oi_change_pct = await asyncio.gather(
            client.get_klines(symbol, "1h", limit=200),  # 200h ≈ 8j, couvre le lookback 7j
            client.get_klines(symbol, "4h", limit=100),
            client.get_funding_rate(symbol),
            client.get_open_interest(symbol),
            client.get_open_interest_change_24h_pct(symbol),
        )
    except BinanceAPIError as e:
        logger.warning(f"{symbol}: erreur API ignorée -> {e}")
        return None
    except Exception as e:
        logger.warning(f"{symbol}: erreur inattendue ignorée -> {e}")
        return None

    if len(h1_raw) < 60 or len(h4_raw) < 30:
        return None

    df_h1 = enrich_dataframe(_klines_to_df(h1_raw), settings)
    df_h4 = _klines_to_df(h4_raw)
    df_h4["chop"] = choppiness_index(df_h4, settings.CHOP_PERIOD)

    return {
        "symbol": symbol, "h1": df_h1, "h4": df_h4,
        "funding": funding, "open_interest": open_interest, "oi_change_pct": oi_change_pct,
    }


def _score_category1(data: dict, btc_change_pct: float) -> tuple[float, dict]:
    h1 = data["h1"]
    last = h1.iloc[-1]
    prev = h1.iloc[-2]

    # 1. Volatilité : ATR% par rapport à l'objectif de mouvement (5%)
    vol_score = _clip01(last["atr_pct"] / (settings.TARGET_MOVE_PCT * 100) * 0.6)

    # 2. Volume anormal
    volume_ratio = float(last["vol_ratio"]) if not np.isnan(last["vol_ratio"]) else 1.0
    volume_score = _clip01((volume_ratio - 1) / 3)

    # 3. Momentum : extrémité du RSI + accélération du MACD histogram
    rsi_extremity = abs(last["rsi"] - 50) / 50
    macd_accel = abs(last["macd_hist"] - prev["macd_hist"])
    macd_norm = _clip01(macd_accel / (last["close"] * 0.002 + 1e-9))
    momentum_score = _clip01(0.6 * rsi_extremity + 0.4 * macd_norm)

    # 4. Squeeze qui se relâche (bb_width remonte après une phase de compression)
    bb_width_recent = h1["bb_width"].tail(30)
    if bb_width_recent.notna().sum() >= 10:
        percentile_min = bb_width_recent.min()
        is_was_squeezed = bb_width_recent.iloc[:-3].min() <= bb_width_recent.quantile(0.25)
        expanding_now = last["bb_width"] > bb_width_recent.iloc[-4]
        squeeze_score = 1.0 if (is_was_squeezed and expanding_now) else 0.3
    else:
        squeeze_score = 0.3

    # 5. Funding rate extrême (positionnement excessif long/short -> risque de squeeze)
    funding = data["funding"] or 0.0
    funding_score = _clip01(abs(funding) / 0.01)  # 1% de funding = score max

    # 6. Divergence / alignement avec BTC (si le prix évolue fort indépendamment de BTC
    #    ou de façon amplifiée dans le même sens, ça renforce la probabilité de mouvement)
    symbol_change_pct = (last["close"] / h1["close"].iloc[-25] - 1) * 100  # ~24h en H1
    corr_score = _clip01(abs(symbol_change_pct - btc_change_pct) / 8)

    weighted = (
        0.25 * vol_score
        + 0.20 * volume_score
        + 0.20 * momentum_score
        + 0.15 * squeeze_score
        + 0.10 * funding_score
        + 0.10 * corr_score
    )
    score = round(weighted * 100, 2)

    details = {
        "vol_score": vol_score,
        "volume_score": volume_score,
        "momentum_score": momentum_score,
        "squeeze_score": squeeze_score,
        "funding_score": funding_score,
        "corr_score": corr_score,
        "volume_ratio": volume_ratio,
        "symbol_change_pct": symbol_change_pct,
    }
    return score, details


def _direction_from_trend(last) -> str:
    bullish = last["close"] > last["ema20"] > last["ema50"] and last["macd_hist"] > 0
    bearish = last["close"] < last["ema20"] < last["ema50"] and last["macd_hist"] < 0
    if bullish and last["rsi"] > 50:
        return "Long"
    if bearish and last["rsi"] < 50:
        return "Short"
    # cas mixte : on tranche sur le signe du MACD histogram
    return "Long" if last["macd_hist"] > 0 else "Short"


def _trigger_text_cat1(details: dict, funding: float | None) -> tuple[str, str]:
    reasons = []
    trigger_type = "technique"
    if details["squeeze_score"] >= 1.0:
        reasons.append("sortie de compression des Bandes de Bollinger (squeeze release)")
    if details["volume_score"] > 0.5:
        reasons.append(f"volume anormal (x{details['volume_ratio']:.1f} vs moyenne 20 périodes)")
    if details["momentum_score"] > 0.5:
        reasons.append("momentum fort (RSI extrême / accélération MACD)")
    if details["funding_score"] > 0.5 and funding is not None:
        reasons.append(f"funding rate extrême ({funding*100:.3f}%)")
        trigger_type = "technique+fondamental"
    if not reasons:
        reasons.append("volatilité (ATR) élevée par rapport à la normale")
    return trigger_type, ", ".join(reasons)


def _build_trade_plan_trend(last, direction: str) -> tuple[float, float, float, float]:
    """Plan de trade momentum (Catégorie 1)."""
    price = float(last["close"])
    atr_val = float(last["atr"]) if not np.isnan(last["atr"]) else price * 0.02

    if direction == "Long":
        entry = price
        stop_loss = entry - 1.5 * atr_val
        risk = entry - stop_loss
        take_profit = entry + max(2 * risk, entry * settings.TARGET_MOVE_PCT)
    else:  # Short
        entry = price
        stop_loss = entry + 1.5 * atr_val
        risk = stop_loss - entry
        take_profit = entry - max(2 * risk, entry * settings.TARGET_MOVE_PCT)

    rr = abs(take_profit - entry) / abs(entry - stop_loss) if entry != stop_loss else 0
    return round(entry, 6), round(stop_loss, 6), round(take_profit, 6), round(rr, 2)


def _build_trade_plan_range(h1: pd.DataFrame, bias_long: bool) -> tuple[float, float, float, float]:
    """Plan de trade breakout anticipé (Catégorie 2 - range)."""
    lookback = h1.tail(settings.RANGE_LOOKBACK)
    range_high = float(lookback["high"].max())
    range_low = float(lookback["low"].min())
    range_size = range_high - range_low
    atr_val = float(h1.iloc[-1]["atr"])

    if bias_long:
        entry = range_high  # entrée stop à la cassure du haut de range
        stop_loss = range_high - max(0.5 * range_size, atr_val)
        take_profit = entry + range_size  # projection = amplitude du range
    else:
        entry = range_low
        stop_loss = range_low + max(0.5 * range_size, atr_val)
        take_profit = entry - range_size

    rr = abs(take_profit - entry) / abs(entry - stop_loss) if entry != stop_loss else 0
    return round(entry, 6), round(stop_loss, 6), round(take_profit, 6), round(rr, 2)


def _score_category2(data: dict) -> tuple[float, float] | None:
    """Retourne (chop_h4, compression_score) ou None si CHOP <= seuil."""
    h4 = data["h4"]
    last_chop = float(h4["chop"].iloc[-1])
    if last_chop <= settings.CHOP_THRESHOLD or np.isnan(last_chop):
        return None

    h1 = data["h1"]
    bb_width_recent = h1["bb_width"].tail(30)
    if bb_width_recent.notna().sum() < 10:
        return last_chop, 0.3

    current_width_percentile = (bb_width_recent < bb_width_recent.iloc[-1]).mean()
    compression_score = 1 - current_width_percentile  # bande étroite = score élevé

    vol_recent = h1["volume"].tail(10).mean()
    vol_prior = h1["volume"].tail(30).head(20).mean()
    volume_decline_bonus = 0.2 if vol_prior > 0 and vol_recent < vol_prior else 0.0

    score = _clip01(compression_score + volume_decline_bonus)
    return last_chop, score


def _build_correlation_signals(results: list[dict | None], btc_change_pct: float) -> list[AssetSignal]:
    """Catégorie 4 : altcoins dont la variation 24h diverge le plus de celle de BTC.
    Une forte divergence (positive ou négative) suggère un catalyseur propre à l'actif
    (news, listing, liquidation en cascade...) plutôt qu'un simple effet de marché global."""
    candidates = []
    for data in results:
        if data is None or data["symbol"] == "BTCUSDT":
            continue
        h1 = data["h1"]
        last = h1.iloc[-1]
        if len(h1) < 26 or last[["atr", "rsi", "macd_hist"]].isna().any():
            continue
        symbol_change_pct = float((last["close"] / h1["close"].iloc[-25] - 1) * 100)
        divergence = symbol_change_pct - btc_change_pct

        direction = "Long" if divergence > 0 else "Short"
        entry, sl, tp, rr = _build_trade_plan_trend(last, direction)
        if rr < settings.MIN_RR:
            continue

        sens = "surperforme" if divergence > 0 else "sous-performe"
        reason = (
            f"{sens} BTC de {abs(divergence):.1f} points sur 24h "
            f"(actif: {symbol_change_pct:+.1f}%, BTC: {btc_change_pct:+.1f}%) — "
            "mouvement probablement lié à un catalyseur propre à l'actif"
        )
        candidates.append(
            AssetSignal(
                symbol=data["symbol"],
                category="correlation_btc",
                score=round(min(abs(divergence) * 5, 100), 2),
                trigger_type="technique+fondamental",
                trigger_reason=reason,
                direction=direction,
                entry=entry,
                stop_loss=sl,
                take_profit=tp,
                risk_reward=rr,
                price=float(last["close"]),
                atr_pct=round(float(last["atr_pct"]), 3),
                rsi_h1=round(float(last["rsi"]), 2),
                chop_h4=round(float(data["h4"]["chop"].iloc[-1]), 2),
                volume_ratio=round(float(last["vol_ratio"]), 2),
                funding_rate=data["funding"],
                sparkline=[round(v, 8) for v in h1["close"].tail(24).tolist()],
            )
        )
    return sorted(candidates, key=lambda c: c.score, reverse=True)[:5]


def _build_bonus_derivatives(results: list[dict | None]) -> list[DerivativesAltcoin]:
    """Bonus Trading, partie 2 : top 3 altcoins (hors BTC/ETH) dont l'Open Interest a le
    plus progressé sur 24h ET dont le funding rate est extrême — signe d'un positionnement
    à effet de levier fortement crowded, terreau d'un short ou long squeeze. Réutilise
    l'estimation de zone de liquidation (mêmes niveaux de levier que la Catégorie 5)."""
    candidates = []
    for data in results:
        symbol = data["symbol"] if data else None
        if data is None or symbol in ("BTCUSDT", "ETHUSDT"):
            continue
        if data.get("open_interest") is None or data.get("oi_change_pct") is None:
            continue
        h1 = data["h1"]
        last = h1.iloc[-1]
        if len(h1) < 20 or last[["atr"]].isna().any():
            continue

        funding = data["funding"] or 0.0
        oi_change = data["oi_change_pct"]
        # Score combiné : progression d'OI (normalisée à 30% = score max) + funding extrême
        oi_score = _clip01(abs(oi_change) / 30)
        funding_score = _clip01(abs(funding) / 0.01)
        score = 0.55 * oi_score + 0.45 * funding_score
        if score <= 0.15:  # écarte le bruit
            continue

        price = float(last["close"])
        zones = liquidation_zones_multi(price, settings.LIQUIDATION_LEVERAGE_LEVELS)
        side = "long" if funding > 0 else "short"
        closest = closest_liquidation_zone(zones, price, side)
        zone_price = closest["long_price"] if side == "long" else closest["short_price"]
        zone_side = "Squeeze longs" if funding > 0 else "Squeeze shorts"
        distance_pct = round(abs(price - zone_price) / price * 100, 2)

        candidates.append(
            (
                score,
                DerivativesAltcoin(
                    symbol=symbol,
                    oi_change_24h_pct=oi_change,
                    funding_rate=funding,
                    price=price,
                    nearest_liquidation_zone=round(zone_price, 6),
                    zone_distance_pct=distance_pct,
                    zone_side=zone_side,
                    reasoning=(
                        f"OI {'en hausse' if oi_change >= 0 else 'en baisse'} de {abs(oi_change):.1f}% "
                        f"sur 24h, funding {'très positif' if funding > 0 else 'très négatif'} "
                        f"({funding*100:.3f}%) -> positionnement à effet de levier fortement crowded. "
                        f"Zone de {zone_side.lower()} estimée à {zone_price:.6g} ({distance_pct:.1f}% du prix), "
                        f"levier le plus proche : {closest['leverage']}x."
                    ),
                    liquidation_zones=[LiquidationZone(**z) for z in zones],
                ),
            )
        )
    candidates.sort(key=lambda c: c[0], reverse=True)
    return [c[1] for c in candidates[:3]]


async def run_scan() -> ScanResult:
    """Exécute un scan complet du marché et retourne les 2 catégories de résultats."""
    errors: list[str] = []
    async with BinanceFuturesClient() as client:
        try:
            symbols = await client.get_top_symbols_by_volume(settings.TOP_N_SYMBOLS)
        except Exception as e:
            logger.error(f"Impossible de récupérer la liste des symboles: {e}")
            raise

        # Référence BTC pour la corrélation
        btc_data = await _fetch_symbol_data(client, "BTCUSDT")
        btc_change_pct = 0.0
        if btc_data is not None:
            h1 = btc_data["h1"]
            btc_change_pct = float((h1["close"].iloc[-1] / h1["close"].iloc[-25] - 1) * 100)

        sem = asyncio.Semaphore(settings.MAX_CONCURRENT_REQUESTS)

        async def fetch_with_sem(sym):
            async with sem:
                return await _fetch_symbol_data(client, sym)

        results = await asyncio.gather(*(fetch_with_sem(s) for s in symbols))

    cat1_candidates = []
    cat2_candidates = []

    for data in results:
        if data is None:
            continue
        symbol = data["symbol"]
        h1 = data["h1"]
        last_h1 = h1.iloc[-1]

        if last_h1[["atr", "rsi", "macd_hist", "bb_width"]].isna().any():
            continue

        # ---- Catégorie 1 ----
        try:
            score1, details = _score_category1(data, btc_change_pct)
            direction = _direction_from_trend(last_h1)
            trigger_type, trigger_reason = _trigger_text_cat1(details, data["funding"])
            entry, sl, tp, rr = _build_trade_plan_trend(last_h1, direction)
            sparkline = [round(v, 8) for v in h1["close"].tail(24).tolist()]
            cat1_candidates.append(
                AssetSignal(
                    symbol=symbol,
                    category="probabilite_mouvement",
                    score=score1,
                    trigger_type=trigger_type,
                    trigger_reason=trigger_reason,
                    direction=direction,
                    entry=entry,
                    stop_loss=sl,
                    take_profit=tp,
                    risk_reward=rr,
                    price=float(last_h1["close"]),
                    atr_pct=round(float(last_h1["atr_pct"]), 3),
                    rsi_h1=round(float(last_h1["rsi"]), 2),
                    chop_h4=round(float(data["h4"]["chop"].iloc[-1]), 2),
                    volume_ratio=round(details["volume_ratio"], 2),
                    funding_rate=data["funding"],
                    sparkline=sparkline,
                )
            )
        except Exception as e:
            errors.append(f"{symbol} (cat1): {e}")

        # ---- Catégorie 2 ----
        try:
            chop_result = _score_category2(data)
            if chop_result is not None:
                chop_h4, comp_score = chop_result
                # biais directionnel indicatif basé sur la tendance de fond (EMA50 pente) :
                # affiché à côté de "Neutre" pour indiquer le sens du trade de breakout proposé
                # (le marché est en range, donc la direction n'est pas encore confirmée, mais
                # le plan d'entrée/SL/TP ci-dessous est construit dans ce sens précis).
                ema50_series = h1["ema50"].tail(10)
                bias_long = ema50_series.iloc[-1] >= ema50_series.iloc[0]
                entry, sl, tp, rr = _build_trade_plan_range(h1, bias_long)
                direction_label = f"Neutre ({'Long' if bias_long else 'Short'})"
                sparkline = [round(v, 8) for v in h1["close"].tail(24).tolist()]
                cat2_candidates.append(
                    AssetSignal(
                        symbol=symbol,
                        category="chop_eleve",
                        score=round(comp_score * 100, 2),
                        trigger_type="technique",
                        trigger_reason=(
                            f"Choppiness Index H4 = {chop_h4:.1f} (>60, marché en range), "
                            "compression des Bandes de Bollinger détectée -> "
                            "sortie de range probable"
                        ),
                        direction=direction_label,
                        entry=entry,
                        stop_loss=sl,
                        take_profit=tp,
                        risk_reward=rr,
                        price=float(last_h1["close"]),
                        atr_pct=round(float(last_h1["atr_pct"]), 3),
                        rsi_h1=round(float(last_h1["rsi"]), 2),
                        chop_h4=round(chop_h4, 2),
                        volume_ratio=round(float(last_h1["vol_ratio"]), 2),
                        funding_rate=data["funding"],
                        sparkline=sparkline,
                    )
                )
        except Exception as e:
            errors.append(f"{symbol} (cat2): {e}")

    # Tri et sélection du top 5, en ne gardant que les setups avec R:R >= MIN_RR
    cat1_final = sorted(
        [c for c in cat1_candidates if c.risk_reward >= settings.MIN_RR],
        key=lambda c: c.score,
        reverse=True,
    )[:5]
    cat2_final = sorted(
        [c for c in cat2_candidates if c.risk_reward >= settings.MIN_RR],
        key=lambda c: c.score,
        reverse=True,
    )[:5]

    cat4_final = _build_correlation_signals(results, btc_change_pct)

    cat6_final, cat6_errors = await build_category6_strategies(results)
    errors.extend(cat6_errors)

    # Bonus Trading partie 1 (pics sociaux) nécessite une recherche X/web via Grok :
    # dégradation gracieuse si GROK_API_KEY absente.
    try:
        social_spikes = await ai_research.fetch_social_spikes()
    except Exception as e:
        errors.append(f"Bonus Trading (pics sociaux): {e}")
        social_spikes = []

    derivatives_top3 = _build_bonus_derivatives(results)
    bonus_trading = BonusTrading(
        social_spikes=social_spikes,
        social_spikes_available=bool(settings.GROK_API_KEY),
        derivatives_top3=derivatives_top3,
    )

    # Catégories 7/9 : analyse multi-exchange (Top Movers). Exécutée en dernier et
    # isolée dans son propre try/except : un problème sur OKX/Bybit ne doit jamais
    # faire échouer le reste du scan Binance.
    try:
        cat7_final, cat9_final, multi_exchange_errors = await build_categories_7_9()
        errors.extend(multi_exchange_errors)
    except Exception as e:
        logger.exception("Échec complet des Catégories 7/9 (multi-exchange)")
        errors.append(f"Catégories 7/9 (multi-exchange): {e}")
        cat7_final, cat9_final = {}, {}

    # Catégorie 10 : Global Breakout Score (Binance + Bybit), isolée dans son
    # propre try/except pour ne jamais faire échouer le reste du scan.
    try:
        cat10_final, cat10_errors = await build_category10()
        errors.extend(cat10_errors)
    except Exception as e:
        logger.exception("Échec complet de la Catégorie 10 (GSB)")
        errors.append(f"Catégorie 10 (GSB): {e}")
        cat10_final = []

    # Catégorie 11 : Scalping IA (Grok), isolée dans son propre try/except.
    try:
        cat11_final, cat11_errors = await build_category11()
        errors.extend(cat11_errors)
    except Exception as e:
        logger.exception("Échec complet de la Catégorie 11 (Scalping IA)")
        errors.append(f"Catégorie 11 (Scalping IA): {e}")
        cat11_final = []

    return ScanResult(
        timestamp=datetime.now(timezone.utc),
        category1=cat1_final,
        category2=cat2_final,
        category4=cat4_final,
        category6=cat6_final,
        bonus_trading=bonus_trading,
        category7=cat7_final,
        category9=cat9_final,
        category10=cat10_final,
        category11=cat11_final,
        symbols_analyzed=len(results),
        errors=errors[:30],
    )
