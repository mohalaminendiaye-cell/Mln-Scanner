"""
Catégorie 10 — Global Breakout Score (GSB), positionnée AU-DESSUS de la
Catégorie 9 dans l'onglet "Top Movers".

Combine 5 facteurs quantitatifs (0-100 chacun) sur Binance Futures ET Bybit
Futures, pondérés selon le cahier des charges :
  GSB = 0.25*VSI + 0.20*RVOL + 0.25*OIFD + 0.15*MSD + 0.15*CORR
Seules les paires avec GSB >= GSB_MIN_SCORE (60 par défaut) sont retenues,
puis on garde le top 5 TOUTES EXCHANGES CONFONDUES (classement unique, pas
un top 5 par exchange comme pour les Catégories 7/8). Si AUCUNE paire
n'atteint ce seuil sur un scan donné, on retombe automatiquement sur le
top 5 des scores entre CATEGORY10_FALLBACK_MIN_SCORE (40 par défaut) et
GSB_MIN_SCORE, marqués `is_fallback=True` (affichés distinctement côté
frontend) plutôt que de ne rien montrer.

⚠️ Approximations documentées (voir indicators.py pour le détail) :
  - CVD (Cumulative Volume Delta) : approximation candle-based
    (volume * signe(close-open)), PAS un vrai CVD tick-by-tick.
  - Zones de liquidité / "poches de liquidités" : proxy via Volume Profile
    (POC/VAH/VAL) calculé sur les bougies, pas un vrai carnet d'ordres agrégé
    multi-niveaux historique.
  - Zones de liquidation : même estimation heuristique (funding + levier)
    que les Catégories 5/7/8/9, PAS un flux de liquidations réel.
  - Intégration Bybit non testée en conditions réelles (voir bybit_client.py).
"""
import asyncio
import logging

import numpy as np
import pandas as pd

from .config import settings
from .binance_client import BinanceFuturesClient
from .bybit_client import BybitClient
from .indicators import (
    enrich_dataframe, garman_klass_volatility, squeeze_score, cvd_estimate,
    vwap, volume_profile, beta_vs_reference, liquidation_zones_multi, closest_liquidation_zone,
)
from .models import Category10Signal, LiquidationZone

logger = logging.getLogger("category10_scanner")


def _clip01(x: float) -> float:
    return float(max(0.0, min(1.0, x)))


def _klines_to_df(raw: list[list]) -> pd.DataFrame:
    """Format commun (après normalisation par les clients respectifs) :
    [timestamp, open, high, low, close, volume, ...]."""
    df = pd.DataFrame([row[:6] for row in raw], columns=["ts", "open", "high", "low", "close", "volume"])
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    return df


async def _fetch_bundle(exchange: str, client, symbol: str) -> dict | None:
    try:
        h1_raw, h4_raw, d1_raw = await asyncio.gather(
            client.get_klines(symbol, "1h", limit=150),
            client.get_klines(symbol, "4h", limit=100),
            client.get_klines(symbol, "1d", limit=10),
        )
        if exchange == "Binance":
            funding = await client.get_funding_rate(symbol)
            oi_usd = await client.get_open_interest(symbol)
            oi_change = await client.get_open_interest_change_pct(symbol, hours=4)  # cohérent avec price_change_4h dans _score_oifd
            imbalance = await client.get_orderbook_imbalance(symbol)
            spread = await client.get_spread_pct(symbol)
        else:  # Bybit
            funding = await client.get_funding_rate(symbol)
            oi_usd, oi_change = await client.get_open_interest_change_pct(symbol, hours=4)
            imbalance = await client.get_orderbook_imbalance(symbol)
            spread = await client.get_spread_pct(symbol)
    except Exception as e:
        logger.warning(f"{exchange} {symbol}: erreur de récupération ignorée -> {e}")
        return None

    if len(h1_raw) < 60 or len(h4_raw) < 30 or len(d1_raw) < 7:
        return None

    df_h1 = enrich_dataframe(_klines_to_df(h1_raw), settings)
    df_h4 = enrich_dataframe(_klines_to_df(h4_raw), settings)
    df_d1 = _klines_to_df(d1_raw)

    return {
        "exchange": exchange, "symbol": symbol, "h1": df_h1, "h4": df_h4, "d1": df_d1,
        "funding": funding, "oi_usd": oi_usd, "oi_change_pct": oi_change,
        "imbalance": imbalance, "spread_pct": spread,
    }


