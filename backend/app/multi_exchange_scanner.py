"""
Catégories 7 et 9 — analyse multi-exchange, positionnées respectivement dans les
onglets "Signaux Techniques" (Cat.7) et "Top Movers" (Cat.9).

  - Catégorie 7 (mouvements imminents, fenêtre 4h) : Bybit + OKX
  - Catégorie 9 (Stratégie Fib) : Binance + Bybit

⚠️ Voir les docstrings de okx_client.py et bybit_client.py : ces intégrations
suivent la documentation publique officielle mais n'ont pas pu être testées en
conditions réelles depuis l'environnement de développement (pas d'accès
internet). Un bug de parsing OKX (nombre de colonnes des bougies) a déjà été
trouvé et corrigé en production — testez sérieusement chaque nouveau déploiement.

Architecture : REST interrogé à chaque scan (pas de WebSocket persistant),
cohérent avec le fonctionnement de l'app par scans programmés + monitoring
périodique plutôt qu'un flux continu. Voir README pour une piste d'évolution
vers du WebSocket si un jour nécessaire.
"""
import asyncio
import logging

import numpy as np
import pandas as pd

from .config import settings
from .binance_client import BinanceFuturesClient
from .bybit_client import BybitClient
from .okx_client import OKXClient
# Hyperliquid n'est plus utilisée par aucune catégorie active (voir docstring
# de build_categories_7_9 ci-dessous) -> import retiré. Le client reste
# disponible dans hyperliquid_client.py si besoin futur.
from .indicators import (
    enrich_dataframe, rsi as compute_rsi, fibonacci_zone, volume_trend_pct,
    liquidation_zones_multi, closest_liquidation_zone,
    swing_points, detect_liquidity_sweep, volume_profile, vwap_bands,
    cvd_series, footprint_pressure,
)
from .models import MultiExchangeSignal, Category9Result, LiquidationZone

logger = logging.getLogger("multi_exchange_scanner")


def _klines_to_df(raw: list[list]) -> pd.DataFrame:
    """Format commun OKX/Hyperliquid après normalisation dans les clients respectifs :
    [timestamp, open, high, low, close, volume, ...]. On ne garde explicitement
    que les 6 premiers champs (filet de sécurité) : un exchange peut renvoyer
    des colonnes supplémentaires (ex: OKX en renvoie 9, Bybit 7) — un bug de ce
    type est déjà survenu une fois avec OKX en production, d'où cette protection
    même si les clients sont censés déjà normaliser à 6 colonnes en amont."""
    df = pd.DataFrame([row[:6] for row in raw], columns=["ts", "open", "high", "low", "close", "volume"])
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    return df


def _clip01(x: float) -> float:
    return float(max(0.0, min(1.0, x)))


async def _fetch_symbol_bundle(exchange: str, client, symbol: str) -> dict | None:
    """Récupère H1/H4/D1 + funding + OI + spread pour un symbole, sur l'exchange donné."""
    try:
        h1_raw, h4_raw, d1_raw = await asyncio.gather(
            client.get_klines(symbol, "1h", limit=200),
            client.get_klines(symbol, "4h", limit=120),
            client.get_klines(symbol, "1d", limit=100),
        )
        if exchange == "OKX":
            funding = await client.get_funding_rate(symbol)
            oi_usd, oi_change = await client.get_open_interest(symbol)
        elif exchange == "Binance":
            funding = await client.get_funding_rate(symbol)
            oi_usd = await client.get_open_interest(symbol)
            oi_change = await client.get_open_interest_change_24h_pct(symbol)
        elif exchange == "Bybit":
            funding = await client.get_funding_rate(symbol)
            oi_usd, oi_change = await client.get_open_interest_change_pct(symbol, hours=4)
        else:  # Hyperliquid
            funding, oi_usd = await client.get_funding_and_oi(symbol)
            oi_change = None
        spread = await client.get_spread_pct(symbol)
    except Exception as e:
        logger.warning(f"{exchange} {symbol}: erreur de récupération ignorée -> {e}")
        return None

    if len(h1_raw) < 60 or len(h4_raw) < 30 or len(d1_raw) < 20:
        return None

    df_h1 = enrich_dataframe(_klines_to_df(h1_raw), settings)
    df_h4 = enrich_dataframe(_klines_to_df(h4_raw), settings)
    df_d1 = _klines_to_df(d1_raw)
    df_d1["rsi"] = compute_rsi(df_d1["close"], settings.RSI_PERIOD)

    return {
        "exchange": exchange, "symbol": symbol,
        "h1": df_h1, "h4": df_h4, "d1": df_d1,
        "funding": funding, "oi_usd": oi_usd, "oi_change_pct": oi_change, "spread_pct": spread,
    }


