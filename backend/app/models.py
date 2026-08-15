"""Schémas de données échangés entre le backend et le frontend."""
from datetime import datetime
from pydantic import BaseModel


class LiquidationZone(BaseModel):
    """Zone de liquidation ESTIMÉE (projection à effet de levier constant), PAS un
    flux de liquidations réel. Un niveau par levier courant (10x/25x/50x/100x)."""
    leverage: int
    long_price: float
    short_price: float


class AssetSignal(BaseModel):
    symbol: str
    category: str                 # "probabilite_mouvement" | "chop_eleve" | "correlation_btc" | "liquidations"
    score: float                  # score composite 0-100
    trigger_type: str             # "technique" | "fondamental" | "technique+fondamental"
    trigger_reason: str           # explication textuelle du déclencheur
    direction: str                # "Long" | "Short" | "Neutre (Long)" | "Neutre (Short)"
                                   # (les 2 derniers formats sont utilisés en Catégorie 2 :
                                   # marché en range, mais avec un biais de breakout indiqué)
    entry: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    price: float
    atr_pct: float
    rsi_h1: float
    chop_h4: float
    volume_ratio: float
    funding_rate: float | None = None
    sparkline: list[float] = []   # ~24 dernières clôtures H1, pour mini-graphique
    liquidation_zones: list[LiquidationZone] = []  # peuplé pour la Catégorie 5 uniquement
    is_fallback: bool = False  # True si le score n'atteint pas le seuil "qualifié" de sa
                                 # catégorie/stratégie et que ce signal vient d'un repli


class Category6Strategies(BaseModel):
    """Catégorie 6 — Stratégies personnalisées. Deux modules indépendants, chacun
    produisant une liste de signaux au même format que les Cat.1/2/4/5 (AssetSignal),
    pour bénéficier gratuitement de l'affichage frontend (AssetCard) et des
    notifications déjà en place. Remplace l'ancienne Cat.6 (Unlocks)."""
    strategie1: list[AssetSignal] = []
    strategie2: list[AssetSignal] = []


class SocialSpikeSignal(BaseModel):
    """Bonus Trading, partie 1. ⚠️ Nécessite ANTHROPIC_API_KEY (recherche web)."""
    symbol: str
    cause: str                  # partenariat / annonce / rumeur / listing...
    volume_change_24h_pct: float | None = None
    behavior: str                # "Compression" | "Accumulation" | "Prise de profit" | "Indéterminé"
    summary: str


class DerivativesAltcoin(BaseModel):
    """Bonus Trading, partie 2 — basé sur données Binance réelles (OI + funding)."""
    symbol: str
    oi_change_24h_pct: float | None
    funding_rate: float | None
    price: float
    nearest_liquidation_zone: float
    zone_distance_pct: float
    zone_side: str               # "Squeeze longs" | "Squeeze shorts"
    reasoning: str
    liquidation_zones: list[LiquidationZone] = []


class BonusTrading(BaseModel):
    social_spikes: list[SocialSpikeSignal] = []
    social_spikes_available: bool = False
    derivatives_top3: list[DerivativesAltcoin] = []


class MultiExchangeSignal(BaseModel):
    """Modèle commun aux Catégories 7, 8 et 9 (OKX + Hyperliquid).
    Les champs spécifiques à une seule catégorie sont optionnels (None ailleurs)."""
    exchange: str                  # "OKX" | "Hyperliquid"
    symbol: str                    # ex: "BTC-USDT-SWAP" (OKX) ou "BTC" (Hyperliquid)
    score: float
    direction: str                 # "Long" | "Short"
    trigger_reason: str
    entry: float
    take_profit: float
    stop_loss: float
    risk_reward: float
    price: float
    volume_trend_pct: float        # variation vs moyenne mobile 20 périodes
    open_interest_usd: float | None = None
    oi_change_24h_pct: float | None = None   # None si l'exchange ne fournit pas l'historique
    spread_pct: float | None = None
    liquidation_long: float | None = None    # ⚠️ estimation heuristique — niveau le + proche (multi-levier)
    liquidation_short: float | None = None
    liquidation_zones: list[LiquidationZone] = []  # détail par niveau de levier (10x/25x/50x/100x)
    sparkline: list[float] = []
    # --- Catégorie 8 uniquement (RSI multi-timeframe) ---
    rsi_h1: float | None = None
    rsi_h4: float | None = None
    rsi_d1: float | None = None
    # --- Catégorie 9 uniquement (Fibonacci) ---
    fib_level_label: str | None = None       # "Retracement 0.50" | "Golden Pocket (0.618 - 0.786)"
    fib_sub_category: str | None = None      # "retracement_050" | "golden_pocket"
    is_fallback: bool = False  # True si aucun candidat n'a atteint FIB_MIN_SCORE dans cette
                                 # sous-catégorie/exchange et que ce signal vient du repli 40-65


