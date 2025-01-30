from decimal import Decimal
from typing import Dict, List
import aiohttp
from loguru import logger
import asyncio

# Jupiter API limits
MAX_IDS_PER_REQUEST = 100


class JupiterClient:
    def __init__(self):
        self.price_url = "https://api.jup.ag/price/v2"
        self.session = None
        self.headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

    async def ensure_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession(headers=self.headers)
        return self.session

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None

    async def get_token_prices(self, mints: List[str]) -> Dict[str, float]:
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
                                prices[mint] = float(price_data["price"])
                            else:
                                logger.debug(f"No price data for {mint}")

            except Exception as e:
                logger.error(f"Error fetching prices for batch: {e}")
                continue

            await asyncio.sleep(0.1)  # Rate limiting

        return prices

    async def get_token_price_by_ids(self, mint_ids: List[str]) -> Dict[str, Decimal]:
        """Get token prices from Jupiter with rate limit handling"""
        try:
            # Prepare request parameters
            params = {
                "ids": ",".join(mint_ids),
            }

            max_retries = 3
            retry_delay = 2  # Initial delay in seconds

            for attempt in range(max_retries):
                try:
                    logger.debug(
                        f"Fetching Jupiter prices for {len(mint_ids)} tokens (attempt {attempt + 1}/{max_retries})"
                    )
                    await self.ensure_session()
                    async with self.session.get(
                        self.price_url, params=params
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            if data and "data" in data:
                                prices = {}
                                for mint, price_data in data["data"].items():
                                    if price_data and "price" in price_data:
                                        prices[mint] = Decimal(str(price_data["price"]))
                                        logger.debug(
                                            f"Got price from Jupiter for {mint}: ${prices[mint]}"
                                        )
                                return prices
                            logger.debug(f"No price data from Jupiter: {data}")
                            break
                        elif response.status == 429:  # Rate limit exceeded
                            response_text = await response.text()
                            logger.debug(
                                f"Jupiter rate limit exceeded: {response_text}"
                            )
                            if attempt < max_retries - 1:
                                delay = retry_delay * (
                                    2**attempt
                                )  # Exponential backoff
                                logger.debug(f"Waiting {delay} seconds before retry...")
                                await asyncio.sleep(delay)
                            continue
                        else:
                            response_text = await response.text()
                            logger.debug(
                                f"Jupiter API returned status {response.status}: {response_text}"
                            )
                            break

                except Exception as e:
                    logger.warning(f"Error fetching Jupiter prices: {e}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
                    continue

        except Exception as e:
            logger.warning(f"Failed to get prices: {e}")

        return {}  # Return empty dict if prices not available