def _liquidation_estimate(price: float, direction: str) -> tuple[float, float, list[dict]]:
    """⚠️ Estimation heuristique (même méthode que la Catégorie 5), sur les niveaux
    de levier configurés (10x/25x/50x/100x) : PAS un flux de liquidations réel.
    Retourne (long_price, short_price) du niveau le plus proche du prix dans le
    sens du signal, ainsi que le détail complet des zones par niveau."""
    zones = liquidation_zones_multi(price, settings.LIQUIDATION_LEVERAGE_LEVELS)
    side = "long" if direction == "Long" else "short"
    closest = closest_liquidation_zone(zones, price, side)
    return closest["long_price"], closest["short_price"], zones


def _trade_plan(price: float, atr_val: float, direction: str, target_pct: float = 0.05) -> tuple[float, float, float, float]:
    """Plan de trade générique (entrée au marché, SL basé ATR, TP visant le
    meilleur de 2x le risque ou target_pct)."""
    if direction == "Long":
        entry = price
        sl = entry - 1.5 * atr_val
        risk = entry - sl
        tp = entry + max(2 * risk, entry * target_pct)
    else:
        entry = price
        sl = entry + 1.5 * atr_val
        risk = sl - entry
        tp = entry - max(2 * risk, entry * target_pct)
    rr = abs(tp - entry) / abs(entry - sl) if entry != sl else 0
    return round(entry, 8), round(sl, 8), round(tp, 8), round(rr, 2)


# --------------------------------------------------------------------------
# Catégorie 7 : Mouvements imminents à haute probabilité (4h)
# --------------------------------------------------------------------------
def _score_category7(bundle: dict) -> tuple[float, str] | None:
    h1 = bundle["h1"]
    last = h1.iloc[-1]
    if pd.isna(last[["atr", "bb_width", "vol_ratio"]]).any():
        return None

    atr_score = _clip01(last["atr_pct"] / 5 * 0.6)
    vol_ratio = float(last["vol_ratio"]) if not np.isnan(last["vol_ratio"]) else 1.0
    volume_score = _clip01((vol_ratio - 1) / 3)

    bb_recent = h1["bb_width"].tail(30)
    squeeze_score = 0.3
    if bb_recent.notna().sum() >= 10:
        was_squeezed = bb_recent.iloc[:-3].min() <= bb_recent.quantile(0.25)
        expanding_now = last["bb_width"] > bb_recent.iloc[-4]
        squeeze_score = 1.0 if (was_squeezed and expanding_now) else 0.3

    oi_score = _clip01(abs(bundle.get("oi_change_pct") or 0) / 20)

    score = 0.3 * atr_score + 0.3 * volume_score + 0.25 * squeeze_score + 0.15 * oi_score
    reason = (
        f"Compression de volatilité + volume x{vol_ratio:.1f} vs moyenne (fenêtre 4h)"
        + (f", OI {bundle['oi_change_pct']:+.1f}%" if bundle.get("oi_change_pct") is not None else "")
    )
    return round(score * 100, 2), reason


