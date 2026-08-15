"""
Marchés traditionnels — DXY, or, pétrole, S&P 500, Nasdaq 100, EUR/USD.

⚠️ IMPORTANT : utilise l'endpoint public (non-officiel) de Yahoo Finance
(query1.finance.yahoo.com). C'est un endpoint largement utilisé par la
communauté (ex: librairie yfinance) mais NON contractuel : il peut changer
ou être temporairement indisponible sans préavis. Pour un usage production
critique, remplacez par un fournisseur payant (Alpha Vantage, Twelve Data,
Polygon.io...).

Pour chaque actif, on calcule des niveaux techniques (support/résistance sur
20 séances, moyennes mobiles 20/50) et on classe le comportement récent en
"Compression" (resserrement avant mouvement), "Accumulation" (proche du
support, volume stable), "Prise de profit" (proche de la résistance, RSI
suracheté) ou "Zone neutre".
"""
import asyncio
import logging

import httpx
import numpy as np
import pandas as pd

from .config import settings
from .indicators import rsi as compute_rsi
from .models import TraditionalMarketAsset

logger = logging.getLogger("traditional_markets")


async def _fetch_yahoo_chart(client: httpx.AsyncClient, ticker: str) -> pd.DataFrame | None:
    url = settings.YAHOO_FINANCE_CHART_URL.format(ticker=ticker)
    try:
        resp = await client.get(
            url,
            params={"range": "6mo", "interval": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        data = resp.json()
        result = data["chart"]["result"][0]
        quote = result["indicators"]["quote"][0]
        df = pd.DataFrame(
            {
                "close": quote["close"],
                "high": quote["high"],
                "low": quote["low"],
                "volume": quote.get("volume", [None] * len(quote["close"])),
            }
        ).dropna(subset=["close"])
        return df
    except Exception as e:
        logger.warning(f"Yahoo Finance indisponible pour {ticker}: {e}")
        return None


def _classify(df: pd.DataFrame) -> tuple[str, dict]:
    close = df["close"]
    high20 = df["high"].tail(20).max()
    low20 = df["low"].tail(20).min()
    ma20 = close.rolling(20).mean().iloc[-1]
    ma50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else None
    rsi_series = compute_rsi(close, 14)
    rsi_val = float(rsi_series.iloc[-1])
    last = float(close.iloc[-1])

    range_width_pct = (high20 - low20) / last * 100
    range_history = (
        (df["high"].rolling(20).max() - df["low"].rolling(20).min()) / close * 100
    ).dropna()
    is_compressed = (
        len(range_history) >= 30 and range_width_pct <= range_history.quantile(0.25)
    )
    near_resistance = last >= high20 * 0.98
    near_support = last <= low20 * 1.02

    if is_compressed and 40 <= rsi_val <= 60:
        classification = "Compression"
    elif near_resistance and rsi_val > 65:
        classification = "Prise de profit"
    elif near_support and rsi_val < 45:
        classification = "Accumulation"
    else:
        classification = "Zone neutre"

    return classification, {
        "support": round(float(low20), 4),
        "resistance": round(float(high20), 4),
        "ma20": round(float(ma20), 4) if not np.isnan(ma20) else None,
        "ma50": round(float(ma50), 4) if ma50 is not None and not np.isnan(ma50) else None,
        "rsi": round(rsi_val, 2),
    }


async def fetch_all_traditional_markets() -> list[TraditionalMarketAsset]:
    results: list[TraditionalMarketAsset] = []
    async with httpx.AsyncClient(timeout=10) as client:
        tasks = {
            name: _fetch_yahoo_chart(client, ticker)
            for name, ticker in settings.TRADITIONAL_MARKET_TICKERS.items()
        }
        dfs = await asyncio.gather(*tasks.values())

    for (name, ticker), df in zip(settings.TRADITIONAL_MARKET_TICKERS.items(), dfs):
        if df is None or len(df) < 25:
            results.append(
                TraditionalMarketAsset(
                    name=name, ticker=ticker, price=None, change_pct_1d=None,
                    support=None, resistance=None, ma20=None, ma50=None, rsi=None,
                    classification="Indisponible",
                )
            )
            continue
        try:
            classification, levels = _classify(df)
            last = float(df["close"].iloc[-1])
            prev = float(df["close"].iloc[-2])
            change_pct = round((last / prev - 1) * 100, 2)
            results.append(
                TraditionalMarketAsset(
                    name=name, ticker=ticker, price=round(last, 4), change_pct_1d=change_pct,
                    classification=classification, **levels,
                )
            )
        except Exception as e:
            logger.warning(f"Erreur classification {name}: {e}")
            results.append(
                TraditionalMarketAsset(
                    name=name, ticker=ticker, price=None, change_pct_1d=None,
                    support=None, resistance=None, ma20=None, ma50=None, rsi=None,
                    classification="Indisponible",
                )
            )
    return results
