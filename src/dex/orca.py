from decimal import Decimal

from .base import DEX, SwapQuote, SwapResult


class OrcaDEX(DEX):
    def __init__(self, rpc_url: str):
        super().__init__(rpc_url)
        self.api_url = "https://api.orca.so"

    @property
    def name(self) -> str:
        return "Orca"

    async def get_quote(
        self, input_mint: str, output_mint: str, amount: int, slippage_bps: int = 100
    ) -> SwapQuote:
        # テスト用のモックデータを返す
        return SwapQuote(
            input_mint=input_mint,
            output_mint=output_mint,
            input_amount=amount,
            expected_output_amount=int(amount * 1.005),  # 0.5%のプレミアム
            price_impact_pct=Decimal("0.15"),
            minimum_output_amount=int(amount * 0.985),  # 1.5%のスリッページ
            dex_name=self.name,
        )

    async def execute_swap(
        self, quote: SwapQuote, wallet_address: str, wallet_private_key: str
    ) -> SwapResult:
        # テスト用に成功を返す
        return SwapResult(success=True, tx_signature="test_signature")
