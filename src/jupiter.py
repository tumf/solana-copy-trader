import asyncio
from decimal import Decimal
from typing import Dict, List

import aiohttp
from loguru import logger

# Jupiter API limits
MAX_IDS_PER_REQUEST = 100
logger = logger.bind(name="jupiter")


class JupiterClient:
    def __init__(self):
        self.session = None
        self.price_url = "https://api.jup.ag/price/v2"
        self.headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

    async def initialize(self):
        """Initialize Jupiter client"""
        await self.ensure_session()

    async def ensure_session(self):
        """Ensure aiohttp session is initialized"""
        if self.session is None:
            self.session = aiohttp.ClientSession(headers=self.headers)
        return self.session

    async def close(self):
        """Close the session"""
        if self.session:
            await self.session.close()
            await asyncio.sleep(0.1)  # Give time for the session to close properly
            self.session = None

    async def get_token_prices(self, mints: List[str]) -> Dict[str, Decimal]:
        """Get token prices from Jupiter API in batches

        Args:
            mints: List of token mint addresses

        Note:
            Jupiter API has a limit of 100 token IDs per request.
            This method automatically handles batching for large lists of tokens.
        """
        if not mints:
            return {}
        prices = {}

        session = await self.ensure_session()
        for i in range(0, len(mints), MAX_IDS_PER_REQUEST):
            batch = mints[i : i + MAX_IDS_PER_REQUEST]
            try:
                # Use single ids parameter with comma-separated values
                url = f"{self.price_url}?ids={','.join(batch)}"

                async with session.get(url) as response:
                    if response.status == 429:  # Rate limit
                        logger.warning(
                            "Rate limited by Jupiter API, waiting 10 seconds"
                        )
                        await asyncio.sleep(10)
                        continue

                    if response.status != 200:
                        logger.error(
                            f"Error from Jupiter API: {response.status} - {await response.text()}"
                        )
                        continue

                    data = await response.json()
                    if data and "data" in data:
                        for mint, price_data in data["data"].items():
                            if price_data and "price" in price_data:
                                prices[mint] = Decimal(str(price_data["price"]))
                            else:
                                logger.debug(f"No price data for {mint}")

            except Exception as e:
                logger.error(f"Error fetching prices for batch: {e}")
                continue

            await asyncio.sleep(0.1)  # Rate limiting

        return prices
