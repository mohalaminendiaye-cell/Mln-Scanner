"""
Calculs des indicateurs techniques.

Formules exactes utilisées :

1) True Range (TR) :
   TR = max(High - Low, |High - Close_prev|, |Low - Close_prev|)

2) ATR (Average True Range, lissage de Wilder) :
   ATR = EMA(TR, période, alpha = 1/période)

3) RSI (Wilder) :
   RS = moyenne_mobile(gains, période) / moyenne_mobile(pertes, période)
   RSI = 100 - 100 / (1 + RS)

4) MACD :
   MACD_line = EMA(close, 12) - EMA(close, 26)
   Signal    = EMA(MACD_line, 9)
   Histogram = MACD_line - Signal

5) Bandes de Bollinger :
   Middle = SMA(close, période)
   Upper  = Middle + k * stdev(close, période)
   Lower  = Middle - k * stdev(close, période)
   Width  = (Upper - Lower) / Middle   -> utilisé pour détecter un "squeeze"

6) Choppiness Index (CHOP) — formule officielle (E. W. Dreiss) :
   CHOP(n) = 100 * log10( Somme(TR, n) / (Plus_Haut(n) - Plus_Bas(n)) ) / log10(n)

   - Somme(TR, n)      : somme des True Range sur les n dernières bougies
   - Plus_Haut(n)      : plus haut sur n bougies
   - Plus_Bas(n)        : plus bas sur n bougies
   - CHOP > 61.8  -> marché très erratique / range (choppy)
   - CHOP < 38.2  -> marché fortement directionnel (trend)
   Le calcul est fait ici sur des bougies H4 comme demandé.

7) Retracement de Fibonacci (Catégorie 9) :
   Sur une fenêtre récente (impulsion majeure = plus haut et plus bas sur N
   bougies), on calcule les niveaux :
     - impulsion haussière (plus bas avant le plus haut) :
         niveau(r) = Plus_Haut - r * (Plus_Haut - Plus_Bas)
     - impulsion baissière (plus haut avant le plus bas) :
         niveau(r) = Plus_Bas + r * (Plus_Haut - Plus_Bas)
   avec r ∈ {0.5, 0.618, 0.786}. Le prix actuel est comparé à ces niveaux
   (tolérance ± FIBO_TOLERANCE_PCT) pour détecter un retracement 0.5 exact
   ou une zone "Golden Pocket" (0.618 à 0.786).
"""
import numpy as np
import pandas as pd


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = true_range(df)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100 - (100 / (1 + rs))
    return result.fillna(50)  # neutre si pas de données suffisantes


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def bollinger_bands(close: pd.Series, period: int = 20, std_mult: float = 2.0):
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    width = (upper - lower) / mid
    return upper, mid, lower, width


def choppiness_index(df: pd.DataFrame, period: int = 14) -> pd.Series:
    tr = true_range(df)
    tr_sum = tr.rolling(period).sum()
    high_max = df["high"].rolling(period).max()
    low_min = df["low"].rolling(period).min()
    denom = (high_max - low_min).replace(0, np.nan)
    chop = 100 * np.log10(tr_sum / denom) / np.log10(period)
    return chop.fillna(50)


def volume_sma(volume: pd.Series, period: int = 20) -> pd.Series:
    return volume.rolling(period).mean()


def stdev_pct(close: pd.Series, period: int = 20) -> pd.Series:
    """Écart-type des rendements en % — mesure de volatilité récente."""
    returns = close.pct_change()
    return returns.rolling(period).std() * 100


