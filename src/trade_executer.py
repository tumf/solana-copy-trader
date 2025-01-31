import asyncio
from decimal import Decimal
from typing import List, Optional, Dict
import logging

from solana.rpc.async_api import AsyncClient
from solders.pubkey import Pubkey  # type: ignore

from dex.base import SwapQuote, SwapResult
from jupiter import JupiterClient
from logger import logger
from models import RiskConfig, SwapTrade, Token
from network import USDC_MINT
import aiohttp

logger = logger.bind(name="trade_executer")


class TradeExecuter:
    def __init__(self, rpc_url: str, risk_config: RiskConfig):
        self.rpc_url = rpc_url
        self.risk_config = risk_config
        self.client = AsyncClient(rpc_url)
        self.max_slippage_bps = risk_config.max_slippage_bps
        self.jupiter_client = JupiterClient(rpc_url=self.rpc_url)
        self.wallet_address = None
        self.wallet_private_key = None

    def set_wallet_address(self, wallet_address: str):
        self.wallet_address = Pubkey.from_string(wallet_address)

    def set_wallet_private_key(self, wallet_private_key: str):
        self.wallet_private_key = wallet_private_key

    async def initialize(self):
        """Initialize trade executer"""
        await self.jupiter_client.initialize()

    async def close(self):
        """Close all connections"""
        if self.client:
            await self.client.close()
            await asyncio.sleep(0.1)  # Give time for the session to close properly
            self.client = None
        if self.jupiter_client:
            await self.jupiter_client.close()
            await asyncio.sleep(0.1)  # Give time for the session to close properly
            self.jupiter_client = None

    async def get_quote(self, trade: SwapTrade) -> dict:
        """Get best quote for a swap"""
        # Validate addresses - Base58 encoded Solana addresses are 44 chars or less
        if len(trade.from_mint) > 44 or len(trade.to_mint) > 44:
            error_msg = (
                f"Invalid token address for {trade.from_symbol} -> {trade.to_symbol}"
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Get quote from Jupiter API
        params = {
            "inputMint": trade.from_mint,
            "outputMint": trade.to_mint,
            "amount": trade.from_amount_lamports,
            "slippageBps": self.max_slippage_bps,
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(
                self.jupiter_client.quote_url + "/quote", params=params
            ) as response:
                if response.status != 200:
                    error_msg = f"Failed to get quote: {response.status} {await response.text()}"
                    logger.error(error_msg)
                    raise ValueError(error_msg)
                return await response.json()

    async def execute_swap_with_retry(self, trade: SwapTrade) -> SwapResult:
        try:
            amount_lamports = int(trade.from_amount * 10**trade.from_decimals)

            # Get quote
            quote = await self.jupiter_client.get_quote(
                input_mint=trade.from_mint,
                output_mint=trade.to_mint,
                amount=amount_lamports,
            )
            if not quote:
                return SwapResult(
                    success=False,
                    tx_signature=None,
                    error_message="Failed to get quote",
                )

            # Execute swap
            result = await self.jupiter_client.execute_swap(
                quote=quote,
                wallet_address=self.wallet_address,
                wallet_private_key=self.wallet_private_key,
            )

            if result.success:
                return result
            else:
                return SwapResult(
                    success=False,
                    tx_signature=None,
                    error_message=result.error_message,
                )

        except Exception as e:
            return SwapResult(
                success=False,
                tx_signature=None,
                error_message=str(e),
            )

    async def get_token_price(self, mint: str) -> Decimal:
        """Get token price from Jupiter"""
        if mint == USDC_MINT:
            return Decimal(1)  # USDC is our price reference

        try:
            # Try Jupiter
            prices = await self.jupiter_client.get_token_price_by_ids([mint])
            if mint in prices:
                return prices[mint]
            raise ValueError(f"Price not found for token {mint}")

        except Exception as e:
            logger.exception(f"Failed to get price for {mint}: {e}")
            raise  # Re-raise to let caller handle the error

    async def execute_trades(self, trades: List[SwapTrade]) -> List[SwapResult]:
        results = []
        for trade in trades:
            try:
                quote = await self.get_quote(trade)
                result = await self.execute_swap_with_retry(trade)
                if result.success:
                    logger.info(
                        f"Swapped {trade.from_symbol} for {trade.to_symbol} (${trade.usd_value}): {result.tx_signature}"
                    )
                else:
                    raise RuntimeError(
                        f"Failed to execute trade {trade.from_symbol} -> {trade.to_symbol}: {result.error_message}"
                    )
                results.append(result)
            except Exception as e:
                logger.exception(
                    f"Failed to execute trade {trade.from_symbol} -> {trade.to_symbol}: {str(e)}"
                )
                results.append(
                    SwapResult(
                        success=False,
                        tx_signature=None,
                        error_message=str(e),
                    )
                )
        return results


async def main():
    # Initialize trade executer with Solana mainnet RPC URL
    trade_executer = TradeExecuter(
        "https://api.mainnet-beta.solana.com", RiskConfig(max_slippage_bps=100)
    )
    await trade_executer.initialize()

    # Example trades
    trades = [
        SwapTrade(
            type="swap",
            from_symbol="SOL",
            from_mint="So11111111111111111111111111111111111111112",
            from_amount=Decimal("0.1"),
            from_decimals=9,
            to_symbol="USDC",
            to_mint=USDC_MINT,
            to_amount=Decimal("2"),
            to_decimals=6,
            usd_value=Decimal("2"),
        ),
    ]
    # Note: You need to set these variables before running this example
    test_wallet_address = "your_wallet_address"
    test_private_key = "your_private_key"
    await trade_executer.execute_trades(trades, test_wallet_address, test_private_key)
    await trade_executer.close()


if __name__ == "__main__":
    asyncio.run(main())
