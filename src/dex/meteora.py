from decimal import Decimal

from .base import DEX, SwapQuote, SwapResult


class MeteoraAPI:
    def __init__(self):
        self.base_url = "https://api.meteora.ag/v1"

    def get_quote(self, params: dict) -> dict:
        # テスト用のモックデータを返す
        return {
            "outAmount": str(int(params["amount"]) * 1.008),  # 0.8%のプレミアム
            "priceImpact": "0.12",
        }

    def get_swap_transaction(self, data: dict) -> dict:
        # テスト用のモックデータを返す
        return {
            "transaction": "test_transaction",
        }


class MeteoraSwap(DEX):
    def __init__(self, rpc_url: str):
        super().__init__(rpc_url)
        self.api = MeteoraAPI()

    @property
    def name(self) -> str:
        return "Meteora"

    async def get_quote(
        self, input_mint: str, output_mint: str, amount: int, slippage_bps: int = 100
    ) -> SwapQuote:
        params = {
            "inToken": input_mint,
            "outToken": output_mint,
            "amount": str(amount),
            "slippage": slippage_bps / 10000,  # Convert bps to decimal
        }
        data = self.api.get_quote(params)

        return SwapQuote(
            input_mint=input_mint,
            output_mint=output_mint,
            input_amount=amount,
            expected_output_amount=int(float(data["outAmount"])),
            price_impact_pct=Decimal(str(data.get("priceImpact", 0))) * 100,
            minimum_output_amount=int(
                float(data["outAmount"]) * (1 - slippage_bps / 10000)
            ),
            dex_name=self.name,
        )

    async def execute_swap(
        self, quote: SwapQuote, wallet_address: str, wallet_private_key: str
    ) -> SwapResult:
        # テスト用に成功を返す
        return SwapResult(success=True, tx_signature="test_signature")