def enrich_dataframe(df: pd.DataFrame, cfg) -> pd.DataFrame:
    """Ajoute toutes les colonnes d'indicateurs nécessaires à un DataFrame OHLCV H1."""
    df = df.copy()
    df["atr"] = atr(df, cfg.ATR_PERIOD)
    df["atr_pct"] = df["atr"] / df["close"] * 100
    df["rsi"] = rsi(df["close"], cfg.RSI_PERIOD)
    macd_line, signal_line, hist = macd(df["close"])
    df["macd"] = macd_line
    df["macd_signal"] = signal_line
    df["macd_hist"] = hist
    upper, mid, lower, width = bollinger_bands(df["close"], cfg.BB_PERIOD, cfg.BB_STD)
    df["bb_upper"] = upper
    df["bb_mid"] = mid
    df["bb_lower"] = lower
    df["bb_width"] = width
    df["vol_sma"] = volume_sma(df["volume"], cfg.VOLUME_SMA_PERIOD)
    df["vol_ratio"] = df["volume"] / df["vol_sma"]
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["stdev_pct"] = stdev_pct(df["close"], 20)
    return df


def volume_trend_pct(volume: pd.Series, period: int = 20) -> float:
    """Variation du volume courant par rapport à sa moyenne mobile, en % —
    utilisé tel quel pour l'affichage "Tendance du Volume" des Catégories 7/8/9."""
    sma = volume.rolling(period).mean()
    if len(sma.dropna()) == 0 or sma.iloc[-1] == 0:
        return 0.0
    return round(float((volume.iloc[-1] / sma.iloc[-1] - 1) * 100), 1)


def fibonacci_zone(df: pd.DataFrame, lookback: int = 60, tolerance_pct: float = 1.5) -> dict | None:
    """Détecte si le prix courant se situe dans une zone de retracement de
    Fibonacci notable (0.5 exact, ou Golden Pocket 0.618-0.786), calculée sur
    la dernière impulsion majeure (plus haut/plus bas) des `lookback` bougies.

    Retourne un dict {direction, zone_label, sub_category, swing_high, swing_low,
    level_price} ou None si aucune zone n'est actuellement testée.
    """
    window = df.tail(lookback)
    if len(window) < lookback // 2:
        return None

    high_idx = window["high"].idxmax()
    low_idx = window["low"].idxmin()
    swing_high = float(window.loc[high_idx, "high"])
    swing_low = float(window.loc[low_idx, "low"])
    price_range = swing_high - swing_low
    if price_range <= 0:
        return None

    price = float(df["close"].iloc[-1])
    # Impulsion haussière si le plus bas est survenu AVANT le plus haut (donc on
    # retrace vers le bas depuis le sommet) ; sinon impulsion baissière.
    bullish_impulse = low_idx < high_idx

    def level(r: float) -> float:
        return swing_high - r * price_range if bullish_impulse else swing_low + r * price_range

    lvl_050 = level(0.5)
    lvl_0618 = level(0.618)
    lvl_0786 = level(0.786)
    tol = price_range * (tolerance_pct / 100)

    # Golden Pocket : zone entre 0.618 et 0.786 (bornes selon le sens de l'impulsion)
    gp_low, gp_high = sorted([lvl_0618, lvl_0786])

    if gp_low - tol <= price <= gp_high + tol:
        zone_label = "Golden Pocket (0.618 - 0.786)"
        sub_category = "golden_pocket"
        level_price = lvl_0618
    elif abs(price - lvl_050) <= tol:
        zone_label = "Retracement 0.50"
        sub_category = "retracement_050"
        level_price = lvl_050
    else:
        return None

    # Une impulsion haussière qui retrace -> on anticipe un rebond (Long).
    # Une impulsion baissière qui retrace -> on anticipe un rejet (Short).
    direction = "Long" if bullish_impulse else "Short"

    return {
        "direction": direction,
        "zone_label": zone_label,
        "sub_category": sub_category,
        "swing_high": swing_high,
        "swing_low": swing_low,
        "level_price": round(level_price, 8),
    }


# ---------------------------------------------------------------------------
# Indicateurs additionnels — Catégorie 10 (Global Breakout Score)
# ---------------------------------------------------------------------------

