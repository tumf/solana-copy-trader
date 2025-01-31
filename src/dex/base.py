import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from models import SwapQuote, SwapResult
import aiohttp



class MockAsyncClient:
    async def send_transaction(self, *args, **kwargs):
        class Response:
            value = "test_signature"

        return Response()


class DEX(ABC):
    def __init__(self, rpc_url: str):
        self.rpc_url = rpc_url
        self.client = MockAsyncClient()
        self.session = None
        self.headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

    async def ensure_session(self):
        """Ensure aiohttp session is initialized"""
        if self.session is None:
            self.session = aiohttp.ClientSession(headers=self.headers)
        return self.session

    async def close(self):
        """Close all connections"""
        if self.session:
            await self.session.close()
            await asyncio.sleep(0.1)  # Give time for the session to close properly
            self.session = None

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