def _build_category7_for_exchange(bundles: list[dict]) -> list[MultiExchangeSignal]:
    candidates = []
    for b in bundles:
        result = _score_category7(b)
        if result is None:
            continue
        score, reason = result
        h1 = b["h1"]
        last = h1.iloc[-1]
        price = float(last["close"])
        direction = "Long" if last["macd_hist"] > 0 else "Short"
        # Fenêtre 4h -> cible de mouvement plus resserrée qu'un plan 24h (2.5% au
        # lieu de 5%), cohérent avec un horizon 6x plus court.
        entry, sl, tp, rr = _trade_plan(price, float(last["atr"]), direction, target_pct=0.025)
        if rr < settings.MIN_RR:
            continue
        liq_long, liq_short, liq_zones = _liquidation_estimate(price, direction)
        candidates.append(
            MultiExchangeSignal(
                exchange=b["exchange"], symbol=b["symbol"], score=score, direction=direction,
                trigger_reason=reason, entry=entry, take_profit=tp, stop_loss=sl, risk_reward=rr,
                price=price, volume_trend_pct=volume_trend_pct(h1["volume"]),
                open_interest_usd=b.get("oi_usd"), oi_change_24h_pct=b.get("oi_change_pct"),
                spread_pct=b.get("spread_pct"), liquidation_long=liq_long, liquidation_short=liq_short,
                liquidation_zones=[LiquidationZone(**z) for z in liq_zones],
                sparkline=[round(v, 8) for v in h1["close"].tail(24).tolist()],
            )
        )
    return sorted(candidates, key=lambda c: c.score, reverse=True)[:5]


# --------------------------------------------------------------------------
# Catégorie 9 : Stratégie Fib (Fibonacci + Volume Profile + VWAP + Market Structure
# + Liquidity Sweep + Delta/CVD + Footprint)
# --------------------------------------------------------------------------
def _fib_market_structure_score(h1: pd.DataFrame, direction: str) -> float:
    """20 pts : confirmation de retournement de structure (type CHoCH) sur H1, dans le
    sens du rebond anticipé par le Fibo -> plein crédit si le swing structurel le plus
    récent est déjà cassé, dégressif selon la distance sinon."""
    swing_highs, swing_lows = swing_points(h1, lookback=settings.FIB_SWING_LOOKBACK)
    last_close = float(h1["close"].iloc[-1])
    n = len(h1)
    if direction == "Long":
        candidates = [p for i, p in swing_highs if i < n - 1]
        if not candidates:
            return 10.0  # historique insuffisant -> score neutre
        recent_high = candidates[-1]
        if last_close > recent_high:
            return 20.0
        distance_pct = abs(recent_high - last_close) / last_close * 100
        return 20.0 * _clip01(1 - distance_pct / 3)
    candidates = [p for i, p in swing_lows if i < n - 1]
    if not candidates:
        return 10.0
    recent_low = candidates[-1]
    if last_close < recent_low:
        return 20.0
    distance_pct = abs(last_close - recent_low) / last_close * 100
    return 20.0 * _clip01(1 - distance_pct / 3)


def _fib_sweep_score(h1: pd.DataFrame, direction: str) -> float:
    """15 pts : le swing ayant ancré le Fibo (ou un swing proche) a-t-il été balayé
    récemment (mèche au-delà, clôture qui réintègre) -> confirme une vraie prise de
    liquidité plutôt qu'un simple passage de prix. Tout ou rien (pas de crédit
    partiel : soit un sweep valide existe, soit non)."""
    swing_highs, swing_lows = swing_points(h1, lookback=settings.FIB_SWING_LOOKBACK)
    sweep = detect_liquidity_sweep(h1, swing_highs, swing_lows, recent_window=settings.FIB_SWEEP_WINDOW)
    return 15.0 if (sweep is not None and sweep["direction"] == direction) else 0.0


def _fib_vp_score(h4: pd.DataFrame, level_price: float, price: float) -> float:
    """20 pts : proximité du niveau Fibo actif à un niveau clé de Volume Profile
    (POC/VAH/VAL, calculé sur les bougies H4) -> confluence structurelle."""
    vp = volume_profile(h4, period=min(len(h4), 100), bins=24)
    closest = min((vp["poc"], vp["vah"], vp["val"]), key=lambda lv: abs(level_price - lv))
    distance_pct = abs(level_price - closest) / price * 100
    return 20.0 * _clip01(1 - distance_pct / (2 * settings.FIB_VP_PROXIMITY_PCT))


def _fib_vwap_score(h1: pd.DataFrame, price: float) -> float:
    """15 pts : proximité du prix au VWAP (bandes ±1 écart-type, H1) -> confluence de
    zone de valeur institutionnelle, quelle que soit la direction du setup."""
    vwb = vwap_bands(h1, period=100, band_mult=1.0)
    band_half_width_pct = max(abs(vwb["upper"] - vwb["lower"]) / price * 100 / 2, 0.2)
    vwap_distance_pct = abs(price - vwb["vwap"]) / price * 100
    return 15.0 * _clip01(1 - vwap_distance_pct / band_half_width_pct)