def garman_klass_volatility(df: pd.DataFrame, period: int = 24) -> float:
    """Volatilité de Garman-Klass (utilise O/H/L/C, plus précise que la
    volatilité close-to-close car elle capture le range intra-bougie)."""
    window = df.tail(period)
    if len(window) < 2:
        return 0.0
    log_hl = np.log(window["high"] / window["low"]).pow(2)
    log_co = np.log(window["close"] / window["open"]).pow(2)
    variance = (0.5 * log_hl - (2 * np.log(2) - 1) * log_co).mean()
    return float(np.sqrt(max(variance, 0)) * 100)  # en %


def parkinson_volatility(df: pd.DataFrame, period: int = 24) -> float:
    """Volatilité de Parkinson (utilise uniquement High/Low)."""
    window = df.tail(period)
    if len(window) < 2:
        return 0.0
    log_hl2 = np.log(window["high"] / window["low"]).pow(2)
    variance = log_hl2.mean() / (4 * np.log(2))
    return float(np.sqrt(max(variance, 0)) * 100)  # en %


def keltner_channels(df: pd.DataFrame, period: int = 20, atr_mult: float = 1.5):
    """Canaux de Keltner : EMA(period) ± atr_mult * ATR(period)."""
    mid = df["close"].ewm(span=period, adjust=False).mean()
    atr_val = atr(df, period)
    upper = mid + atr_mult * atr_val
    lower = mid - atr_mult * atr_val
    return upper, mid, lower


def squeeze_score(df: pd.DataFrame, bb_period: int = 20, bb_std: float = 2.0, kc_mult: float = 1.5) -> float:
    """Score de compression 0-1 : les Bandes de Bollinger sont-elles à
    l'intérieur des Canaux de Keltner (squeeze technique), et à quel point ?
    1.0 = compression maximale, 0.0 = pas de squeeze."""
    bb_upper, _, bb_lower, _ = bollinger_bands(df["close"], bb_period, bb_std)
    kc_upper, _, kc_lower = keltner_channels(df, bb_period, kc_mult)
    if len(bb_upper) == 0 or pd.isna(bb_upper.iloc[-1]) or pd.isna(kc_upper.iloc[-1]):
        return 0.0
    bb_width = float(bb_upper.iloc[-1] - bb_lower.iloc[-1])
    kc_width = float(kc_upper.iloc[-1] - kc_lower.iloc[-1])
    if kc_width <= 0:
        return 0.0
    ratio = bb_width / kc_width
    is_squeezed = ratio < 1.0  # BB strictement à l'intérieur de KC
    if not is_squeezed:
        return 0.0
    return float(max(0.0, min(1.0, 1 - ratio)))


def cvd_estimate(df: pd.DataFrame, period: int = 20) -> float:
    """Estimation du Cumulative Volume Delta (CVD) à partir des bougies OHLCV
    seules (approximation candle-based, PAS un vrai CVD tick-by-tick qui
    nécessiterait les trades agrégés avec le flag acheteur/vendeur) :
    delta_bougie = volume * signe(close - open). Retourne la somme cumulée
    sur `period` bougies, normalisée par le volume total (donc dans [-1, 1])."""
    window = df.tail(period)
    if len(window) == 0 or window["volume"].sum() == 0:
        return 0.0
    delta = window["volume"] * np.sign(window["close"] - window["open"])
    return float(delta.sum() / window["volume"].sum())


def delta_series(df: pd.DataFrame) -> pd.Series:
    """Delta par bougie (volume acheteur - volume vendeur), approximation candle-based
    cohérente avec cvd_estimate() ci-dessus : delta = volume * signe(close - open).
    ⚠️ PAS un vrai order flow delta (qui nécessiterait les trades exécutés tick-by-tick
    avec leur sens acheteur/vendeur, via aggTrades) — impraticable ici pour ~50-155
    paires x 4 exchanges sans exploser les limites API. C'est une approximation standard
    largement utilisée en l'absence de données tick, mais elle reste une approximation."""
    return df["volume"] * np.sign(df["close"] - df["open"])


