import os
from decimal import Decimal
from typing import Dict, Optional

import aiohttp
from loguru import logger
from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey


class BirdEyeClient:
    BASE_URL = "https://public-api.birdeye.so"
    API_KEY = os.getenv("BIRDEYE_API_KEY", "")
    RPC_URL = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com")

    def __init__(self):
        if not self.API_KEY:
            logger.warning("BIRDEYE_API_KEY not set. Some features may be limited.")

        self._session: Optional[aiohttp.ClientSession] = None
        self._solana: Optional[AsyncClient] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "X-API-KEY": self.API_KEY,
                    "Accept": "application/json",
                    "x-chain": "solana",
                }
            )
        return self._session

    async def _get_solana(self) -> AsyncClient:
        if self._solana is None:
            self._solana = AsyncClient(self.RPC_URL)
        return self._solana

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
        if self._solana:
            await self._solana.close()

    async def get_token_price(self, token_address: str) -> Decimal:
        """Get token price in USD"""
        session = await self._get_session()
        url = f"{self.BASE_URL}/defi/price"
        params = {
            "address": token_address,
        }

        try:
            async with session.get(url, params=params) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise ValueError(
                        f"Failed to get price (HTTP {response.status}): {error_text}"
                    )

                data = await response.json()
                if not data.get("success"):
                    raise ValueError(f"API error: {data.get('message')}")

                price = data.get("data", {}).get("value")
                if price is None:
                    raise ValueError(
                        f"No price data available for token {token_address}"
                    )

                return Decimal(str(price))
        except (aiohttp.ClientError, ValueError) as e:
            logger.exception(f"Failed to get price for token {token_address}: {e}")
            raise

    async def get_token_metadata(self, token_address: str) -> Dict:
        """Get token metadata from SPL Token Program"""
        solana = await self._get_solana()
        token_pubkey = Pubkey.from_string(token_address)

        # Get token account info
        account_info = await solana.get_account_info(token_pubkey)
        if not account_info.value:
            raise Exception(f"Token account not found: {token_address}")

        # Parse token metadata
        data = account_info.value.data
        decimals = data[44] if len(data) >= 45 else 0

        # Get token metadata from account data
        metadata = {
            "address": token_address,
            "decimals": decimals,
        }

        try:
            # Try to get additional metadata from the token metadata program
            metadata_pda = await self._find_metadata_pda(token_pubkey)
            metadata_info = await solana.get_account_info(metadata_pda)

            if metadata_info.value:
                # Get metadata from the token metadata program
                metadata_data = metadata_info.value.data
                if len(metadata_data) > 0:
                    # Skip discriminator and update authority
                    offset = 1 + 32

                    # Get mint address
                    offset += 32

                    # Get name
                    name_len = int.from_bytes(
                        metadata_data[offset : offset + 4], byteorder="little"
                    )
                    offset += 4
                    name = (
                        metadata_data[offset : offset + name_len]
                        .decode("utf-8")
                        .rstrip("\x00")
                    )
                    offset += name_len

                    # Get symbol
                    symbol_len = int.from_bytes(
                        metadata_data[offset : offset + 4], byteorder="little"
                    )
                    offset += 4
                    symbol = (
                        metadata_data[offset : offset + symbol_len]
                        .decode("utf-8")
                        .rstrip("\x00")
                    )

                    metadata.update(
                        {
                            "name": name,
                            "symbol": symbol,
                        }
                    )
        except Exception as e:
            logger.exception(f"Failed to get additional metadata: {e}")

        return metadata

    async def _find_metadata_pda(self, mint: Pubkey) -> Pubkey:
        """Find the metadata PDA for a token mint"""
        metadata_program_id = Pubkey.from_string(
            "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s"
        )
        seeds = [
            b"metadata",
            bytes(metadata_program_id),
            bytes(mint),
        ]
        pda, _ = Pubkey.find_program_address(seeds, metadata_program_id)
        return pda
