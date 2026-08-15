"""
Crypto Fear & Greed Index — source: alternative.me (API publique, gratuite, sans clé).
Documentation : https://alternative.me/crypto/fear-and-greed-index/
"""
import logging
from datetime import datetime, timezone

import httpx

from .config import settings
from .models import FearGreedData

logger = logging.getLogger("fear_greed")


async def fetch_fear_greed() -> FearGreedData | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(settings.FEAR_GREED_API_URL, params={"limit": 1})
            resp.raise_for_status()
            payload = resp.json()
        entry = payload["data"][0]
        return FearGreedData(
            value=int(entry["value"]),
            classification=entry["value_classification"],
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as e:
        logger.warning(f"Impossible de récupérer le Fear & Greed Index: {e}")
        return None
