from decimal import Decimal
from typing import Dict, List

from jupiter import JupiterClient
from logger import logger
from network.solana import RPC_URL

logger = logger.bind(name="token_price_resolver")


class TokenPriceResolver:
    def __init__(self, rpc_url: str = RPC_URL):
        self.rpc_url = rpc_url
        self.jupiter_client = JupiterClient(rpc_url=rpc_url)

    async def initialize(self):
        """Initialize price resolver"""
        await self.jupiter_client.initialize()

    async def close(self):
        """Close all connections"""
        await self.jupiter_client.close()

    async def get_token_prices(self, mints: List[str]) -> Dict[str, Decimal]:
        """Get token price from Jupiter"""
        # Get price from Jupiter
        prices = await self.jupiter_client.get_token_prices(mints)
        return prices