def _score_vsi(bundle: dict) -> float:
    """Volatility Squeeze Index : compression BB/Keltner + ATR normalisé +
    volatilité Garman-Klass + phase d'énergie potentielle (vol faible + volume qui monte)."""
    h1, h4 = bundle["h1"], bundle["h4"]

    squeeze_h1 = squeeze_score(h1)
    squeeze_h4 = squeeze_score(h4)
    squeeze_component = 0.6 * squeeze_h1 + 0.4 * squeeze_h4

    atr_history = h1["atr_pct"].tail(60).dropna()
    atr_compression = 0.5
    if len(atr_history) >= 20:
        current_atr = atr_history.iloc[-1]
        percentile = (atr_history < current_atr).mean()
        atr_compression = 1 - percentile  # faible ATR récent vs historique = score élevé

    gk_vol = garman_klass_volatility(h1, period=24)
    # Une volatilité GK faible en absolu (< 1.5%) renforce l'idée de compression
    gk_component = _clip01(1 - gk_vol / 3)

    # "Énergie potentielle" : compression + volume qui recommence à monter
    vol_sma5 = h1["volume"].tail(5).mean()
    vol_sma20 = h1["volume"].tail(20).mean()
    energy_component = _clip01((vol_sma5 / vol_sma20 - 1)) if vol_sma20 else 0.0

    vsi = 0.4 * squeeze_component + 0.25 * atr_compression + 0.2 * gk_component + 0.15 * energy_component
    return round(_clip01(vsi) * 100, 2)


def _score_rvol(bundle: dict) -> float:
    """Relative Volume & Flow Imbalance : ratio de volume + CVD estimé + déséquilibre du carnet."""
    h1 = bundle["h1"]
    last = h1.iloc[-1]
    vol_ratio = float(last["vol_ratio"]) if not pd.isna(last["vol_ratio"]) else 1.0
    rvol_component = _clip01(vol_ratio / 2.5)  # seuil 2.5 = score max selon le cahier des charges

    cvd = cvd_estimate(h1, period=20)  # dans [-1, 1]
    cvd_component = _clip01((cvd + 1) / 2)  # recentré sur 0-1, 0.5 = neutre

    imbalance = bundle.get("imbalance")
    imbalance_component = _clip01(imbalance) if imbalance is not None else 0.5

    rvol = 0.5 * rvol_component + 0.3 * cvd_component + 0.2 * imbalance_component
    return round(_clip01(rvol) * 100, 2)


def _score_oifd(bundle: dict) -> float:
    """Open Interest & Funding Disparity : croissance d'OI disproportionnée par
    rapport au mouvement de prix (accumulation de levier) + funding extrême."""
    h4 = bundle["h4"]
    oi_change = bundle.get("oi_change_pct")
    price_change_4h_pct = float((h4["close"].iloc[-1] / h4["close"].iloc[-2] - 1) * 100) if len(h4) >= 2 else 0.0

    if oi_change is not None:
        disparity = abs(oi_change) / (abs(price_change_4h_pct) + 1.0)  # +1 pour éviter division par ~0
        disparity_component = _clip01(disparity / 10)
    else:
        disparity_component = 0.3  # OI change indisponible sur certains exchanges -> score neutre-bas

    funding = bundle.get("funding") or 0.0
    funding_component = _clip01(abs(funding) / settings.GSB_FUNDING_EXTREME_PCT)

    oifd = 0.55 * disparity_component + 0.45 * funding_component
    return round(_clip01(oifd) * 100, 2)


