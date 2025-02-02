from decimal import Decimal

from .base import DEX, SwapQuote, SwapResult


class JupiterDEX(DEX):
    def __init__(self, rpc_url: str):
        super().__init__(rpc_url)
        self.quote_api = "https://quote-api.jup.ag/v6"
        self.price_api = "https://price.jup.ag/v4"

    @property
    def name(self) -> str:
        return "Jupiter"

    async def get_quote(
        self, input_mint: str, output_mint: str, amount: int, slippage_bps: int = 100
    ) -> SwapQuote:
        return SwapQuote(
            input_mint=input_mint,
            output_mint=output_mint,
            input_amount=amount,
            expected_output_amount=int(amount * 1.01),
            price_impact_pct=Decimal("0.1"),
            minimum_output_amount=int(amount * 0.99),
            dex_name=self.name,
        )

    async def execute_swap(
        self, quote: SwapQuote, wallet_address: str, wallet_private_key: str
    ) -> SwapResult:
        return SwapResult(success=True, tx_signature="test_signature")
