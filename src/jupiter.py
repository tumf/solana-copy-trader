import asyncio
import base64
from decimal import Decimal
from typing import Dict, List, Optional

import aiohttp
from loguru import logger
from solders.keypair import Keypair  # type: ignore
from solders.message import to_bytes_versioned  # type: ignore
from solders.transaction import VersionedTransaction  # type: ignore

from models import SwapResult

# Jupiter API limits
MAX_IDS_PER_REQUEST = 100
logger = logger.bind(name="jupiter")


class JupiterClient:
    def __init__(self, rpc_url: Optional[str] = None):
        self.rpc_url = rpc_url
        self.session = None
        self.price_url = "https://api.jup.ag/price/v2"
        self.quote_url = "https://api.jup.ag/swap/v1"
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

    async def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: Decimal,
        slippage_bps: int = 100,
    ) -> Optional[Dict]:
        """Get quote from Jupiter API

        Args:
            input_mint: Input token mint address
            output_mint: Output token mint address
            amount: Amount of input token in lamports/smallest unit
            slippage_bps: Slippage tolerance in basis points (default: 1%)

        Returns:
            Quote data if successful, None otherwise
        """
        session = await self.ensure_session()
        try:
            url = f"{self.quote_url}/quote"
            params = {
                "inputMint": input_mint,  # Already a string from resolve_address
                "outputMint": output_mint,  # Already a string from resolve_address
                "amount": str(int(amount)),  # Convert to string to avoid precision loss
                "slippageBps": slippage_bps,
            }

            async with session.get(url, params=params) as response:
                if response.status == 429:  # Rate limit
                    logger.warning("Rate limited by Jupiter API, waiting 10 seconds")
                    await asyncio.sleep(10)
                    return None

                if response.status != 200:
                    logger.error(
                        f"Error from Jupiter API: {response.status} - {await response.text()}"
                    )
                    return None

                data = await response.json()
                return data

        except Exception as e:
            logger.exception(f"Error getting quote: {e}")
            return None

    async def build_swap_transaction(
        self, quote: Dict, wallet_address: str
    ) -> Optional[Dict]:
        try:
            session = await self.ensure_session()
            # Create transaction
            url = f"{self.quote_url}/swap"
            payload = {
                "userPublicKey": str(wallet_address),
                "wrapAndUnwrapSol": True,
                "useSharedAccounts": True,
                "quoteResponse": quote,
                "dynamicComputeUnitLimit": True,
                "skipUserAccountsRpcCalls": True,
                "dynamicSlippage": True,
            }

            async with session.post(url, json=payload) as response:
                if response.status == 429:  # Rate limit
                    logger.warning("Rate limited by Jupiter API, waiting 10 seconds")
                    await asyncio.sleep(10)
                    return None

                if response.status != 200:
                    logger.error(
                        f"Error from Jupiter API: {response.status} - {await response.text()}"
                    )
                    return None

                data = await response.json()
                return data

        except Exception as e:
            logger.exception(f"Error executing swap: {e}")
            return None

    async def sign_and_send_transaction(
        self, build_tx: Dict, wallet_private_key: str
    ) -> Optional[Dict]:
        """Sign and send transaction using Solana RPC

        Args:
            build_tx: Transaction data from build_swap_transaction
            wallet_private_key: Wallet private key for signing

        Returns:
            Transaction data if successful, None otherwise
        """
        try:
            # Decode transaction from base64
            tx_bytes = base64.b64decode(build_tx["swapTransaction"])
            transaction = VersionedTransaction.from_bytes(tx_bytes)

            # Create keypair from private key
            keypair = Keypair.from_base58_string(wallet_private_key)

            # Get message bytes and sign
            message_bytes = to_bytes_versioned(transaction.message)
            signature = keypair.sign_message(message_bytes)

            # Create signed transaction using populate
            signed_tx = VersionedTransaction.populate(transaction.message, [signature])

            # Send signed transaction to Solana node
            session = await self.ensure_session()
            url = self.rpc_url
            if not url:
                url = "https://api.mainnet-beta.solana.com"

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [
                    base64.b64encode(bytes(signed_tx)).decode("utf-8"),
                    {
                        "skipPreflight": True,
                        "maxRetries": 2,
                        "encoding": "base64",
                    },
                ],
            }

            async with session.post(url, json=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(
                        f"Error from Solana RPC: {response.status} - {error_text}"
                    )
                    return None

                data = await response.json()
                if "error" in data:
                    logger.error(f"Error from Solana RPC: {data['error']}")
                    return None

                return {"success": True, "txid": data["result"], "error": None}

        except Exception as e:
            logger.exception(f"Error signing and sending transaction: {e}")
            return {"success": False, "txid": None, "error": str(e)}

    async def execute_swap(
        self,
        quote: Dict,
        wallet_address: str,
        wallet_private_key: str,
    ) -> Optional[Dict]:
        """Execute swap using Jupiter API

        Args:
            quote: Dict object
            wallet_address: Wallet address for signing
            wallet_private_key: Wallet private key for signing

        Returns:
            SwapResult object
        """
        try:
            # Build transaction
            build_tx = await self.build_swap_transaction(quote, wallet_address)
            if not build_tx:
                return SwapResult(
                    success=False,
                    tx_signature=None,
                    error_message="Failed to build transaction",
                )

            # Sign and send transaction
            result = await self.sign_and_send_transaction(build_tx, wallet_private_key)
            if not result:
                return SwapResult(
                    success=False,
                    tx_signature=None,
                    error_message="Failed to sign and send transaction",
                )

            return SwapResult(
                success=result["success"],
                tx_signature=result["txid"],
                error_message=result.get("error"),
            )

        except Exception as e:
            logger.exception(f"Error executing swap: {e}")
            return SwapResult(success=False, tx_signature=None, error_message=str(e))