def cvd_series(df: pd.DataFrame, period: int = 50) -> pd.Series:
    """CVD (Cumulative Volume Delta) cumulé, non normalisé, sur les `period` dernières
    bougies -> sert à détecter la tendance de fond du delta (accumulation/distribution)
    ou une divergence prix/CVD. Voir delta_series() pour la méthode d'approximation."""
    window = df.tail(period)
    return delta_series(window).cumsum()


def footprint_pressure(df: pd.DataFrame, n_candles: int = 5) -> dict:
    """Proxy 'footprint' simplifié à partir des bougies OHLCV : PAS un vrai footprint
    chart (qui décompose le volume acheteur/vendeur À CHAQUE NIVEAU DE PRIX à partir des
    trades tick-by-tick) — juste une estimation agrégée sur les `n_candles` dernières
    bougies :
      - buy_pressure : part du volume total sur des bougies haussières (close >= open)
      - absorption : la dernière bougie a un gros volume + un large range mais un petit
        corps (beaucoup d'ordres exécutés sans faire bouger le prix net -> signe
        classique d'absorption institutionnelle en lecture d'order flow)."""
    window = df.tail(n_candles)
    if len(window) == 0 or window["volume"].sum() == 0:
        return {"buy_pressure": 0.5, "absorption": False}
    buy_vol = window.loc[window["close"] >= window["open"], "volume"].sum()
    total_vol = window["volume"].sum()
    buy_pressure = float(buy_vol / total_vol)
    last = window.iloc[-1]
    candle_range = float(last["high"] - last["low"])
    body = float(abs(last["close"] - last["open"]))
    vol_avg = float(window["volume"].mean())
    absorption = bool(candle_range > 0 and body / candle_range < 0.35 and last["volume"] > 1.3 * vol_avg)
    return {"buy_pressure": buy_pressure, "absorption": absorption}


def vwap(df: pd.DataFrame, period: int = 24) -> float:
    """VWAP glissant sur `period` bougies (proxy du VWAP journalier en continu)."""
    window = df.tail(period)
    typical_price = (window["high"] + window["low"] + window["close"]) / 3
    total_volume = window["volume"].sum()
    if total_volume == 0:
        return float(df["close"].iloc[-1])
    return float((typical_price * window["volume"]).sum() / total_volume)


def volume_profile(df: pd.DataFrame, period: int = 100, bins: int = 24) -> dict:
    """Volume Profile simplifié : découpe la fourchette de prix des `period`
    dernières bougies en `bins` tranches, cumule le volume par tranche, et
    retourne le POC (Point of Control = tranche la + tradée), ainsi que
    VAH/VAL (bornes de la zone contenant ~70% du volume total, centrée sur le POC)."""
    window = df.tail(period)
    if len(window) < 5:
        last = float(df["close"].iloc[-1])
        return {"poc": last, "vah": last, "val": last}

    price_min, price_max = float(window["low"].min()), float(window["high"].max())
    if price_max <= price_min:
        last = float(df["close"].iloc[-1])
        return {"poc": last, "vah": last, "val": last}

    edges = np.linspace(price_min, price_max, bins + 1)
    typical = (window["high"] + window["low"] + window["close"]) / 3
    bin_idx = np.clip(np.digitize(typical, edges) - 1, 0, bins - 1)
    vol_per_bin = np.zeros(bins)
    for idx, v in zip(bin_idx, window["volume"]):
        vol_per_bin[idx] += v

    poc_idx = int(np.argmax(vol_per_bin))
    poc_price = float((edges[poc_idx] + edges[poc_idx + 1]) / 2)

    total_vol = vol_per_bin.sum()
    target = total_vol * 0.70
    lo, hi = poc_idx, poc_idx
    acc = vol_per_bin[poc_idx]
    while acc < target and (lo > 0 or hi < bins - 1):
        expand_lo = vol_per_bin[lo - 1] if lo > 0 else -1
        expand_hi = vol_per_bin[hi + 1] if hi < bins - 1 else -1
        if expand_hi >= expand_lo:
            hi = min(hi + 1, bins - 1)
            acc += vol_per_bin[hi]
        else:
            lo = max(lo - 1, 0)
            acc += vol_per_bin[lo]

    return {
        "poc": poc_price,
        "vah": float(edges[hi + 1]),
        "val": float(edges[lo]),
    }


