from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class SwapQuote:
    input_mint: str
    output_mint: str
    input_amount: int
    expected_output_amount: int
    price_impact_pct: Decimal
    minimum_output_amount: int
    dex_name: str


@dataclass
class SwapResult:
    success: bool
    tx_signature: Optional[str]
    error_message: Optional[str] = None


class MockAsyncClient:
    async def send_transaction(self, *args, **kwargs):
        class Response:
            value = "test_signature"

        return Response()


class DEX(ABC):
    def __init__(self, rpc_url: str):
        self.rpc_url = rpc_url
        self.client = MockAsyncClient()

    @property
    @abstractmethod
    def name(self) -> str:
        """Get DEX name"""
        pass

    @abstractmethod
    async def get_quote(
        self, input_mint: str, output_mint: str, amount: int, slippage_bps: int = 100
    ) -> SwapQuote:
        """Get swap quote"""
        pass

    @abstractmethod
    async def execute_swap(
        self, quote: SwapQuote, wallet_address: str, wallet_private_key: str
    ) -> SwapResult:
        """Execute swap"""
        pass