def _fib_cvd_score(h1: pd.DataFrame, direction: str) -> float:
    """15 pts : la tendance du CVD (Delta cumulé, voir indicators.cvd_series) sur les
    FIB_CVD_LOOKBACK dernières bougies H1 confirme la direction anticipée -> CVD
    montant pour un Long (accumulation), descendant pour un Short (distribution)."""
    cvd = cvd_series(h1, period=settings.FIB_CVD_LOOKBACK)
    if len(cvd) < 5:
        return 7.5  # historique insuffisant -> score neutre
    slope = float(cvd.iloc[-1] - cvd.iloc[0])
    if direction == "Long":
        return 15.0 if slope > 0 else 0.0
    return 15.0 if slope < 0 else 0.0


def _fib_footprint_score(h1: pd.DataFrame, direction: str) -> tuple[float, dict]:
    """15 pts : pression acheteuse/vendeuse (proxy footprint, voir
    indicators.footprint_pressure) concentrée du bon côté sur les
    FIB_FOOTPRINT_CANDLES dernières bougies, +3 pts bonus si absorption détectée."""
    fp = footprint_pressure(h1, n_candles=settings.FIB_FOOTPRINT_CANDLES)
    pressure = fp["buy_pressure"] if direction == "Long" else 1 - fp["buy_pressure"]
    score = 15.0 * _clip01((pressure - 0.4) / 0.4)
    if fp["absorption"]:
        score = min(15.0, score + 3.0)
    return score, fp


