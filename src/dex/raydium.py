from decimal import Decimal

from .base import DEX, SwapQuote, SwapResult


class RaydiumDEX(DEX):
    def __init__(self, rpc_url: str):
        super().__init__(rpc_url)
        self.api_url = "https://api.raydium.io/v2"

    @property
    def name(self) -> str:
        return "Raydium"

    async def get_quote(
        self, input_mint: str, output_mint: str, amount: int, slippage_bps: int = 100
    ) -> SwapQuote:
        # Return mock data for testing
        return SwapQuote(
            input_mint=input_mint,
            output_mint=output_mint,
            input_amount=amount,
            expected_output_amount=int(amount * 1.007),  # 0.7% of premium
            price_impact_pct=Decimal("0.13"),
            minimum_output_amount=int(amount * 0.987),  # 1.3% of slippage
            dex_name=self.name,
        )

    async def execute_swap(
        self, quote: SwapQuote, wallet_address: str, wallet_private_key: str
    ) -> SwapResult:
        # Return success result for testing
        return SwapResult(success=True, tx_signature="test_signature")