def _score_msd(bundle: dict) -> tuple[float, str, float]:
    """Market Structure & Key Level Distance : proximité au niveau clé le plus
    proche parmi VWAP, plus haut/bas 7j, POC/VAH/VAL. Retourne (score, libellé, distance%)."""
    h1, h4, d1 = bundle["h1"], bundle["h4"], bundle["d1"]
    price = float(h1["close"].iloc[-1])

    levels = {
        "VWAP (24 dernières H1)": vwap(h1, period=24),
        "Plus haut 7j": float(d1["high"].tail(7).max()),
        "Plus bas 7j": float(d1["low"].tail(7).min()),
    }
    vp = volume_profile(h4, period=100, bins=24)
    levels["POC (Volume Profile)"] = vp["poc"]
    levels["VAH (Volume Profile)"] = vp["vah"]
    levels["VAL (Volume Profile)"] = vp["val"]

    closest_label, closest_dist = None, float("inf")
    for label, level_price in levels.items():
        if level_price <= 0:
            continue
        dist_pct = abs(price - level_price) / price * 100
        if dist_pct < closest_dist:
            closest_dist, closest_label = dist_pct, label

    # <= 1.5% de distance = score max (seuil du cahier des charges), décroît ensuite
    score = _clip01(1 - closest_dist / 5) if closest_dist != float("inf") else 0.3
    return round(score * 100, 2), closest_label, (round(closest_dist, 2) if closest_dist != float("inf") else None)


def _score_corr(bundle: dict, btc_h1_returns: pd.Series) -> tuple[float, float]:
    """BTC Beta & Correlation Factor : bêta vs BTC + divergence (surperformance
    ou déconnexion) par rapport au mouvement attendu selon ce bêta."""
    h1 = bundle["h1"]
    returns = h1["close"].pct_change().dropna()
    beta = beta_vs_reference(returns, btc_h1_returns)

    n = min(len(returns), len(btc_h1_returns), 24)
    if n < 5:
        return 30.0, beta
    symbol_return_24h = float(h1["close"].iloc[-1] / h1["close"].iloc[-1 - n] - 1)
    btc_return_24h = float(btc_h1_returns.tail(n).add(1).prod() - 1)
    expected_return = beta * btc_return_24h
    divergence = abs(symbol_return_24h - expected_return)

    score = round(_clip01(divergence / 0.08) * 100, 2)  # 8% de divergence = score max
    return score, round(beta, 2)


def _liquidation_zones(price: float) -> list[dict]:
    """Zones de liquidation ESTIMÉES sur les niveaux de levier configurés
    (10x/25x/50x/100x), même méthode heuristique que les Catégories 5/7/8/9."""
    return liquidation_zones_multi(price, settings.LIQUIDATION_LEVERAGE_LEVELS)


def _build_trade_plan(price: float, atr_val: float, direction: str):
    """Plan de trade pour la Cat.10 : SL à 1.5×ATR (cohérent avec les autres
    catégories), TP à GSB_TP_ATR_MULTIPLIER×ATR (3×ATR par défaut) — proportionnel
    à la volatilité réelle de l'actif plutôt qu'un objectif en % fixe, qui serait
    disproportionné sur un actif peu volatil et trop conservateur sur un actif
    très volatil. Le TP reste borné par MIN_RR (setups < R:R minimum exclus)."""
    tp_distance = settings.GSB_TP_ATR_MULTIPLIER * atr_val
    if direction == "Long":
        entry = price
        sl = entry - 1.5 * atr_val
        risk = entry - sl
        tp = entry + max(tp_distance, settings.MIN_RR * risk)
    else:
        entry = price
        sl = entry + 1.5 * atr_val
        risk = sl - entry
        tp = entry - max(tp_distance, settings.MIN_RR * risk)
    rr = abs(tp - entry) / abs(entry - sl) if entry != sl else 0
    return round(entry, 8), round(sl, 8), round(tp, 8), round(rr, 2)


