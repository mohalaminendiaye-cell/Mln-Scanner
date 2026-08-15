# MLN Scan

Application de scan automatique du marché crypto (Binance Futures USDT-M),
exécutée 3 fois par jour, à **08h45, 13h15 et 00h15, heure de
Dakar (GMT, sans changement d'heure)**.

## 1. Architecture

```
crypto-scanner/
├── backend/                  FastAPI + APScheduler + SQLite
│   └── app/
│       ├── main.py           Endpoints REST
│       ├── config.py         Paramètres (.env)
│       ├── binance_client.py Client Binance Futures (retry / rate-limit)
│       ├── indicators.py     ATR, RSI, MACD, Bollinger, Choppiness Index
│       ├── scanner.py        Scoring + sélection des catégories 1 & 2
│       ├── notifications.py  Telegram / Discord / Email
│       ├── database.py       Historique des scans (SQLite)
│       └── scheduler.py      Cron 08h45/13h15/00h15 (Africa/Dakar)
└── frontend/                 React + Vite (dashboard)
    └── src/
        ├── App.jsx           Layout principal
        └── components/       AssetCard, ScanResults, History
```

**Flux** : le scheduler déclenche `run_scan()` aux heures définies →
récupération des ~250 paires USDT perpétuelles les plus liquides → calcul
des indicateurs H1 + H4 pour chacune → scoring → sélection top 5 par
catégorie → sauvegarde en base → notifications → disponible via l'API pour
le frontend.

## 2. Formules exactes utilisées

- **True Range** : `TR = max(H-L, |H-C_prev|, |L-C_prev|)`
- **ATR (Wilder)** : `ATR = EMA(TR, période, alpha=1/période)`
- **RSI (Wilder)** : `RSI = 100 - 100/(1 + moyenne_gains/moyenne_pertes)`
- **MACD** : `EMA12 - EMA26`, signal = `EMA9(MACD)`
- **Bollinger** : `Upper/Lower = SMA(close,20) ± 2*stdev(close,20)`
- **Choppiness Index (H4)** :
  `CHOP(n) = 100 * log10( Σ TR(n) / (PlusHaut(n) - PlusBas(n)) ) / log10(n)`
  avec n = 14. CHOP > 60 = marché en range / erratique.

Détail complet des poids de scoring dans les docstrings de `scanner.py`.

## 3. Catégories de résultats

- **Catégorie 1** : top 5 paires par score composite (volatilité ATR,
  volume anormal, momentum RSI/MACD, squeeze release, funding extrême,
  divergence vs BTC), direction Long/Short déterminée par la tendance
  (EMA20/EMA50 + MACD).
- **Catégorie 2** : paires avec CHOP(H4) > 60, classées par potentiel de
  breakout (compression des bandes de Bollinger + volume en déclin).
- **Catégorie 4 — Corrélation BTC** : altcoins dont la variation 24h diverge le plus de
  celle de BTC, signe d'un catalyseur propre à l'actif plutôt qu'un effet de marché global.

Chaque signal (Cat.1/2/4) inclut : déclencheur, direction, entrée, stop loss, take
profit, ratio R:R (setups < 1:2 exclus), et un mini-graphique (sparkline) des 24
dernières clôtures H1.

### Catégorie 7 — multi-exchange Bybit + OKX ("Signaux Techniques")

⚠️ **Intégration OKX non testée en conditions réelles** : mon environnement de
développement n'a pas d'accès internet vers okx.com. Le code suit la
documentation publique officielle de l'exchange, mais **vérifiez sérieusement
le premier scan en production** (logs + `errors[]` du résultat de scan).
Désactivable via `OKX_ENABLED` en cas de problème.

Architecture : ces données sont interrogées en **REST à chaque scan**, pas via
WebSocket persistant — cohérent avec le fonctionnement de l'app par scans
programmés + monitoring périodique plutôt qu'un flux continu.

- **Catégorie 7 — Mouvements imminents (4h)** : Top 5 par exchange, sur **Bybit
  et OKX**, sur compression de volatilité + volume anormal + sursaut d'Open
  Interest. Fenêtre resserrée à 4h (cible de mouvement ~2.5% au lieu de 5% sur
  24h, cohérent avec l'horizon plus court). Score affiché comme en Catégorie 1.

### Catégorie 9 — Stratégie Fib (Binance + Bybit, "Top Movers")

- **Catégorie 9 — Stratégie Fib** : Top 5 par exchange, sur **Binance et
  Bybit**. Combine Fibonacci (retracement 0.50 exact ou zone "Golden Pocket"
  0.618-0.786 d'une impulsion majeure H4, `FIBO_LOOKBACK_CANDLES` bougies —
  filtre bloquant, identité de la stratégie) avec un score de confluence
  pondéré sur 100 pts : Market Structure/CHoCH (20 pts, H1), Liquidity Sweep
  (15 pts, H1), Volume Profile POC/VAH/VAL (20 pts, H4), VWAP (15 pts, H1),
  Delta/CVD (15 pts, H1) et Footprint — proxy candle-based, pas un vrai order
  flow tick-by-tick (15 pts, H1). Seules les paires avec un score ≥
  `FIB_MIN_SCORE` (65/100 par défaut) sont retenues, avec repli automatique
  sur le top 5 des scores 40-65 si aucune ne passe le seuil
  (`FIB_FALLBACK_MIN_SCORE`). Deux sous-listes (retracement_050 /
  golden_pocket) par exchange. Voir `multi_exchange_scanner.py` et
  `indicators.py` (`delta_series`, `cvd_series`, `footprint_pressure`) pour le
  détail des méthodes et leurs limites documentées.

Chaque signal Cat.7/9 inclut en plus : tendance du volume vs moyenne mobile,
Open Interest (+ variation 24h si l'exchange la fournit — OKX ne l'expose pas
nativement contrairement à Binance), spread bid-ask, et des zones de
liquidation ⚠️ **estimées** (heuristique funding + niveaux de levier courants,
pas un flux réel).

#### Évoluer vers du WebSocket (piste future)

L'architecture actuelle (scans programmés + polling REST) convient très bien
à un usage "scan périodique". Pour du vrai temps réel (ex: alerte immédiate
dès qu'un spread explose ou qu'une liquidation approche), il faudrait un
service séparé qui maintient des connexions WebSocket permanentes vers
OKX/Hyperliquid, met à jour un cache en mémoire (ou Redis), et déclenche des
alertes indépendamment du cycle de scan — une évolution architecturale plus
lourde, à envisager seulement si le besoin de latence sub-minute se confirme.

### Catégorie 10 — Global Breakout Score (Binance Futures + Bybit Futures)

Positionnée directement **au-dessus** de la Catégorie 9. ⚠️ **Intégration Bybit
non testée en conditions réelles** (mêmes réserves que OKX/Hyperliquid).
Désactivable via `BYBIT_ENABLED=false`.

Combine 5 facteurs (0-100 chacun) en un score global GSB :
`GSB = 0.25×VSI + 0.20×RVOL + 0.25×OIFD + 0.15×MSD + 0.15×CORR`. Seules les
paires avec **GSB ≥ 60** sont retenues, top 5 toutes exchanges confondues
(pas un top 5 par exchange, contrairement aux Cat.7/8). Le Take Profit du plan
de trade est **dynamique, basé sur l'ATR** (`GSB_TP_ATR_MULTIPLIER × ATR`, 3×
par défaut) plutôt qu'un objectif en % fixe, pour rester proportionnel à la
volatilité réelle de chaque actif. Ces signaux sont suivis en backtest avec un
horizon dédié plus long (`CATEGORY10_LOOKFORWARD_HOURS`, 72h par défaut) que
Cat.1/Cat.2, car un breakout post-compression met souvent plus de temps à se
matérialiser qu'un signal de momentum pur.

- **VSI** (Volatility Squeeze Index) : compression Bollinger(20,2) vs
  Keltner(20,1.5), percentile de l'ATR récent vs son historique, volatilité
  de Garman-Klass, et volume qui recommence à monter ("énergie potentielle").
- **RVOL** (Relative Volume & Flow Imbalance) : ratio de volume vs moyenne 20
  périodes, CVD estimé (⚠️ approximation candle-based : `volume × signe(close-open)`,
  **pas** un vrai CVD tick-by-tick), déséquilibre du carnet d'ordres à ±2%.
- **OIFD** (Open Interest & Funding Disparity) : croissance d'OI disproportionnée
  par rapport au mouvement de prix + funding rate extrême.
- **MSD** (Market Structure & Key Level Distance) : distance au niveau clé le
  plus proche parmi VWAP glissant, plus haut/bas 7 jours, et POC/VAH/VAL
  (⚠️ Volume Profile calculé sur les bougies disponibles, proxy des "poches de
  liquidité" — pas un carnet d'ordres agrégé historique réel).
- **CORR** (BTC Beta & Correlation) : bêta vs BTC + divergence par rapport au
  rendement attendu selon ce bêta (détecte la déconnexion/surperformance).

### Catégorie 11 — Scalping IA (Grok)

Stratégie de scalping mécanique en 4 étapes sur Binance Futures (M1/M15),
notée et expliquée par Grok (xAI) une fois les setups déjà validés :

1. **Filtre (M15)** : prix au-dessus de l'EMA200 pour un Long, en dessous
   pour un Short — on ne trade que dans le sens de la tendance globale.
2. **Point d'ancrage (M1)** : pullback sur le VWAP (bandes ±1 écart-type) ou
   un Order Block (dernière bougie opposée avant l'impulsion).
3. **Déclencheur (M1)** : sursaut de volume (RVOL ≥ `CATEGORY11_MIN_RVOL`),
   bougie de retournement (engulfing ou marteau/étoile filante), et
   Stochastique RSI en zone de survente/surachat cohérente.
4. **Exécution** : entrée limite sur la zone, Stop-Loss serré sous/sur le
   dernier creux/sommet, R:R minimum `CATEGORY11_MIN_RR` (1:1.2 par défaut).

⚠️ **Rôle de Grok** : toute la détection ci-dessus est **mécanique**
(déterministe, calculée sur les vraies données OHLCV) — un LLM n'est pas
fiable pour calculer un EMA ou un Stochastique RSI. Grok intervient
uniquement **après**, sur les setups déjà validés : il reçoit leurs
métriques exactes et donne une note de confiance (0-100) + une explication
courte. Sans `GROK_API_KEY`, ou si l'appel échoue, un **score de repli 100%
local** est calculé à partir des mêmes métriques et c'est indiqué
explicitement dans le résultat (`[Score local, Grok indisponible]`) plutôt
que d'inventer une réponse. ⚠️ xAI fait évoluer régulièrement ses noms de
modèles — vérifier `GROK_MODEL` sur https://docs.x.ai si les appels échouent.

## 4. Fonctionnalités additionnelles

- **Watchlist** (`GET/POST/DELETE /api/watchlist`) : suit n'importe quel symbole avec son
  prix live, indépendamment des scans programmés.
- **Suivi de performance / backtest léger** (`GET /api/backtest/stats`, `GET
  /api/backtest/recent`, `GET /api/backtest/categories`) : chaque signal Cat.1/Cat.2/
  Cat.10 est réévalué automatiquement (cycle de monitoring) pour déterminer si le Take
  Profit ou le Stop Loss a été touché en premier, avec expiration après
  `BACKTEST_LOOKFORWARD_HOURS` (48h, Cat.1/Cat.2) ou `CATEGORY10_LOOKFORWARD_HOURS`
  (72h, Cat.10) sans issue. Les signaux Cat.10 ouverts sur Bybit sont suivis via le
  client Bybit dédié, ceux sur Binance via le client Binance. L'onglet Performance
  permet de **filtrer par catégorie et par période** (jour/semaine/mois/tout) et de
  **cliquer sur un trade** pour voir son prix d'entrée, son Stop Loss et son Take
  Profit exacts (`entry`, `stop_loss`, `take_profit` déjà stockés en base, exposés
  tels quels par l'API — aucune donnée supplémentaire nécessaire).
- **Alertes de breakout en temps réel** : un cycle de monitoring indépendant
  (`MONITORING_INTERVAL_MINUTES`, 15 min par défaut) détecte si un actif de la Catégorie 2
  a cassé sa borne de range depuis le dernier scan et envoie une alerte immédiate.
- **Filtre de liquidité minimale** : `MIN_QUOTE_VOLUME_USDT` (5M$ par défaut) exclut les
  paires trop peu liquides, même si elles seraient sinon dans le top N par volume.

## 5. Exemple de sortie

Voir `example_output.json` à la racine du projet.

## 6. Déploiement

### Option A — Docker Compose (recommandé)

```bash
cp backend/.env.example backend/.env
# éditer backend/.env : ajouter vos tokens Telegram/Discord/SMTP si besoin
docker compose up -d --build
```

- Backend : http://localhost:8000 (docs Swagger sur `/docs`)
- Frontend : http://localhost:5173

### Option B — Manuel

```bash
# Backend
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload

# Frontend (autre terminal)
cd frontend
npm install
echo "VITE_API_URL=http://localhost:8000" > .env
npm run dev
```

### Notifications

Renseigner dans `.env` :
- `NOTIFY_TELEGRAM=true` + `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`
- `NOTIFY_DISCORD=true` + `DISCORD_WEBHOOK_URL`
- `NOTIFY_EMAIL=true` + variables `SMTP_*` et `EMAIL_TO`

### Déploiement production

- Backend : conteneur Docker derrière un reverse-proxy (nginx/Caddy) avec
  HTTPS, ou hébergement type Railway/Render (le scheduler tourne en
  arrière-plan tant que le process est actif — utiliser un plan "worker
  toujours actif", pas serverless).
- Frontend : build statique (`npm run build`) servable sur Vercel/Netlify,
  en pointant `VITE_API_URL` vers l'URL du backend.
- Base de données : SQLite convient pour un usage mono-instance ; migrer
  vers PostgreSQL (`DATABASE_URL=postgresql://...`) si montée en charge.

### Tester comme application sur iPhone (PWA)

L'app est une PWA (Progressive Web App) : installable "Sur l'écran d'accueil"
depuis Safari, sans Xcode ni App Store, avec icône, lancement plein écran et
splash screen comme une vraie app.

**Prérequis : le PC et l'iPhone doivent être sur le même réseau Wi-Fi.**

1. Trouver l'adresse IP locale du PC :
   - **Windows** (PowerShell) : `ipconfig` → repérer "Adresse IPv4"
     (ex: `192.168.1.50`)
   - **Mac/Linux** : `ifconfig | grep "inet "` ou `ip a`
2. Lancer l'app normalement (`docker compose up -d --build`).
3. Vérifier que le pare-feu Windows autorise les ports 5173 et 8000 en
   entrée (sinon l'iPhone ne pourra pas joindre le PC) :
   ```powershell
   New-NetFirewallRule -DisplayName "MLN Scan Frontend" -Direction Inbound -LocalPort 5173 -Protocol TCP -Action Allow
   New-NetFirewallRule -DisplayName "MLN Scan Backend" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow
   ```
4. Sur l'iPhone, ouvrir **Safari** (obligatoire — Chrome iOS ne permet pas
   l'installation PWA) et aller sur `http://<IP-DU-PC>:5173` (ex:
   `http://192.168.1.50:5173`).
5. Bouton **Partager** (carré avec flèche vers le haut) → **Sur l'écran
   d'accueil** → **Ajouter**.
6. L'icône "MLN Scan" apparaît sur l'écran d'accueil ; elle s'ouvre en plein
   écran, sans barre d'adresse Safari, comme une app native.

L'app détecte automatiquement l'IP du PC pour joindre l'API backend (pas de
configuration manuelle nécessaire — voir `frontend/src/api.js`). Si tu
modifies le code, il suffit de relancer `docker compose up -d --build
frontend` puis de refermer/rouvrir l'app sur l'iPhone (le service worker se
met à jour automatiquement).

⚠️ Cette PWA n'est PAS publiée sur l'App Store — elle reste accessible
uniquement tant que le PC (le backend) est allumé et sur le même réseau.
Pour un accès depuis n'importe où (4G/5G), il faudrait déployer le backend
sur un serveur public (cf. section déploiement production ci-dessus) et
pointer l'app dessus.

## 7. Ajouter un nouveau filtre/indicateur

1. Ajouter la fonction de calcul dans `indicators.py`.
2. L'appeler dans `enrich_dataframe()` pour qu'elle soit disponible sur
   chaque ligne du DataFrame H1 (ou dans `run_scan()` pour du H4).
3. L'intégrer dans `_score_category1` ou `_score_category2` avec son
   propre poids, en gardant la somme des poids égale à 1.

## 8. Optimisations appliquées

- **Coûts IA maîtrisés** : les fonctionnalités basées sur Claude + recherche web
  (pics sociaux, flux ETF) sont mises en cache `AI_RESEARCH_CACHE_HOURS`
  heures (20h par défaut) — un seul appel réel par jour environ, même si le scan
  tourne 5x/jour. Un plafond `AI_RESEARCH_MAX_DAILY_CALLS` (15 par défaut)
  protège contre toute dérive. **Configurez en complément un budget de dépense
  sur [console.anthropic.com](https://console.anthropic.com).**
- **Surveillance du rate limit Binance** : le poids d'API utilisé
  (`X-MBX-USED-WEIGHT-1M`) est journalisé, avec avertissement au-delà de
  1000/1200 — réduisez `TOP_N_SYMBOLS` si ce message apparaît souvent.
  Avec `TOP_N_SYMBOLS=250` (valeur par défaut), un scan complet analyse
  ~1250 appels API (5 par symbole) répartis avec `MAX_CONCURRENT_REQUESTS=8` :
  comptez quelques minutes par scan, surveillez les logs les premiers jours.
- **Anti-chevauchement scan/monitoring** : un verrou partagé empêche un scan
  complet et un cycle de monitoring de tourner simultanément (évite de cumuler
  la charge sur le rate limit Binance).
- **Purge automatique de la base** : les scans de plus de `DB_RETENTION_DAYS`
  jours (90 par défaut) sont supprimés à chaque scan planifié — l'historique de
  performance (backtest) n'est PAS purgé, il reste léger et utile long terme.
- **Alerte dédiée en cas d'échec complet d'un scan** (`NOTIFY_ON_SCAN_FAILURE`),
  distincte des erreurs par symbole déjà tolérées.
- **Sécurité** : `.gitignore` fourni pour ne jamais committer `.env` ; un
  avertissement est loggué au démarrage si `ALLOWED_ORIGINS=*` en production.
- **Transparence UX** : le frontend interroge `GET /api/config` et affiche une
  bannière si `ANTHROPIC_API_KEY` n'est pas configurée, pour ne pas laisser
  penser que les sections IA vides sont un bug.

## 9. Gestion des erreurs

- Requêtes Binance : retry avec backoff exponentiel (`tenacity`), gestion
  spécifique des codes 429/418 (pause selon `Retry-After`).
- Une paire qui échoue individuellement (données insuffisantes, erreur
  API) est ignorée et journalisée dans `errors[]`, sans interrompre le
  scan global.