def _build_category9_for_exchange(bundles: list[dict]) -> Category9Result:
    """Catégorie 9 — Stratégie Fib : Fibonacci (identité/filtre bloquant de la
    stratégie) + Market Structure + Liquidity Sweep + Volume Profile + VWAP +
    Delta/CVD + Footprint (proxy), combinés en un score pondéré sur 100 pts avec
    seuil minimum FIB_MIN_SCORE — même architecture que les Stratégies 1/2 de la
    Catégorie 6 (voir strategie1_scanner.py / strategie2_scanner.py).

    ⚠️ Delta/CVD et Footprint sont des approximations à partir des bougies OHLCV
    (candle-based), PAS un vrai order flow tick-by-tick (qui nécessiterait les
    trades exécutés individuellement avec leur sens acheteur/vendeur, via
    aggTrades) — impraticable ici pour ~50-155 paires x 4 exchanges sans exploser
    les limites API. Voir indicators.py pour le détail des méthodes."""
    retracement_050, golden_pocket = [], []
    for b in bundles:
        h1, h4 = b["h1"], b["h4"]
        zone = fibonacci_zone(h4, settings.FIBO_LOOKBACK_CANDLES, settings.FIBO_TOLERANCE_PCT)
        if zone is None:
            continue

        last = h4.iloc[-1]
        price = float(last["close"])
        atr_val = float(last["atr"]) if not np.isnan(last["atr"]) else price * 0.02
        direction = zone["direction"]

        # --- Scoring pondéré (100 pts) ---
        ms_score = _fib_market_structure_score(h1, direction)
        sweep_score = _fib_sweep_score(h1, direction)
        vp_score = _fib_vp_score(h4, zone["level_price"], price)
        vwap_score = _fib_vwap_score(h1, price)
        cvd_score = _fib_cvd_score(h1, direction)
        footprint_score, fp = _fib_footprint_score(h1, direction)

        total_score = round(ms_score + sweep_score + vp_score + vwap_score + cvd_score + footprint_score, 2)
        if total_score < settings.FIB_FALLBACK_MIN_SCORE:
            continue  # trop loin même du seuil de repli -> pas la peine de construire un plan de trade

        # SL au-delà du swing (extrémité de l'impulsion), TP = retour vers l'autre borne du swing
        if direction == "Long":
            entry = price
            sl = min(zone["swing_low"], entry - 1.2 * atr_val)
            tp = zone["swing_high"]
        else:
            entry = price
            sl = max(zone["swing_high"], entry + 1.2 * atr_val)
            tp = zone["swing_low"]
        risk = abs(entry - sl)
        rr = round(abs(tp - entry) / risk, 2) if risk else 0
        if rr < settings.FIB_MIN_RR:
            continue

        liq_long, liq_short, liq_zones = _liquidation_estimate(price, direction)
        reason = (
            f"Prix en zone de {zone['zone_label']} (impulsion {'haussière' if direction == 'Long' else 'baissière'} "
            f"de {zone['swing_low']:.6g} à {zone['swing_high']:.6g} sur H4) | "
            f"Structure {'cassée' if ms_score >= 15 else 'en formation'} + "
            f"{'Sweep confirmé' if sweep_score > 0 else 'pas de sweep récent'} + "
            f"VP {vp_score:.0f}/20 + VWAP {vwap_score:.0f}/15 + "
            f"CVD {'favorable' if cvd_score > 0 else 'défavorable/neutre'} + "
            f"Footprint {'(absorption) ' if fp['absorption'] else ''}pression acheteuse {fp['buy_pressure']*100:.0f}% | "
            f"Score: {total_score}/100 (Structure:{ms_score:.0f} Sweep:{sweep_score:.0f} VP:{vp_score:.0f} "
            f"VWAP:{vwap_score:.0f} CVD:{cvd_score:.0f} Footprint:{footprint_score:.0f})"
        )
        signal = MultiExchangeSignal(
            exchange=b["exchange"], symbol=b["symbol"],
            score=total_score,
            direction=direction,
            trigger_reason=reason,
            entry=round(entry, 8), take_profit=round(tp, 8), stop_loss=round(sl, 8), risk_reward=rr,
            price=price, volume_trend_pct=volume_trend_pct(h4["volume"]),
            open_interest_usd=b.get("oi_usd"), oi_change_24h_pct=b.get("oi_change_pct"),
            spread_pct=b.get("spread_pct"), liquidation_long=liq_long, liquidation_short=liq_short,
            liquidation_zones=[LiquidationZone(**z) for z in liq_zones],
            sparkline=[round(v, 8) for v in h4["close"].tail(24).tolist()],
            fib_level_label=zone["zone_label"], fib_sub_category=zone["sub_category"],
            is_fallback=total_score < settings.FIB_MIN_SCORE,
        )
        if zone["sub_category"] == "retracement_050":
            retracement_050.append(signal)
        else:
            golden_pocket.append(signal)

    def _select_top5(candidates: list) -> list:
        """Top 5 des qualifiés (score >= FIB_MIN_SCORE) si au moins un existe, sinon
        repli sur le top 5 des scores 40-65 (déjà filtrés en amont), marqué
        is_fallback=True -> mieux qu'une liste vide, tout en distinguant clairement
        les vrais setups qualifiés des simples "meilleurs scores disponibles"."""
        qualified = [c for c in candidates if not c.is_fallback]
        if qualified:
            return sorted(qualified, key=lambda c: c.score, reverse=True)[:5]
        fallback = sorted([c for c in candidates if c.is_fallback], key=lambda c: c.score, reverse=True)[:5]
        if fallback:
            logger.warning(
                f"Stratégie Fib : aucun candidat n'a atteint le score {settings.FIB_MIN_SCORE} -> "
                f"repli sur le top {len(fallback)} des scores {settings.FIB_FALLBACK_MIN_SCORE}-"
                f"{settings.FIB_MIN_SCORE} : {', '.join(f'{c.symbol}({c.score})' for c in fallback)}"
            )
        return fallback

    return Category9Result(
        retracement_050=_select_top5(retracement_050),
        golden_pocket=_select_top5(golden_pocket),
    )


# --------------------------------------------------------------------------
# Orchestration : une exchange à la fois, puis fusion des résultats
# --------------------------------------------------------------------------
async def _get_top_symbols(exchange_name: str, client) -> list[str]:
    """Dispatcher : BinanceFuturesClient.get_top_symbols_by_volume(n) n'a pas le
    même paramètre min_quote_volume que les autres clients (il utilise en
    interne settings.MIN_QUOTE_VOLUME_USDT)."""
    if exchange_name == "Binance":
        return await client.get_top_symbols_by_volume(settings.MULTI_EXCHANGE_TOP_N_SYMBOLS)
    return await client.get_top_symbols_by_volume(
        settings.MULTI_EXCHANGE_TOP_N_SYMBOLS, settings.MULTI_EXCHANGE_MIN_QUOTE_VOLUME
    )