def ichimoku_signal(df: pd.DataFrame, tenkan_p: int = 9, kijun_p: int = 26,
                     senkou_b_p: int = 52, displacement: int = 26) -> dict | None:
    """Ichimoku Kinko Hyo — retourne l'état actuel (dernière bougie clôturée) :
    Tenkan-sen, Kijun-sen, bornes du nuage (Senkou A/B, déjà décalées dans le temps
    comme sur un chart), et un biais Long/Short/Neutre basé sur 3 conditions
    combinées (prix vs nuage, pente de la Kijun, "Chikou libre" — approximé ici par
    comparaison du close actuel au close `displacement` bougies plus tôt, plutôt
    qu'une vraie détection d'obstruction de bougie/nuage à cet endroit précis).
    Retourne None si l'historique est insuffisant (il faut au moins
    senkou_b_p + displacement bougies)."""
    if len(df) < senkou_b_p + displacement + 2:
        return None

    high, low, close = df["high"], df["low"], df["close"]
    tenkan = (high.rolling(tenkan_p).max() + low.rolling(tenkan_p).min()) / 2
    kijun = (high.rolling(kijun_p).max() + low.rolling(kijun_p).min()) / 2
    senkou_a = ((tenkan + kijun) / 2).shift(displacement)
    senkou_b = ((high.rolling(senkou_b_p).max() + low.rolling(senkou_b_p).min()) / 2).shift(displacement)

    if pd.isna(senkou_a.iloc[-1]) or pd.isna(senkou_b.iloc[-1]) or pd.isna(kijun.iloc[-1]):
        return None

    price = float(close.iloc[-1])
    cloud_top = float(max(senkou_a.iloc[-1], senkou_b.iloc[-1]))
    cloud_bottom = float(min(senkou_a.iloc[-1], senkou_b.iloc[-1]))
    kijun_now = float(kijun.iloc[-1])
    kijun_prev = float(kijun.iloc[-6]) if len(kijun) > 6 and not pd.isna(kijun.iloc[-6]) else kijun_now
    kijun_slope_up = kijun_now > kijun_prev
    kijun_slope_down = kijun_now < kijun_prev

    chikou_free_up = chikou_free_down = False
    if len(close) > displacement:
        past_price = float(close.iloc[-1 - displacement])
        chikou_free_up = price > past_price
        chikou_free_down = price < past_price

    bias = "Neutre"
    if price > cloud_top and kijun_slope_up and chikou_free_up:
        bias = "Long"
    elif price < cloud_bottom and kijun_slope_down and chikou_free_down:
        bias = "Short"

    return {
        "bias": bias, "price": price, "tenkan": float(tenkan.iloc[-1]), "kijun": kijun_now,
        "cloud_top": cloud_top, "cloud_bottom": cloud_bottom,
        "kijun_slope_up": kijun_slope_up, "kijun_slope_down": kijun_slope_down,
    }


