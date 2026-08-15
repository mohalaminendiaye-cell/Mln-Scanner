"""
Configuration centrale de l'application.
Toutes les valeurs sensibles / ajustables viennent des variables d'environnement (.env).
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _parse_scan_times(raw: str) -> list[tuple[int, int]]:
    """Parse une liste "HH:MM,HH:MM,..." (ou juste "HH,HH,..." pour rester
    compatible avec l'ancien format à l'heure pile) en liste de tuples (heure, minute)."""
    times = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            h, m = part.split(":")
        else:
            h, m = part, "0"
        times.append((int(h), int(m)))
    return times


class Settings:
    # --- Binance Futures ---
    BINANCE_BASE_URL: str = os.getenv("BINANCE_BASE_URL", "https://fapi.binance.com")
    # Nombre de paires (les plus liquides par volume 24h) analysées à chaque scan.
    # Limiter ce nombre évite de saturer les rate limits Binance.
    TOP_N_SYMBOLS: int = int(os.getenv("TOP_N_SYMBOLS", "250"))
    QUOTE_ASSET: str = "USDT"
    # Concurrence max de requêtes klines simultanées
    MAX_CONCURRENT_REQUESTS: int = int(os.getenv("MAX_CONCURRENT_REQUESTS", "8"))

    # --- Horaires de scan (heure de Dakar = GMT, pas de DST) ---
    # Format "HH:MM,HH:MM,..." pour un contrôle précis à la minute (remplace l'ancien
    # SCAN_HOURS qui ne permettait que l'heure pile).
    SCAN_TIMES: list[tuple[int, int]] = _parse_scan_times(os.getenv("SCAN_TIMES", "08:45,13:15,00:15"))
    SCAN_TIMEZONE: str = os.getenv("SCAN_TIMEZONE", "Africa/Dakar")  # UTC+0 toute l'année

    # --- OKX / Hyperliquid (Catégories 7, 8, 9 — "Top Movers" multi-exchange) ---
    # ⚠️ Intégrations non testées en conditions réelles (pas d'accès internet
    # dans l'environnement de développement). Désactivables individuellement
    # si un problème de connectivité/schéma apparaît en production.
    OKX_ENABLED: bool = _get_bool("OKX_ENABLED", True)
    HYPERLIQUID_ENABLED: bool = _get_bool("HYPERLIQUID_ENABLED", True)
    # Nombre de symboles analysés par exchange (plus petit que Binance pour
    # limiter le temps de scan total, vu qu'on ajoute 2 exchanges de plus)
    MULTI_EXCHANGE_TOP_N_SYMBOLS: int = int(os.getenv("MULTI_EXCHANGE_TOP_N_SYMBOLS", "60"))
    MULTI_EXCHANGE_MIN_QUOTE_VOLUME: float = float(
        os.getenv("MULTI_EXCHANGE_MIN_QUOTE_VOLUME", "2000000")
    )
    # Catégorie 9 : tolérance autour des niveaux Fibonacci (en % de l'amplitude
    # du mouvement) pour considérer que le prix "teste" la zone
    FIBO_LOOKBACK_CANDLES: int = int(os.getenv("FIBO_LOOKBACK_CANDLES", "60"))
    FIBO_TOLERANCE_PCT: float = float(os.getenv("FIBO_TOLERANCE_PCT", "1.5"))

    # --- Catégorie 9 / Stratégie Fib : Fibonacci + Volume Profile + VWAP + Market
    # Structure + Liquidity Sweep + Delta/CVD + Footprint (proxy) ---
    # Score minimum (/100) pour être éligible au top 5, même architecture que les
    # Stratégies 1 et 2 de la Catégorie 6 (1 filtre structurel bloquant + scoring pondéré)
    FIB_MIN_SCORE: float = float(os.getenv("FIB_MIN_SCORE", "65"))
    # Si AUCUN candidat n'atteint FIB_MIN_SCORE (par sous-catégorie/exchange), repli
    # sur le top 5 des scores entre FIB_FALLBACK_MIN_SCORE et FIB_MIN_SCORE, marqués
    # explicitement is_fallback=True plutôt que de ne rien afficher (même logique que
    # le repli de la Catégorie 10, voir category10_scanner.py)
    FIB_FALLBACK_MIN_SCORE: float = float(os.getenv("FIB_FALLBACK_MIN_SCORE", "40"))
    FIB_MIN_RR: float = float(os.getenv("FIB_MIN_RR", "1.8"))
    # Fenêtre (bougies H1) dans laquelle chercher un sweep récent du swing ayant ancré le Fibo
    FIB_SWEEP_WINDOW: int = int(os.getenv("FIB_SWEEP_WINDOW", "12"))
    FIB_SWING_LOOKBACK: int = int(os.getenv("FIB_SWING_LOOKBACK", "2"))
    # Distance de référence au niveau clé de Volume Profile (POC/VAH/VAL) : score plein
    # à 0%, décroît jusqu'à 0 à 2x cette distance
    FIB_VP_PROXIMITY_PCT: float = float(os.getenv("FIB_VP_PROXIMITY_PCT", "1.5"))
    # Nombre de bougies H1 sur lesquelles évaluer la tendance du CVD et la pression du footprint
    FIB_CVD_LOOKBACK: int = int(os.getenv("FIB_CVD_LOOKBACK", "20"))
    FIB_FOOTPRINT_CANDLES: int = int(os.getenv("FIB_FOOTPRINT_CANDLES", "5"))

    # --- Catégorie 10 : Global Breakout Score (Binance Futures + Bybit Futures) ---
    # ⚠️ Intégration Bybit non testée en conditions réelles (voir bybit_client.py).
    BYBIT_ENABLED: bool = _get_bool("BYBIT_ENABLED", True)
    CATEGORY10_TOP_N_SYMBOLS: int = int(os.getenv("CATEGORY10_TOP_N_SYMBOLS", "50"))
    CATEGORY10_MIN_QUOTE_VOLUME: float = float(os.getenv("CATEGORY10_MIN_QUOTE_VOLUME", "3000000"))
    GSB_MIN_SCORE: float = float(os.getenv("GSB_MIN_SCORE", "60"))
    # Poids fixés par le cahier des charges (somme = 1.0)
    GSB_WEIGHT_VSI: float = 0.25
    GSB_WEIGHT_RVOL: float = 0.20
    GSB_WEIGHT_OIFD: float = 0.25
    GSB_WEIGHT_MSD: float = 0.15
    GSB_WEIGHT_CORR: float = 0.15
    # Multiplicateur d'ATR pour le Take Profit du plan de trade Cat.10 (au lieu d'un
    # objectif en % fixe type TARGET_MOVE_PCT) : un signal basé sur la compression de
    # volatilité (VSI) doit viser un objectif proportionnel à la volatilité réelle de
    # l'actif plutôt qu'un pourcentage générique disproportionné sur les actifs peu
    # volatils (et trop conservateur sur les actifs très volatils).
    GSB_TP_ATR_MULTIPLIER: float = float(os.getenv("GSB_TP_ATR_MULTIPLIER", "3.0"))
    # Funding rate à partir duquel le sous-facteur OIFD considère un positionnement
    # "extrême" (score plein). 0.01 (1%) était trop strict : ce niveau est rarissime,
    # un funding vraiment tendu tourne plutôt autour de 0.3-0.5% en pratique -> ça
    # écrasait le sous-score OIFD la plupart du temps. Reste ajustable selon ce
    # qu'observent les logs de diagnostic Cat.10.
    GSB_FUNDING_EXTREME_PCT: float = float(os.getenv("GSB_FUNDING_EXTREME_PCT", "0.004"))

    # --- Catégorie 6 / Stratégie 1 : confluence Ichimoku + Volume Profile + Order Book ---
    STRATEGIE1_TOP_N_SYMBOLS: int = int(os.getenv("STRATEGIE1_TOP_N_SYMBOLS", "155"))
    STRATEGIE1_MIN_QUOTE_VOLUME: float = float(os.getenv("STRATEGIE1_MIN_QUOTE_VOLUME", "20000000"))  # 20M$
    # Score minimum (/100) pour être éligible au top 5 — même seuil que la Stratégie 2,
    # pour une cohérence d'exigence entre les deux modules de la Catégorie 6
    STRATEGIE1_MIN_SCORE: float = float(os.getenv("STRATEGIE1_MIN_SCORE", "65"))
    STRATEGIE1_MIN_RVOL: float = float(os.getenv("STRATEGIE1_MIN_RVOL", "1.2"))  # était 1.5
    STRATEGIE1_CHOP_TREND_MAX: float = float(os.getenv("STRATEGIE1_CHOP_TREND_MAX", "45"))  # était 38.2
    STRATEGIE1_CHOP_RANGE_MIN: float = float(os.getenv("STRATEGIE1_CHOP_RANGE_MIN", "58"))  # était 61.8
    STRATEGIE1_RSI_MIN: float = float(os.getenv("STRATEGIE1_RSI_MIN", "30"))  # était 35
    STRATEGIE1_RSI_MAX: float = float(os.getenv("STRATEGIE1_RSI_MAX", "70"))  # était 65
    # Distance de référence au niveau clé de Volume Profile (POC/VAH/VAL) : score plein
    # à 0%, décroît jusqu'à 0 à 2x cette distance (utilisée pour le SCORE, plus un
    # cutoff strict comme avant)
    STRATEGIE1_VP_PROXIMITY_PCT: float = float(os.getenv("STRATEGIE1_VP_PROXIMITY_PCT", "1.8"))  # était 1.0
    STRATEGIE1_TARGET_PCT: float = float(os.getenv("STRATEGIE1_TARGET_PCT", "0.05"))  # TP ≈ 5%
    STRATEGIE1_MIN_RR: float = float(os.getenv("STRATEGIE1_MIN_RR", "1.5"))  # était 2.0
    STRATEGIE1_BTC_PAUSE_CHOP: float = float(os.getenv("STRATEGIE1_BTC_PAUSE_CHOP", "60"))  # pause si CHOP(1H) BTC > seuil

    # --- Catégorie 6 / Stratégie 2 : Scalping ICT/VWAP (scoring pondéré 100 pts) ---
    STRATEGIE2_TOP_N_SYMBOLS: int = int(os.getenv("STRATEGIE2_TOP_N_SYMBOLS", "155"))
    STRATEGIE2_MIN_QUOTE_VOLUME: float = float(os.getenv("STRATEGIE2_MIN_QUOTE_VOLUME", "15000000"))  # 15M$ (spec)
    # Score minimum (/100) pour être éligible au top 5
    STRATEGIE2_MIN_SCORE: float = float(os.getenv("STRATEGIE2_MIN_SCORE", "65"))
    # Seuil RVOL pour le plein crédit des 15 pts du sous-score "Volume d'expansion"
    STRATEGIE2_MIN_RVOL: float = float(os.getenv("STRATEGIE2_MIN_RVOL", "1.3"))
    STRATEGIE2_MIN_RR: float = float(os.getenv("STRATEGIE2_MIN_RR", "2.0"))  # spec : minimum 1:2
    # Cible d'impulsion : ~1.5% par défaut, cible structurelle acceptée si dans [1.2%, 2.0%]
    STRATEGIE2_TARGET_PCT: float = float(os.getenv("STRATEGIE2_TARGET_PCT", "0.015"))
    STRATEGIE2_TARGET_MIN_PCT: float = float(os.getenv("STRATEGIE2_TARGET_MIN_PCT", "0.012"))
    STRATEGIE2_TARGET_MAX_PCT: float = float(os.getenv("STRATEGIE2_TARGET_MAX_PCT", "0.02"))
    # Actifs de référence pour la divergence SMT (bonus de score, pas un filtre bloquant) :
    # BTC vérifié en premier, ETH en second si BTC ne montre pas de divergence
    STRATEGIE2_SMT_REFERENCE: str = os.getenv("STRATEGIE2_SMT_REFERENCE", "BTCUSDT")
    STRATEGIE2_SMT_SECONDARY_REFERENCE: str = os.getenv("STRATEGIE2_SMT_SECONDARY_REFERENCE", "ETHUSDT")
    # Fenêtre de recherche d'un sweep récent = 15 dernières bougies (spec), sur 1m OU 5m
    STRATEGIE2_SWEEP_WINDOW: int = int(os.getenv("STRATEGIE2_SWEEP_WINDOW", "15"))
    # Lookback (bougies de chaque côté) pour qu'un point soit un swing high/low ; plus
    # petit = plus de swings détectés = sweep/CHoCH plus faciles à valider
    STRATEGIE2_SWING_LOOKBACK: int = int(os.getenv("STRATEGIE2_SWING_LOOKBACK", "2"))
    # Tolérance de distance au FVG (en multiple d'ATR) pour le crédit partiel du
    # sous-score "Qualité du FVG/Entry" quand le prix n'est pas retourné dedans
    STRATEGIE2_FVG_ATR_TOLERANCE: float = float(os.getenv("STRATEGIE2_FVG_ATR_TOLERANCE", "1.0"))
    # Garde-fou de sécurité supplémentaire (hors spec) : pause si BTC trop choppy/mèche
    # violente, comme pour la Stratégie 1
    STRATEGIE2_BTC_PAUSE_CHOP: float = float(os.getenv("STRATEGIE2_BTC_PAUSE_CHOP", "60"))

    # Catégorie 10 (GSB) : si AUCUNE paire n'atteint GSB_MIN_SCORE (60), on retombe sur
    # le top 5 des paires ayant un score entre CATEGORY10_FALLBACK_MIN_SCORE et
    # GSB_MIN_SCORE, marquées explicitement comme "proches du seuil" plutôt que de ne
    # rien afficher.
    CATEGORY10_FALLBACK_MIN_SCORE: float = float(os.getenv("CATEGORY10_FALLBACK_MIN_SCORE", "40"))

    # --- Indicateurs ---
    ATR_PERIOD: int = 14
    RSI_PERIOD: int = 14
    CHOP_PERIOD: int = 14           # Choppiness Index calculé en H4
    BB_PERIOD: int = 20
    BB_STD: float = 2.0
    VOLUME_SMA_PERIOD: int = 20
    RANGE_LOOKBACK: int = 20        # bougies pour détecter les bornes du range (Cat.2)

    # --- Seuils de sélection ---
    CHOP_THRESHOLD: float = float(os.getenv("CHOP_THRESHOLD", "60"))
    MIN_RR: float = 2.0              # Ratio Risque/Rendement minimum exigé
    TARGET_MOVE_PCT: float = 0.05    # Mouvement significatif visé (±5%)

    # --- Base de données ---
    # ⚠️ Doit pointer vers le volume Docker persistant (/app/data, monté par
    # docker-compose.yml). Un chemin relatif comme "sqlite:///./scanner.db" écrirait
    # dans la couche writable du conteneur (WORKDIR /app), qui est PERDUE à chaque
    # recréation du conteneur (`docker-compose up --build`) -> perte de tout
    # l'historique de scans, la watchlist et le backtest à chaque mise à jour du code.
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:////app/data/scanner.db")

    # --- Notifications ---
    NOTIFY_TELEGRAM: bool = _get_bool("NOTIFY_TELEGRAM", False)
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    NOTIFY_DISCORD: bool = _get_bool("NOTIFY_DISCORD", False)
    DISCORD_WEBHOOK_URL: str = os.getenv("DISCORD_WEBHOOK_URL", "")

    NOTIFY_EMAIL: bool = _get_bool("NOTIFY_EMAIL", False)
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    EMAIL_FROM: str = os.getenv("EMAIL_FROM", "")
    EMAIL_TO: str = os.getenv("EMAIL_TO", "")

    # --- CORS ---
    ALLOWED_ORIGINS: list[str] = os.getenv("ALLOWED_ORIGINS", "*").split(",")

    # --- Sécurité API ---
    # Si définie, protège les endpoints qui coûtent de l'argent ou modifient des
    # données (scan manuel, watchlist, test de notifications) derrière un header
    # `X-API-Key`. Laissé vide par défaut pour ne pas casser un déploiement local
    # existant, mais un avertissement est loggé au démarrage si vide (voir main.py) —
    # à définir impérativement avant toute exposition publique de l'API (Vercel/Railway/etc).
    API_KEY: str = os.getenv("API_KEY", "")
    # Anti-abus : délai minimum (secondes) entre deux déclenchements manuels de scan
    # (POST /api/scan/run), qui hit Binance/Bybit/OKX/Hyperliquid + Claude/Grok si
    # configurés -> évite qu'un tiers (ou un script client bugué) ne fasse exploser
    # les coûts ou les limites de taux des exchanges en spammant cet endpoint.
    MANUAL_SCAN_COOLDOWN_SECONDS: int = int(os.getenv("MANUAL_SCAN_COOLDOWN_SECONDS", "60"))

    # --- Résumé macro/géopolitique (optionnel) ---
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

    # --- Catégorie 11 : Scalping IA (Grok) ---
    # ⚠️ Sans clé, la Catégorie 11 reste vide (le filtrage mécanique tourne quand
    # même, seule la notation finale Grok est indisponible -> score de repli local).
    GROK_API_KEY: str = os.getenv("GROK_API_KEY", "")
    GROK_MODEL: str = os.getenv("GROK_MODEL", "grok-4.3")  # xAI a retiré grok-4/grok-4-fast/grok-3
    # etc. le 15 mai 2026 -> grok-4.3 est le choix stable actuel (grok-4.5 est plus
    # récent mais restreint pour les comptes UE au moment de la rédaction). Vérifiez
    # le catalogue courant sur https://docs.x.ai/developers/models si les appels échouent.
    CATEGORY11_TOP_N_SYMBOLS: int = int(os.getenv("CATEGORY11_TOP_N_SYMBOLS", "155"))  # était 80
    CATEGORY11_MIN_QUOTE_VOLUME: float = float(os.getenv("CATEGORY11_MIN_QUOTE_VOLUME", "20000000"))
    CATEGORY11_EMA_PERIOD: int = int(os.getenv("CATEGORY11_EMA_PERIOD", "200"))  # EMA de tendance sur M15
    CATEGORY11_STOCH_OVERSOLD: float = float(os.getenv("CATEGORY11_STOCH_OVERSOLD", "20"))
    CATEGORY11_STOCH_OVERBOUGHT: float = float(os.getenv("CATEGORY11_STOCH_OVERBOUGHT", "80"))
    CATEGORY11_MIN_RVOL: float = float(os.getenv("CATEGORY11_MIN_RVOL", "1.4"))
    CATEGORY11_MIN_RR: float = float(os.getenv("CATEGORY11_MIN_RR", "1.2"))  # spec : minimum 1:1.2
    CATEGORY11_VWAP_PROXIMITY_PCT: float = float(os.getenv("CATEGORY11_VWAP_PROXIMITY_PCT", "1.0"))  # était 0.4
    CATEGORY11_OB_LOOKBACK: int = int(os.getenv("CATEGORY11_OB_LOOKBACK", "30"))  # bougies M1
    # Fenêtre (bougies M1) sur laquelle chercher le déclencheur (RVOL/bougie de
    # retournement/StochRSI extrême) -> "récemment", pas uniquement à l'instant T,
    # comme pour les Stratégies 1/2 et la Cat.9 (Fib)
    CATEGORY11_TRIGGER_WINDOW: int = int(os.getenv("CATEGORY11_TRIGGER_WINDOW", "5"))
    # Score minimum (/100) pour être éligible au top 5, même seuil que les autres
    # stratégies de scoring pondéré (Stratégies 1/2, Cat.9)
    CATEGORY11_MIN_SCORE: float = float(os.getenv("CATEGORY11_MIN_SCORE", "65"))
    # Si aucun candidat n'atteint CATEGORY11_MIN_SCORE, repli sur le top 5 des
    # scores entre CATEGORY11_FALLBACK_MIN_SCORE et CATEGORY11_MIN_SCORE
    CATEGORY11_FALLBACK_MIN_SCORE: float = float(os.getenv("CATEGORY11_FALLBACK_MIN_SCORE", "40"))

    # --- Filtre de liquidité minimale (appliqué à toutes les catégories) ---
    # Exclut les paires dont le volume 24h (en USDT) est inférieur à ce seuil,
    # même si elles font partie du TOP_N_SYMBOLS par volume.
    MIN_QUOTE_VOLUME_USDT: float = float(os.getenv("MIN_QUOTE_VOLUME_USDT", "5000000"))  # 5M$ par défaut

    # --- Zones de liquidation estimées (Catégorie 5) ---
    # Estimation heuristique (funding + open interest + niveaux de levier courants),
    # PAS un flux de liquidations réel : Binance ne fournit pas de heatmap de
    # liquidations en API publique.
    LIQUIDATION_LEVERAGE_LEVELS: list[int] = [10, 25, 50, 100]

    # --- Backtesting (suivi de performance des signaux Cat.1/Cat.2 passés) ---
    # Durée max de suivi d'un signal avant expiration automatique (statut "expired")
    BACKTEST_LOOKFORWARD_HOURS: int = int(os.getenv("BACKTEST_LOOKFORWARD_HOURS", "48"))
    # Horizon dédié pour la Cat.10 (GSB) : un breakout après compression de volatilité
    # (VSI) peut mettre plus de temps à se matérialiser qu'un signal de momentum pur
    # (Cat.1/Cat.2) -> horizon plus long pour ne pas classer "expired" des setups
    # encore valides.
    CATEGORY10_LOOKFORWARD_HOURS: int = int(os.getenv("CATEGORY10_LOOKFORWARD_HOURS", "72"))

    # --- Monitoring continu ---
    # Cycle indépendant des 5 scans quotidiens : réévalue les signaux en cours (backtest)
    # et détecte les cassures de range (Catégorie 2) en temps quasi-réel.
    MONITORING_INTERVAL_MINUTES: int = int(os.getenv("MONITORING_INTERVAL_MINUTES", "15"))

    # --- Fear & Greed Index (crypto) ---
    # Source publique et gratuite : alternative.me
    FEAR_GREED_API_URL: str = "https://api.alternative.me/fng/"

    # --- Marchés traditionnels (DXY, or, pétrole, indices, EUR/USD) ---
    # ⚠️ Source Yahoo Finance non-officielle (endpoint public utilisé communément,
    # mais non garanti/contractuel — à surveiller en production).
    YAHOO_FINANCE_CHART_URL: str = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    TRADITIONAL_MARKET_TICKERS: dict[str, str] = {
        "DXY (Dollar Index)": "DX-Y.NYB",
        "Or (Gold)": "GC=F",
        "Pétrole (WTI Crude)": "CL=F",
        "S&P 500": "^GSPC",
        "Nasdaq 100": "^NDX",
        "EUR/USD": "EURUSD=X",
    }

    # --- Fonctionnalités nécessitant une recherche web via Claude ---
    # Sans ANTHROPIC_API_KEY : ces sections restent vides avec un message explicite
    # (aucune donnée n'est inventée). Concerne : résumé macro (déjà existant),
    # pics d'activité sociale (Bonus Trading), flux ETF.
    AI_RESEARCH_MODEL: str = "claude-sonnet-5"

    # Cache dédié pour pics sociaux/flux ETF : ces données ne changent pas
    # toutes les 3h, contrairement aux prix. On ne les rafraîchit réellement qu'une
    # fois toutes les AI_RESEARCH_CACHE_HOURS heures, même si un scan a lieu 5x/jour
    # -> réduit fortement la consommation de crédits API Claude.
    AI_RESEARCH_CACHE_HOURS: int = int(os.getenv("AI_RESEARCH_CACHE_HOURS", "20"))
    # Garde-fou anti-dérive de coût : nombre max d'appels Claude (recherche web)
    # autorisés par jour calendaire (UTC), tous types confondus (pics
    # sociaux + flux ETF + résumé macro). Au-delà, la fonction retourne un résultat
    # vide/en cache plutôt que d'appeler l'API. Complète (ne remplace pas) le budget
    # de dépense à configurer sur console.anthropic.com.
    AI_RESEARCH_MAX_DAILY_CALLS: int = int(os.getenv("AI_RESEARCH_MAX_DAILY_CALLS", "15"))

    # --- Rétention de la base de données ---
    # Purge automatique des scans plus anciens que N jours (exécutée à chaque scan
    # planifié) pour éviter une croissance illimitée de la table des scans.
    DB_RETENTION_DAYS: int = int(os.getenv("DB_RETENTION_DAYS", "90"))

    # --- Alerte en cas d'échec complet d'un scan planifié ---
    # (indépendant des erreurs par symbole, déjà tolérées et listées dans errors[])
    NOTIFY_ON_SCAN_FAILURE: bool = _get_bool("NOTIFY_ON_SCAN_FAILURE", True)


settings = Settings()