async def _scan_exchange(exchange_name: str, client) -> tuple[list[dict], list[str]]:
    errors = []
    try:
        symbols = await _get_top_symbols(exchange_name, client)
    except Exception as e:
        errors.append(f"{exchange_name}: impossible de lister les symboles -> {e}")
        return [], errors

    sem = asyncio.Semaphore(settings.MAX_CONCURRENT_REQUESTS)

    async def fetch_with_sem(sym):
        async with sem:
            return await _fetch_symbol_bundle(exchange_name, client, sym)

    results = await asyncio.gather(*(fetch_with_sem(s) for s in symbols))
    bundles = [r for r in results if r is not None]
    return bundles, errors


async def build_categories_7_9() -> tuple[dict, dict, list[str]]:
    """Point d'entrée appelé par scanner.run_scan(). Retourne (category7,
    category9, errors), chacun structuré par exchange.

    Répartition des exchanges par catégorie :
      - Catégorie 7 (mouvements imminents 4h) : Bybit + OKX (Hyperliquid exclue
        de cette catégorie sur demande explicite, mais n'est plus utilisée par
        aucune catégorie active depuis la suppression de la Cat.8 et le
        recentrage de la Cat.9 sur Binance+Bybit)
      - Catégorie 9 (Stratégie Fib) : Binance + Bybit

    Les bundles de données sont récupérés UNE SEULE FOIS par exchange (pas
    une fois par catégorie) pour éviter de doubler la charge API."""
    category7: dict[str, list] = {}
    category9: dict[str, Category9Result] = {}
    all_errors: list[str] = []

    CAT7_EXCHANGES = {"OKX", "Bybit"}
    # Ordre d'affichage des sous-menus (onglets) de la Catégorie 7 côté frontend :
    # Bybit en premier, puis OKX. L'ordre des clés du dict est repris tel quel par
    # `Object.keys(...)` côté React (MultiExchangeMovers.jsx).
    CAT7_TAB_ORDER = ["Bybit", "OKX"]
    CAT9_EXCHANGES = {"Binance", "Bybit"}

    exchange_configs = []
    if settings.OKX_ENABLED:
        exchange_configs.append(("OKX", OKXClient))
    # Hyperliquid n'est plus utilisée par aucune catégorie active (Cat.8 supprimée,
    # Cat.9 recentrée sur Binance+Bybit) -> retirée de la boucle de scan pour ne
    # pas consommer de quota API inutilement. Le client reste disponible dans
    # hyperliquid_client.py si une future catégorie en a besoin.
    # Binance est toujours actif (exchange principal de l'app, cf. Catégories 1-6)
    exchange_configs.append(("Binance", BinanceFuturesClient))
    if settings.BYBIT_ENABLED:
        exchange_configs.append(("Bybit", BybitClient))

    for exchange_name, client_cls in exchange_configs:
        try:
            async with client_cls() as client:
                bundles, errors = await _scan_exchange(exchange_name, client)
                all_errors += errors
                if exchange_name in CAT7_EXCHANGES:
                    category7[exchange_name] = _build_category7_for_exchange(bundles)
                if exchange_name in CAT9_EXCHANGES:
                    category9[exchange_name] = _build_category9_for_exchange(bundles)
        except Exception as e:
            logger.exception(f"Échec complet du scan {exchange_name}")
            all_errors.append(f"{exchange_name}: échec complet du scan -> {e}")
            if exchange_name in CAT7_EXCHANGES:
                category7[exchange_name] = []
            if exchange_name in CAT9_EXCHANGES:
                category9[exchange_name] = Category9Result()

    # Réordonnance finale de la Cat.7 : Bybit d'abord, puis OKX (indépendant de l'ordre
    # de scan ci-dessus).
    category7 = {name: category7[name] for name in CAT7_TAB_ORDER if name in category7}

    return category7, category9, all_errors