async def _scan_exchange_for_gsb(exchange: str, client, btc_h1_returns: pd.Series | None) -> list[Category10Signal]:
    try:
        symbols = await client.get_top_symbols_by_volume(
            settings.CATEGORY10_TOP_N_SYMBOLS, settings.CATEGORY10_MIN_QUOTE_VOLUME
        )
    except Exception as e:
        logger.warning(f"{exchange}: impossible de lister les symboles pour la Cat.10 -> {e}")
        return []

    sem = asyncio.Semaphore(settings.MAX_CONCURRENT_REQUESTS)

    async def fetch(sym):
        async with sem:
            return await _fetch_bundle(exchange, client, sym)

    bundles = [b for b in await asyncio.gather(*(fetch(s) for s in symbols)) if b is not None]

    if btc_h1_returns is None:
        btc_bundle = next((b for b in bundles if b["symbol"] == "BTCUSDT"), None)
        btc_h1_returns = btc_bundle["h1"]["close"].pct_change().dropna() if btc_bundle else pd.Series(dtype=float)

    signals = []
    all_scores = []  # (symbol, gsb) pour TOUS les candidats évalués, même filtrés -> diagnostic
    for b in bundles:
        h1 = b["h1"]
        last = h1.iloc[-1]
        if pd.isna(last[["atr", "vol_ratio", "bb_width"]]).any():
            continue

        vsi = _score_vsi(b)
        rvol = _score_rvol(b)
        oifd = _score_oifd(b)
        msd, msd_label, msd_dist = _score_msd(b)
        corr, beta = _score_corr(b, btc_h1_returns)

        gsb = round(
            settings.GSB_WEIGHT_VSI * vsi + settings.GSB_WEIGHT_RVOL * rvol
            + settings.GSB_WEIGHT_OIFD * oifd + settings.GSB_WEIGHT_MSD * msd
            + settings.GSB_WEIGHT_CORR * corr,
            2,
        )
        all_scores.append((b["symbol"], gsb, vsi, rvol, oifd, msd, corr))
        if gsb < settings.CATEGORY10_FALLBACK_MIN_SCORE:
            continue  # trop loin même du seuil de repli -> pas la peine de construire un plan de trade

        price = float(last["close"])
        atr_val = float(last["atr"])
        # Direction : sens du momentum MACD récent (le squeeze indique QU'un mouvement
        # arrive, le MACD histogram indique dans quel sens il est le plus probable)
        direction = "Long" if last["macd_hist"] > 0 else "Short"
        entry, sl, tp, rr = _build_trade_plan(price, atr_val, direction)
        if rr < settings.MIN_RR:
            continue

        zones = _liquidation_zones(price)
        side = "long" if direction == "Long" else "short"
        closest = closest_liquidation_zone(zones, price, side)
        liq_long = closest["long_price"]
        liq_short = closest["short_price"]
        reason = (
            f"GSB {gsb}/100 -> VSI {vsi} (squeeze+compression), RVOL {rvol} (volume/flux), "
            f"OIFD {oifd} (OI/funding), MSD {msd} (proche {msd_label}, {msd_dist}%), "
            f"CORR {corr} (bêta BTC {beta})"
        )

        signals.append(
            Category10Signal(
                exchange=exchange, symbol=b["symbol"], gsb_score=gsb,
                vsi_score=vsi, rvol_score=rvol, oifd_score=oifd, msd_score=msd, corr_score=corr,
                direction=direction, trigger_reason=reason, entry=entry, take_profit=tp, stop_loss=sl,
                risk_reward=rr, price=price, atr_pct=round(float(last["atr_pct"]), 3),
                volume_trend_pct=round((float(last["vol_ratio"]) - 1) * 100, 1),
                open_interest_usd=b.get("oi_usd"), oi_change_pct=b.get("oi_change_pct"),
                funding_rate=b.get("funding"), spread_pct=b.get("spread_pct"),
                orderbook_imbalance=b.get("imbalance"), beta_btc=beta,
                key_level_label=msd_label, key_level_distance_pct=msd_dist,
                liquidation_long=liq_long, liquidation_short=liq_short,
                liquidation_zones=[LiquidationZone(**z) for z in zones],
                is_fallback=gsb < settings.GSB_MIN_SCORE,
                sparkline=[round(v, 8) for v in h1["close"].tail(24).tolist()],
            )
        )

    if all_scores:
        top10 = sorted(all_scores, key=lambda x: x[1], reverse=True)[:10]
        best = top10[0]
        qualified_count = sum(1 for s in signals if not s.is_fallback)
        logger.info(
            f"{exchange} Cat.10 (GSB) : {len(bundles)} paires évaluées, {len(symbols) - len(bundles)} "
            f"écartées (données insuffisantes/erreur fetch), {qualified_count} au-dessus du seuil "
            f"({settings.GSB_MIN_SCORE}), {len(signals) - qualified_count} en repli "
            f"({settings.CATEGORY10_FALLBACK_MIN_SCORE}-{settings.GSB_MIN_SCORE}). Meilleur score: {best[0]} GSB={best[1]} "
            f"(VSI={best[2]} RVOL={best[3]} OIFD={best[4]} MSD={best[5]} CORR={best[6]}). "
            f"Top 10 (symbole:GSB): {', '.join(f'{s}:{g}' for s, g, *_ in top10)}"
        )
    else:
        logger.warning(
            f"{exchange} Cat.10 (GSB) : aucune paire exploitable sur {len(symbols)} candidates "
            f"({len(bundles)} bundles récupérés) -> vérifier les erreurs de fetch (klines/funding/OI) "
            f"ci-dessus dans les logs, ou CATEGORY10_MIN_QUOTE_VOLUME trop restrictif."
        )

    return signals


