import asyncio
from decimal import Decimal
from typing import Dict, List, Optional

from solana.rpc.async_api import AsyncClient

from dex.base import SwapResult
from jupiter import JupiterClient
from logger import logger
from models import RiskConfig, SwapTrade
from network.solana import RPC_URL, USDC_MINT

logger = logger.bind(name="trade_executer")


class TradeExecuter:
    def __init__(
        self,
        rpc_url: str = RPC_URL,
        risk_config: Optional[RiskConfig] = None,
    ):
        self.rpc_url = rpc_url
        self.risk_config = risk_config or RiskConfig(
            max_trade_size_usd=Decimal("1000"),
            min_trade_size_usd=Decimal("10"),
            max_slippage_bps=100,
            max_portfolio_allocation=Decimal("0.25"),
            gas_buffer_sol=Decimal("0.1"),
            weight_tolerance=Decimal("0.02"),
            min_weight_threshold=Decimal("0.01"),
        )
        self.client = AsyncClient(rpc_url)
        self.max_slippage_bps = self.risk_config.max_slippage_bps
        self.jupiter_client = JupiterClient(rpc_url=self.rpc_url)
        self.wallet_address: Optional[str] = None
        self.wallet_private_key: Optional[str] = None

    def set_wallet_address(self, wallet_address: str):
        """Set wallet address for trade execution"""
        self.wallet_address = wallet_address

    def set_wallet_private_key(self, wallet_private_key: str):
        """Set wallet private key for trade execution"""
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
        return await self.jupiter_client.get_quote(
            input_mint=trade.from_mint,
            output_mint=trade.to_mint,
            amount=trade.from_amount_lamports,
            slippage_bps=self.max_slippage_bps,
        )

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
            prices: Dict[str, Decimal] = (
                await self.jupiter_client.get_token_price_by_ids([mint])
            )
            price = prices.get(mint)
            if price is not None:
                return price
            raise ValueError(f"Price not found for token {mint}")

        except Exception as e:
            logger.exception(f"Failed to get price for {mint}: {e}")
            raise  # Re-raise to let caller handle the error

    async def execute_trades(self, trades: List[SwapTrade]) -> List[SwapResult]:
        results = []
        for trade in trades:
            try:
                result = await self.execute_swap_with_retry(trade)
                if result.success:
                    logger.info(
                        f"Swapped {trade.from_symbol} for {trade.to_symbol} (${trade.usd_value}): {result.tx_signature}"
                    )
                    # トランザクションの確認を待つ
                    if result.tx_signature:
                        logger.info(
                            f"Waiting for transaction confirmation: {result.tx_signature}"
                        )
                        confirmed = await self.jupiter_client.wait_for_transaction(
                            result.tx_signature
                        )
                        if confirmed:
                            logger.info(f"Transaction confirmed: {result.tx_signature}")
                        else:
                            logger.error(
                                f"Transaction failed or timed out: {result.tx_signature}"
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
    trade_executer = TradeExecuter(RPC_URL)
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
    trade_executer.set_wallet_address(test_wallet_address)
    trade_executer.set_wallet_private_key(test_private_key)
    await trade_executer.execute_trades(trades)
    await trade_executer.close()


if __name__ == "__main__":
    asyncio.run(main())