def swing_points(df: pd.DataFrame, lookback: int = 3) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Détecte les swing highs/lows (fractals ICT) : un point est un swing high/low
    s'il est le plus haut/bas parmi `lookback` bougies de part et d'autre. Retourne
    (swing_highs, swing_lows), chacun une liste de (index dans df, prix)."""
    highs, lows = df["high"], df["low"]
    n = len(df)
    swing_highs, swing_lows = [], []
    for i in range(lookback, n - lookback):
        window_h = highs.iloc[i - lookback: i + lookback + 1]
        window_l = lows.iloc[i - lookback: i + lookback + 1]
        if highs.iloc[i] == window_h.max():
            swing_highs.append((i, float(highs.iloc[i])))
        if lows.iloc[i] == window_l.min():
            swing_lows.append((i, float(lows.iloc[i])))
    return swing_highs, swing_lows


def detect_liquidity_sweep(df: pd.DataFrame, swing_highs: list, swing_lows: list,
                            recent_window: int = 6) -> dict | None:
    """Cherche, dans les `recent_window` dernières bougies, une mèche qui dépasse un
    ancien swing low/high (formé AVANT cette fenêtre) puis une clôture qui réintègre
    à l'intérieur -> "chasse aux stops" (Liquidity Sweep) ICT. Retourne le setup
    Long (sweep de sell-side liquidity) ou Short (sweep de buy-side liquidity)
    détecté, ou None si aucun des deux."""
    n = len(df)
    if n < recent_window + 5:
        return None
    recent = df.iloc[-recent_window:]
    last_close = float(df["close"].iloc[-1])
    cutoff = n - recent_window

    old_lows = [(i, p) for i, p in swing_lows if i < cutoff]
    if old_lows:
        idx, level = max(old_lows, key=lambda x: x[0])  # swing low le plus récent avant la fenêtre
        sweep_extreme = float(recent["low"].min())
        if sweep_extreme < level and last_close > level:
            return {"direction": "Long", "swept_level": level, "sweep_extreme": sweep_extreme, "swept_idx": idx}

    old_highs = [(i, p) for i, p in swing_highs if i < cutoff]
    if old_highs:
        idx, level = max(old_highs, key=lambda x: x[0])
        sweep_extreme = float(recent["high"].max())
        if sweep_extreme > level and last_close < level:
            return {"direction": "Short", "swept_level": level, "sweep_extreme": sweep_extreme, "swept_idx": idx}

    return None


def detect_choch(df: pd.DataFrame, sweep: dict, swing_highs: list, swing_lows: list) -> bool:
    """Change of Character / Market Structure Shift : après le sweep, la clôture
    actuelle doit casser le premier obstacle structurel (swing high pour un setup
    Long, swing low pour un Short) formé APRÈS le point balayé -> confirme le
    retournement plutôt qu'un simple faux mouvement."""
    last_close = float(df["close"].iloc[-1])
    swept_idx = sweep["swept_idx"]
    if sweep["direction"] == "Long":
        candidates = [p for i, p in swing_highs if swept_idx < i < len(df) - 1]
        if not candidates:
            return False
        return last_close > min(candidates)
    else:
        candidates = [p for i, p in swing_lows if swept_idx < i < len(df) - 1]
        if not candidates:
            return False
        return last_close < max(candidates)


def detect_fvg(df: pd.DataFrame, direction: str, lookback: int = 15) -> dict | None:
    """Fair Value Gap (déséquilibre 3 bougies, ICT) : gap haussier si high[i-2] <
    low[i], gap baissier si low[i-2] > high[i]. Cherche le FVG le plus RÉCENT dans
    le sens `direction` sur les `lookback` dernières bougies, et indique si le prix
    actuel est retourné à l'intérieur (retest -> zone d'entrée idéale)."""
    highs, lows, close = df["high"], df["low"], df["close"]
    n = len(df)
    price = float(close.iloc[-1])
    start = max(2, n - lookback)
    best = None
    for i in range(start, n):
        if direction == "Long" and float(highs.iloc[i - 2]) < float(lows.iloc[i]):
            best = {"gap_low": float(highs.iloc[i - 2]), "gap_high": float(lows.iloc[i]), "idx": i}
        elif direction == "Short" and float(lows.iloc[i - 2]) > float(highs.iloc[i]):
            best = {"gap_low": float(highs.iloc[i]), "gap_high": float(lows.iloc[i - 2]), "idx": i}
    if best is None:
        return None
    best["price_in_gap"] = best["gap_low"] <= price <= best["gap_high"]
    return best