async def build_category10() -> tuple[list[Category10Signal], list[str]]:
    """Point d'entrée appelé par scanner.run_scan(). Retourne le top 5 GSB
    TOUTES EXCHANGES CONFONDUES (Binance + Bybit), filtré à GSB >= seuil."""
    errors: list[str] = []
    all_signals: list[Category10Signal] = []

    async with BinanceFuturesClient() as binance_client:
        try:
            binance_signals = await _scan_exchange_for_gsb("Binance", binance_client, None)
            all_signals += binance_signals
        except Exception as e:
            logger.exception("Échec complet du scan Binance pour la Catégorie 10")
            errors.append(f"Catégorie 10 (Binance): {e}")

    if settings.BYBIT_ENABLED:
        async with BybitClient() as bybit_client:
            try:
                bybit_btc = await _fetch_bundle("Bybit", bybit_client, "BTCUSDT")
                btc_returns = bybit_btc["h1"]["close"].pct_change().dropna() if bybit_btc else pd.Series(dtype=float)
                bybit_signals = await _scan_exchange_for_gsb("Bybit", bybit_client, btc_returns)
                all_signals += bybit_signals
            except Exception as e:
                logger.exception("Échec complet du scan Bybit pour la Catégorie 10")
                errors.append(f"Catégorie 10 (Bybit): {e}")

    qualified = [s for s in all_signals if not s.is_fallback]
    if qualified:
        top5 = sorted(qualified, key=lambda s: s.gsb_score, reverse=True)[:5]
    else:
        fallback = [s for s in all_signals if s.is_fallback]
        top5 = sorted(fallback, key=lambda s: s.gsb_score, reverse=True)[:5]
        if top5:
            logger.warning(
                f"Cat.10 : aucune paire n'a atteint GSB >= {settings.GSB_MIN_SCORE} sur ce scan -> "
                f"repli sur le top {len(top5)} des scores {settings.CATEGORY10_FALLBACK_MIN_SCORE}-"
                f"{settings.GSB_MIN_SCORE} : {', '.join(f'{s.symbol}({s.gsb_score})' for s in top5)}"
            )
    return top5, errors
