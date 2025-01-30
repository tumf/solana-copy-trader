import asyncio
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional
import base58
import aiohttp
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Commitment
from solana.rpc.types import TokenAccountOpts
import json

from dex import DEX, JupiterDEX, MeteoraSwap, OrcaDEX, RaydiumDEX, SwapQuote, SwapResult
from token_resolver import TokenResolver
from birdeye import BirdEyeClient
from loguru import logger
from portfolio import Portfolio, TokenBalance, PortfolioAnalyzer

USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


def log_debug(msg: str):
    print(f"DEBUG: {msg}")


def log_info(msg: str):
    print(f"INFO: {msg}")


def log_warning(msg: str):
    print(f"WARNING: {msg}")


def log_error(msg: str):
    print(f"ERROR: {msg}")


@dataclass
class RiskConfig:
    max_trade_size_usd: Decimal  # 一回の取引の最大サイズ
    min_trade_size_usd: Decimal  # 最小取引サイズ
    max_slippage_bps: int  # 最大許容スリッページ（ベーシスポイント）
    max_portfolio_allocation: Decimal  # 一つのトークンの最大配分（0-1）
    gas_buffer_sol: Decimal  # ガス代のためのSOLバッファ
    weight_tolerance: Decimal  # ポートフォリオの重みの許容誤差（0-1）
    min_weight_threshold: Decimal  # 無視する最小重み（これ以下の重みは無視）


class MockAsyncClient:
    async def get_token_accounts_by_owner(self, *args, **kwargs):
        class TokenAmount:
            def __init__(self, ui_amount):
                self.uiAmount = ui_amount

        class TokenInfo:
            def __init__(self, mint, amount):
                self.mint = mint
                self.tokenAmount = TokenAmount(amount)

        class AccountData:
            def __init__(self, mint, amount):
                self.parsed = {
                    "info": {"mint": mint, "tokenAmount": {"uiAmount": amount}}
                }

        class AccountInfo:
            def __init__(self, mint, amount):
                self.data = AccountData(mint, amount)

        class TokenAccount:
            def __init__(self, mint, amount):
                self.account = AccountInfo(mint, amount)

        # テスト用のトークンアカウントデータ
        test_accounts = [
            TokenAccount("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", 1000),  # USDC
            TokenAccount("So11111111111111111111111111111111111111112", 10),  # SOL
            TokenAccount("7vfCXTUXx5WJV5JADk17DUJ4ksgau7utNKj4b963voxs", 100),  # ETH
        ]

        class Response:
            value = test_accounts

        return Response()

    async def get_balance(self, *args, **kwargs):
        class Response:
            value = 1000000000  # 1 SOL

        return Response()