def vwap_bands(df: pd.DataFrame, period: int = 100, band_mult: float = 1.0) -> dict:
    """VWAP "de session" (cumulatif sur les `period` dernières bougies, en l'absence
    de reset de session exchange-native) + bandes à ±band_mult écart-type pondéré
    par le volume -- proxy du Session/Anchored VWAP avec bandes utilisé en ICT."""
    window = df.tail(period).reset_index(drop=True)
    if len(window) < 10 or window["volume"].sum() == 0:
        last = float(df["close"].iloc[-1])
        return {"vwap": last, "vwap_prev": last, "upper": last, "lower": last}

    typical = (window["high"] + window["low"] + window["close"]) / 3
    cum_vol = window["volume"].cumsum()
    cum_tpv = (typical * window["volume"]).cumsum()
    vwap_series = cum_tpv / cum_vol.replace(0, np.nan)
    dev = typical - vwap_series
    cum_var = ((dev ** 2) * window["volume"]).cumsum()
    vwap_std = np.sqrt(cum_var / cum_vol.replace(0, np.nan))
    upper = vwap_series + band_mult * vwap_std
    lower = vwap_series - band_mult * vwap_std
    idx_prev = max(0, len(vwap_series) - 6)

    def _safe(series, idx):
        v = series.iloc[idx]
        return float(v) if not pd.isna(v) else float(vwap_series.iloc[-1])

    return {
        "vwap": _safe(vwap_series, -1),
        "vwap_prev": _safe(vwap_series, idx_prev),
        "upper": _safe(upper, -1),
        "lower": _safe(lower, -1),
    }


def smt_divergence(swing_alt: list, swing_ref: list, direction: str) -> bool:
    """Divergence SMT (Smart Money Technique) : compare les 2 derniers swing lows
    (setup Long) ou swing highs (setup Short) de l'actif vs son actif référent
    (ex: BTC), sur la même fenêtre temporelle.
    - Long : BTC fait un Lower Low mais l'actif fait un Higher Low -> force relative.
    - Short : BTC fait un Higher High mais l'actif fait un Lower High -> faiblesse relative."""
    if len(swing_alt) < 2 or len(swing_ref) < 2:
        return False
    alt_last, alt_prev = swing_alt[-1][1], swing_alt[-2][1]
    ref_last, ref_prev = swing_ref[-1][1], swing_ref[-2][1]
    if direction == "Long":
        return ref_last < ref_prev and alt_last > alt_prev
    return ref_last > ref_prev and alt_last < alt_prev


def beta_vs_reference(returns: pd.Series, ref_returns: pd.Series) -> float:
    """Bêta de `returns` par rapport à `ref_returns` (ex: BTC), sur la période
    commune disponible. beta = cov(actif, référence) / var(référence)."""
    n = min(len(returns), len(ref_returns))
    if n < 10:
        return 1.0
    a = returns.tail(n).reset_index(drop=True)
    b = ref_returns.tail(n).reset_index(drop=True)
    var_b = b.var()
    if var_b == 0 or pd.isna(var_b):
        return 1.0
    cov_ab = a.cov(b)
    return float(cov_ab / var_b)


def liquidation_zones_multi(price: float, leverage_levels: list[int]) -> list[dict]:
    """Zones de liquidation ESTIMÉES (projection à effet de levier constant,
    PAS un flux de liquidations réel) pour chaque niveau de levier fourni
    (ex: [10, 25, 50, 100]). Retourne une liste triée par levier croissant :
    [{"leverage": 10, "long_price": ..., "short_price": ...}, ...].
    Un levier plus élevé = zone plus proche du prix actuel (liquidation plus
    rapide en cas de mouvement adverse)."""
    zones = []
    for lev in sorted(leverage_levels):
        zones.append({
            "leverage": lev,
            "long_price": round(price * (1 - 1 / lev), 8),
            "short_price": round(price * (1 + 1 / lev), 8),
        })
    return zones