class Category9Result(BaseModel):
    """Cat.9 a deux sous-catégories (0.5 et Golden Pocket), par exchange."""
    retracement_050: list[MultiExchangeSignal] = []
    golden_pocket: list[MultiExchangeSignal] = []


class Category10Signal(BaseModel):
    """Catégorie 10 — Global Breakout Score (GSB), Binance Futures + Bybit Futures.
    5 sous-scores (0-100) combinés en un score global, filtré à GSB >= 60."""
    exchange: str                  # "Binance" | "Bybit"
    symbol: str
    gsb_score: float               # score global 0-100
    vsi_score: float                # Volatility Squeeze Index (poids 25%)
    rvol_score: float               # Relative Volume & Flow Imbalance (poids 20%)
    oifd_score: float               # Open Interest & Funding Disparity (poids 25%)
    msd_score: float                 # Market Structure & Key Level Distance (poids 15%)
    corr_score: float                # BTC/ETH Beta & Correlation Factor (poids 15%)
    direction: str                  # "Long" | "Short"
    trigger_reason: str
    entry: float
    take_profit: float
    stop_loss: float
    risk_reward: float
    price: float
    atr_pct: float
    volume_trend_pct: float
    open_interest_usd: float | None = None
    oi_change_pct: float | None = None
    funding_rate: float | None = None
    spread_pct: float | None = None
    orderbook_imbalance: float | None = None    # 0-1, >0.5 = pression acheteuse
    beta_btc: float | None = None
    key_level_label: str | None = None          # ex: "VWAP", "Plus haut 7j", "POC"
    key_level_distance_pct: float | None = None
    liquidation_long: float | None = None       # ⚠️ estimation heuristique — niveau le + proche (multi-levier)
    liquidation_short: float | None = None
    liquidation_zones: list[LiquidationZone] = []   # détail par niveau de levier (10x/25x/50x/100x)
    is_fallback: bool = False  # True si aucune paire n'a atteint GSB_MIN_SCORE ce scan et
                                 # que ce signal provient du repli 40-60 (voir CATEGORY10_FALLBACK_MIN_SCORE)
    sparkline: list[float] = []


class ScanResult(BaseModel):
    id: int | None = None
    timestamp: datetime
    category1: list[AssetSignal]
    category2: list[AssetSignal]
    category4: list[AssetSignal] = []          # Corrélation BTC (divergence)
    category6: Category6Strategies = Category6Strategies()  # Stratégies personnalisées (1 et 2)
    bonus_trading: BonusTrading | None = None
    # Cat.7/9 : dict par exchange -> {"Bybit": [...], "OKX": [...]}
    category7: dict[str, list[MultiExchangeSignal]] = {}   # Mouvements imminents haute probabilité (4h)
    category10: list[Category10Signal] = []                 # Global Breakout Score (Binance+Bybit)
    category9: dict[str, Category9Result] = {}              # Stratégie Fib (Binance+Bybit)
    category11: list[AssetSignal] = []                       # Scalping IA (Grok) — EMA200+VWAP/OB+StochRSI
    symbols_analyzed: int
    errors: list[str] = []


class TraditionalMarketAsset(BaseModel):
    """⚠️ Source Yahoo Finance (API non-officielle, best-effort)."""
    name: str
    ticker: str
    price: float | None
    change_pct_1d: float | None
    support: float | None
    resistance: float | None
    ma20: float | None
    ma50: float | None
    rsi: float | None
    classification: str   # "Compression" | "Accumulation" | "Prise de profit" | "Zone neutre" | "Indisponible"


class ETFFlowDay(BaseModel):
    date: str
    net_flow_usd_m: float


class ETFFlows(BaseModel):
    """⚠️ Nécessite ANTHROPIC_API_KEY (recherche web) — pas d'API publique gratuite fiable."""
    btc: list[ETFFlowDay] = []
    eth: list[ETFFlowDay] = []
    sol: list[ETFFlowDay] = []
    available: bool = False


class FearGreedData(BaseModel):
    value: int
    classification: str
    updated_at: str


class WatchlistItem(BaseModel):
    symbol: str
    added_at: datetime
    price: float | None = None


class BacktestStats(BaseModel):
    total_closed: int
    wins: int
    losses: int
    expired: int
    win_rate_pct: float
    by_category: dict[str, dict[str, float]]
