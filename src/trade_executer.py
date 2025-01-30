import asyncio
from decimal import Decimal
from typing import Dict, List, Optional
from solana.rpc.async_api import AsyncClient
from dex import DEX, JupiterDEX, MeteoraSwap, OrcaDEX, RaydiumDEX, SwapQuote, SwapResult
from jupiter import JupiterClient
from logger import logger

logger = logger.bind(name="trade_executer")

USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


class TradeExecuter:
    def __init__(self, rpc_url: str, max_slippage_bps: int = 100):
        self.rpc_url = rpc_url
        self.client = AsyncClient(rpc_url)
        self.max_slippage_bps = max_slippage_bps
        self.jupiter_client = JupiterClient()

        # Initialize DEXes
        self.dexes: List[DEX] = [
            JupiterDEX(rpc_url),
            OrcaDEX(rpc_url),
            MeteoraSwap(rpc_url),
            RaydiumDEX(rpc_url),
        ]

    async def initialize(self):
        """Initialize trade executer"""
        await self.jupiter_client.initialize()
        for dex in self.dexes:
            if hasattr(dex, "initialize"):
                await dex.initialize()

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
        for dex in self.dexes:
            if hasattr(dex, "close"):
                await dex.close()
                await asyncio.sleep(0.1)  # Give time for the session to close properly
        self.dexes = []

    async def get_best_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: Optional[int] = None,
    ) -> Optional[SwapQuote]:
        """Get the best quote from all available DEXes"""
        slippage = slippage_bps or self.max_slippage_bps
        quotes = []

        for dex in self.dexes:
            try:
                quote = await dex.get_quote(input_mint, output_mint, amount, slippage)
                quotes.append(quote)
            except Exception as e:
                logger.debug(f"Failed to get quote from {dex.name}: {e}")

        if not quotes:
            return None

        # 最良のレートを提供するDEXを選択
        return max(quotes, key=lambda q: q.expected_output_amount)

    async def execute_swap_with_retry(
        self, quote: SwapQuote, wallet_address: str, wallet_private_key: str, max_retries: int = 3
    ) -> SwapResult:
        """Execute swap with retry logic"""
        for dex in self.dexes:
            if dex.name == quote.dex_name:
                for attempt in range(max_retries):
                    result = await dex.execute_swap(
                        quote, wallet_address, wallet_private_key
                    )
                    if result.success:
                        return result
                    logger.warning(
                        f"Swap attempt {attempt + 1} failed: {result.error_message}"
                    )
                    await asyncio.sleep(1)
                break

        return SwapResult(
            success=False, tx_signature=None, error_message="Max retries exceeded"
        )

    async def get_token_price(self, mint: str) -> Decimal:
        """Get token price from Jupiter"""
        try:
            if mint == USDC_MINT:
                return Decimal(1)  # USDC is our price reference

            # Try Jupiter
            prices = await self.jupiter_client.get_token_price_by_ids([mint])
            if mint in prices:
                return prices[mint]

        except Exception as e:
            logger.warning(f"Failed to get price for {mint}: {e}")

        return Decimal(0)  # Return 0 if price not available

    async def execute_trades(self, trades: List[dict], wallet_address: str, wallet_private_key: str):
        """Execute trades using the best available DEX"""
        for trade in trades:
            try:
                mint = trade["mint"]
                usd_value = trade["usd_value"]

                if trade["type"] == "buy":
                    # Buy token using USDC
                    usdc_amount = int(
                        usd_value * Decimal("1000000")
                    )  # USDC has 6 decimals
                    quote = await self.get_best_quote(USDC_MINT, mint, usdc_amount)
                    if quote:
                        result = await self.execute_swap_with_retry(quote, wallet_address, wallet_private_key)
                        if result.success:
                            logger.info(
                                f"Bought {mint} for {usd_value} USDC using {quote.dex_name}: {result.tx_signature}"
                            )
                        else:
                            logger.error(
                                f"Failed to buy {mint}: {result.error_message}"
                            )
                    else:
                        logger.error(f"No quotes available for buying {mint}")

                else:
                    # Sell token to USDC
                    token_price = await self.get_token_price(mint)
                    token_amount = int(usd_value / token_price)
                    quote = await self.get_best_quote(mint, USDC_MINT, token_amount)
                    if quote:
                        result = await self.execute_swap_with_retry(quote, wallet_address, wallet_private_key)
                        if result.success:
                            logger.info(
                                f"Sold {mint} for {usd_value} USDC using {quote.dex_name}: {result.tx_signature}"
                            )
                        else:
                            logger.error(
                                f"Failed to sell {mint}: {result.error_message}"
                            )
                    else:
                        logger.error(f"No quotes available for selling {mint}")

                # Wait between trades to avoid rate limits
                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Failed to execute trade for {mint}: {e}") 