def closest_liquidation_zone(zones: list[dict], price: float, side: str) -> dict:
    """Parmi les zones multi-niveaux, retourne celle dont le prix ('long_price' ou
    'short_price' selon `side`) est le plus proche du prix courant -> le niveau de
    levier le plus vraisemblable pour former une "zone aimant" à court terme,
    plutôt que de supposer arbitrairement un levier fixe (25x)."""
    key = "long_price" if side == "long" else "short_price"
    return min(zones, key=lambda z: abs(price - z[key]))


# ---------------------------------------------------------------------------
# Indicateurs additionnels — Catégorie 11 (Scalping IA / Grok)
# ---------------------------------------------------------------------------

def ema(close: pd.Series, period: int) -> pd.Series:
    """Moyenne mobile exponentielle standard."""
    return close.ewm(span=period, adjust=False).mean()


def stochastic_rsi(close: pd.Series, rsi_period: int = 14, stoch_period: int = 14,
                    k_smooth: int = 3, d_smooth: int = 3) -> dict:
    """Stochastique RSI (%K et %D), calculé sur le RSI plutôt que sur le prix."""
    rsi_series = rsi(close, rsi_period)
    lo = rsi_series.rolling(stoch_period).min()
    hi = rsi_series.rolling(stoch_period).max()
    raw_k = 100 * (rsi_series - lo) / (hi - lo).replace(0, np.nan)
    k = raw_k.rolling(k_smooth).mean()
    d = k.rolling(d_smooth).mean()
    return {"k": k, "d": d}


def reversal_candle(df: pd.DataFrame, direction: str) -> bool:
    """Détecte sur la DERNIÈRE bougie un pattern de retournement simple dans le sens
    `direction` : soit une bougie engloutissante (engulfing) par rapport à la
    précédente, soit un marteau/pin bar (grande mèche du côté opposé, petit corps)."""
    if len(df) < 2:
        return False
    prev, last = df.iloc[-2], df.iloc[-1]
    body = abs(last["close"] - last["open"])
    candle_range = last["high"] - last["low"]
    if candle_range <= 0:
        return False

    if direction == "Long":
        engulfing = (
            prev["close"] < prev["open"] and last["close"] > last["open"]
            and last["close"] >= prev["open"] and last["open"] <= prev["close"]
        )
        lower_wick = min(last["open"], last["close"]) - last["low"]
        hammer = last["close"] > last["open"] and lower_wick > 2 * body
        return bool(engulfing or hammer)

    engulfing = (
        prev["close"] > prev["open"] and last["close"] < last["open"]
        and last["close"] <= prev["open"] and last["open"] >= prev["close"]
    )
    upper_wick = last["high"] - max(last["open"], last["close"])
    shooting_star = last["close"] < last["open"] and upper_wick > 2 * body
    return bool(engulfing or shooting_star)


def order_block(df: pd.DataFrame, direction: str, lookback: int = 30) -> dict | None:
    """Order Block (ICT) simplifié : la dernière bougie de couleur OPPOSÉE à la
    direction, immédiatement suivie d'une bougie d'impulsion forte (corps > 1.5x le
    corps moyen) dans le sens `direction`. Retourne la zone [low, high] de cette
    bougie, ou None si aucune trouvée dans les `lookback` dernières bougies."""
    n = len(df)
    if n < lookback + 2:
        return None
    window = df.tail(lookback + 1).reset_index(drop=True)
    bodies = (window["close"] - window["open"]).abs()
    avg_body = float(bodies.mean()) or 1e-9

    best = None
    for i in range(len(window) - 1):
        cur, nxt = window.iloc[i], window.iloc[i + 1]
        nxt_body = abs(nxt["close"] - nxt["open"])
        if direction == "Long":
            is_opposite = cur["close"] < cur["open"]
            is_impulse = nxt["close"] > nxt["open"] and nxt_body > 1.5 * avg_body
        else:
            is_opposite = cur["close"] > cur["open"]
            is_impulse = nxt["close"] < nxt["open"] and nxt_body > 1.5 * avg_body
        if is_opposite and is_impulse:
            best = {"low": float(cur["low"]), "high": float(cur["high"])}
    return best