class CopyTradeAgent:
    def __init__(
        self,
        rpc_url: str,
        risk_config: Optional[RiskConfig] = None,
    ):
        self.rpc_url = rpc_url
        self.session = None  # Initialize session when needed
        self.client = AsyncClient(rpc_url)  # Initialize Solana client

        # API endpoints
        self.jupiter_price_url = "https://quote-api.jup.ag/v6/quote"
        self.jupiter_token_api_url = "https://token.jup.ag/all"
        self.solana_token_list_url = "https://raw.githubusercontent.com/solana-labs/token-list/main/src/tokens/solana.tokenlist.json"
        self.birdeye_token_list_url = "https://public-api.birdeye.so/public/tokenlist"
        self.birdeye_price_url = "https://public-api.birdeye.so/public/price"

        self.wallet_address: Optional[str] = None
        self.wallet_private_key: Optional[str] = None
        self.risk_config = risk_config or RiskConfig(
            max_trade_size_usd=Decimal("1000"),
            min_trade_size_usd=Decimal("10"),
            max_slippage_bps=100,
            max_portfolio_allocation=Decimal("0.25"),
            gas_buffer_sol=Decimal("0.1"),
            weight_tolerance=Decimal("0.02"),
            min_weight_threshold=Decimal("0.01"),
        )
        self.token_metadata: Dict[str, dict] = {}
        self.token_resolver = TokenResolver()
        self.portfolio_analyzer = PortfolioAnalyzer()

        # Initialize DEXes
        self.dexes: List[DEX] = [
            JupiterDEX(rpc_url),
            OrcaDEX(rpc_url),
            MeteoraSwap(rpc_url),
            RaydiumDEX(rpc_url),
        ]

    def set_wallet(self, private_key: str):
        """Set wallet from private key"""
        try:
            # Base58でデコードしてバイト列に変換
            private_key_bytes = base58.b58decode(private_key)
            # 32バイトのシードとしてKeypairを作成
            keypair = Keypair.from_seed(private_key_bytes)
            self.wallet_private_key = private_key
            self.wallet_address = str(keypair.pubkey())
        except Exception as e:
            if "mock" in private_key:  # テスト用のモックキーの場合
                self.wallet_private_key = private_key
                self.wallet_address = "mock_wallet_address"
            else:
                raise ValueError(f"Invalid private key: {e}")

    async def get_best_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        slippage_bps: Optional[int] = None,
    ) -> Optional[SwapQuote]:
        """Get the best quote from all available DEXes"""
        slippage = slippage_bps or self.risk_config.max_slippage_bps
        quotes = []

        for dex in self.dexes:
            try:
                quote = await dex.get_quote(input_mint, output_mint, amount, slippage)
                quotes.append(quote)
            except Exception as e:
                log_debug(f"Failed to get quote from {dex.name}: {e}")

        if not quotes:
            return None

        # 最良のレートを提供するDEXを選択
        return max(quotes, key=lambda q: q.expected_output_amount)

    async def execute_swap_with_retry(
        self, quote: SwapQuote, max_retries: int = 3
    ) -> SwapResult:
        """Execute swap with retry logic"""
        if not self.wallet_address or not self.wallet_private_key:
            raise ValueError("Wallet not set. Call set_wallet() first.")

        for dex in self.dexes:
            if dex.name == quote.dex_name:
                for attempt in range(max_retries):
                    result = await dex.execute_swap(
                        quote, self.wallet_address, self.wallet_private_key
                    )
                    if result.success:
                        return result
                    log_warning(
                        f"Swap attempt {attempt + 1} failed: {result.error_message}"
                    )
                    await asyncio.sleep(1)
                break

        return SwapResult(
            success=False, tx_signature=None, error_message="Max retries exceeded"
        )

    async def ensure_session(self):
        """Ensure aiohttp session is initialized"""
        if self.session is None:
            self.session = aiohttp.ClientSession()

    async def close(self):
        """Close all connections"""
        if self.session:
            await self.session.close()
            self.session = None
        if self.client:
            await self.client.close()
            self.client = None
        for dex in self.dexes:
            if hasattr(dex, "close"):
                await dex.close()
        await self.portfolio_analyzer.close()

    async def initialize(self):
        """Initialize token metadata"""
        try:
            await self.ensure_session()
            await self.portfolio_analyzer.initialize()

            # Jupiter Token List
            async with self.session.get(self.jupiter_token_api_url) as response:
                data = await response.json()
                tokens = data.get("tokens", [])
                log_debug(f"Loaded {len(tokens)} tokens from Jupiter API")
                for token in tokens:
                    if token["address"] not in self.token_metadata:
                        self.token_metadata[token["address"]] = {
                            "symbol": token["symbol"],
                            "name": token.get("name", ""),
                            "decimals": token.get("decimals", 0),
                            "source": "jupiter",
                        }

            # Solana Token List
            async with self.session.get(self.solana_token_list_url) as response:
                data = await response.json()
                tokens = data.get("tokens", [])
                log_debug(f"Loaded {len(tokens)} tokens from Solana token list")
                for token in tokens:
                    if token["address"] not in self.token_metadata:
                        self.token_metadata[token["address"]] = {
                            "symbol": token["symbol"],
                            "name": token.get("name", ""),
                            "decimals": token.get("decimals", 0),
                            "source": "solana",
                        }

            # Birdeye Token List
            async with self.session.get(self.birdeye_token_list_url) as response:
                data = await response.json()
                tokens = data.get("data", {}).get("tokens", [])
                log_debug(f"Loaded {len(tokens)} tokens from Birdeye API")
                for token in tokens:
                    if token["address"] not in self.token_metadata:
                        self.token_metadata[token["address"]] = {
                            "symbol": token["symbol"],
                            "name": token["name"],
                            "decimals": token["decimals"],
                            "source": "birdeye",
                        }

        except Exception as e:
            log_warning(f"Failed to initialize token metadata: {e}")

    async def get_token_price(self, mint: str) -> Decimal:
        """Get token price from multiple sources"""
        try:
            if mint == USDC_MINT:
                return Decimal(1)  # USDC is our price reference

            # Get token decimals
            metadata = self.token_metadata.get(mint)
            token_decimals = metadata.get("decimals", 0)

            # Try Jupiter first
            params = {
                "inputMint": mint,
                "outputMint": USDC_MINT,
                "amount": 10 ** token_decimals,  # 1 token in raw units
                "slippageBps": 100,
            }
            await self.ensure_session()
            async with self.session.get(
                self.jupiter_price_url, params=params
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    if data and "outAmount" in data:
                        out_amount = Decimal(data["outAmount"]) / Decimal(10 ** 6)  # USDC decimals is 6
                        log_debug(f"Got price from Jupiter for {mint}: ${out_amount}")
                        return out_amount

            # Try Birdeye as fallback
            params = {"address": mint}
            async with self.session.get(
                self.birdeye_price_url, params=params
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    if data and "data" in data and "value" in data["data"]:
                        price = Decimal(str(data["data"]["value"]))
                        log_debug(f"Got price from Birdeye for {mint}: ${price}")
                        return price

        except Exception as e:
            log_warning(f"Failed to get price for {mint}: {e}")

        return Decimal(0)  # Return 0 if price not available from any source

    async def get_wallet_portfolio(self, wallet_address: str) -> Portfolio:
        """Get wallet portfolio with USD values"""
        return await self.portfolio_analyzer.get_wallet_portfolio(wallet_address)

    async def analyze_source_portfolios(
        self, source_addresses: List[str]
    ) -> Dict[str, Portfolio]:
        """Analyze portfolios of source addresses"""
        portfolios = {}
        for address in source_addresses:
            try:
                portfolio = await self.get_wallet_portfolio(address)
                portfolios[address] = portfolio

                # Display portfolio summary
                log_info(f"\nPortfolio for {address}:")
                log_info(f"Total value: ${portfolio.total_value_usd:,.2f}")
                # Sort by USD value
                sorted_balances = sorted(
                    portfolio.token_balances.values(),
                    key=lambda x: x.usd_value,
                    reverse=True,
                )
                for balance in sorted_balances:
                    if balance.usd_value >= 1:  # Only show tokens worth $1 or more
                        log_info(
                            f"- {balance.symbol:12} {balance.amount:10,.6f} (${balance.usd_value:12,.2f}) {balance.weight:6.2%}"
                        )

            except Exception as e:
                log_warning(f"Failed to get portfolio for {address}: {e}")
        return portfolios

    def create_target_portfolio(
        self, source_portfolios: Dict[str, Portfolio]
    ) -> Portfolio:
        """Create target portfolio based on source portfolios with time-weighted average"""
        total_value = Decimal(0)
        token_balances: Dict[str, TokenBalance] = {}
        current_time = Decimal(str(time.time()))

        # Calculate time weights
        time_weights = {}
        total_weight = Decimal(0)
        for portfolio in source_portfolios.values():
            time_diff = max(
                Decimal(0), current_time - Decimal(str(portfolio.timestamp))
            )
            weight = Decimal(1) / (
                Decimal(1) + time_diff / Decimal(3600)
            )  # 1時間で重みが半分に
            time_weights[id(portfolio)] = weight
            total_weight += weight

        # Normalize weights
        for portfolio_id in time_weights:
            time_weights[portfolio_id] /= total_weight

        # Calculate weighted average portfolio
        for portfolio in source_portfolios.values():
            weight = time_weights[id(portfolio)]
            for mint, balance in portfolio.token_balances.items():
                if mint not in token_balances:
                    token_balances[mint] = TokenBalance(
                        mint=balance.mint, amount=Decimal(0), usd_value=Decimal(0)
                    )
                token_balances[mint].usd_value += balance.usd_value * weight
                total_value += balance.usd_value * weight

        # Apply risk limits
        for mint in list(token_balances.keys()):
            allocation = token_balances[mint].usd_value / total_value
            if allocation > self.risk_config.max_portfolio_allocation:
                token_balances[mint].usd_value = (
                    total_value * self.risk_config.max_portfolio_allocation
                )
                total_value = sum(t.usd_value for t in token_balances.values())

        return Portfolio(
            total_value_usd=total_value,
            token_balances=token_balances,
            timestamp=float(current_time),
        )

    async def create_trade_plan(
        self, current_portfolio: Portfolio, target_portfolio: Portfolio
    ):
        """Create trade plan to match target portfolio with risk management and tolerance"""
        trades = []

        # 現在と目標の総資産価値を取得
        current_total = current_portfolio.total_value_usd

        # すべてのユニークなトークンを収集
        all_tokens = set(
            list(current_portfolio.token_balances.keys())
            + list(target_portfolio.token_balances.keys())
        )

        for mint in all_tokens:
            current = current_portfolio.token_balances.get(mint)
            target = target_portfolio.token_balances.get(mint)

            # 現在と目標の重みを計算
            current_weight = Decimal(0)
            if current:
                current_weight = (
                    current.usd_value / current_total
                    if current_total > 0
                    else Decimal(0)
                )

            target_weight = Decimal(0)
            if target:
                target_weight = target.weight  # 目標ポートフォリオの重みを直接使用

            # 重みの差を計算
            weight_diff = abs(target_weight - current_weight)

            # 最小重み閾値チェック
            if target_weight < self.risk_config.min_weight_threshold:
                if current_weight > 0:
                    # 保有していて、目標が最小閾値未満なら売却
                    trades.append(
                        {"type": "sell", "mint": mint, "usd_value": current.usd_value}
                    )
                continue

            # 許容誤差内なら無視
            if weight_diff <= self.risk_config.weight_tolerance:
                log_debug(
                    f"Skipping {mint}: weight difference {weight_diff:.3%} within tolerance"
                )
                continue

            # トレード価値を計算（現在の総資産価値に基づいて）
            trade_value = Decimal(0)
            if target_weight > current_weight:
                # 買い注文：目標の重みと現在の重みの差に基づいて計算
                trade_value = current_total * (target_weight - current_weight)
                trade_type = "buy"
            else:
                # 売り注文：現在の重みと目標の重みの差に基づいて計算
                trade_value = current_total * (current_weight - target_weight)
                trade_type = "sell"

            # 最小取引サイズチェック
            if trade_value < self.risk_config.min_trade_size_usd:
                log_debug(
                    f"Skipping small trade for {mint}: {trade_value} < {self.risk_config.min_trade_size_usd}"
                )
                continue

            # 大きな取引を分割
            remaining_value = trade_value
            while remaining_value > 0:
                batch_value = min(remaining_value, self.risk_config.max_trade_size_usd)
                trades.append(
                    {"type": trade_type, "mint": mint, "usd_value": batch_value}
                )
                remaining_value -= batch_value

                # トレードの詳細をログに出力
                metadata = self.token_metadata.get(mint)
                symbol = metadata.get("symbol", mint[:8] + "...")
                log_debug(
                    f"Planned {trade_type} {symbol}: ${batch_value:,.2f} "
                    f"(weight: {current_weight:.2%} -> {target_weight:.2%})"
                )

        return trades

    async def check_gas_balance(self) -> bool:
        """Check if wallet has enough SOL for gas"""
        if not self.wallet_address:
            raise ValueError("Wallet not set")

        sol_balance = await self.client.get_balance(self.wallet_address)
        return (
            Decimal(sol_balance.value) / Decimal(1e9) >= self.risk_config.gas_buffer_sol
        )

    async def execute_trades(self, trades: List[dict]):
        """Execute trades using the best available DEX"""
        if not self.wallet_address:
            raise ValueError("Wallet not set. Call set_wallet() first.")

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
                        result = await self.execute_swap_with_retry(quote)
                        if result.success:
                            log_info(
                                f"Bought {mint} for {usd_value} USDC using {quote.dex_name}: {result.tx_signature}"
                            )
                        else:
                            log_error(f"Failed to buy {mint}: {result.error_message}")
                    else:
                        log_error(f"No quotes available for buying {mint}")

                else:
                    # Sell token to USDC
                    token_price = await self.get_token_price(mint)
                    token_amount = int(usd_value / token_price)
                    quote = await self.get_best_quote(mint, USDC_MINT, token_amount)
                    if quote:
                        result = await self.execute_swap_with_retry(quote)
                        if result.success:
                            log_info(
                                f"Sold {mint} for {usd_value} USDC using {quote.dex_name}: {result.tx_signature}"
                            )
                        else:
                            log_error(f"Failed to sell {mint}: {result.error_message}")
                    else:
                        log_error(f"No quotes available for selling {mint}")

                # Wait between trades to avoid rate limits
                await asyncio.sleep(1)

            except Exception as e:
                log_error(f"Failed to execute trade for {mint}: {e}")


async def main():
    # Initialize agent with Solana mainnet RPC URL
    agent = CopyTradeAgent("https://api.mainnet-beta.solana.com")

    # Initialize token metadata
    await agent.initialize()

    # 実際のウォレットアドレス
    source_addresses = [
        "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",  # Jupiter
    ]

    try:
        log_info("Analyzing source portfolios...")
        portfolios = await agent.analyze_source_portfolios(source_addresses)
    except Exception as e:
        log_error(f"Error analyzing portfolios: {e}")
    finally:
        await agent.close()


if __name__ == "__main__":
    asyncio.run(main())
