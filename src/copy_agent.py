import asyncio
import time
from decimal import Decimal
from typing import Dict, List, Optional
import base58
from solders.keypair import Keypair  # type: ignore
from solana.rpc.async_api import AsyncClient
import os

from token_resolver import TokenResolver
from portfolio import Portfolio, TokenBalance, PortfolioAnalyzer
from trade_planner import TradePlanner, RiskConfig
from trade_executer import TradeExecuter
from logger import logger
from network import USDC_MINT, TOKEN_PROGRAM_ID

logger = logger.bind(name="copy_agent")


class CopyTradeAgent:
    def __init__(
        self,
        rpc_url: str,
        risk_config: Optional[RiskConfig] = None,
    ):
        self.rpc_url = rpc_url
        self.client = AsyncClient(rpc_url)

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

        self.token_resolver = TokenResolver()
        self.portfolio_analyzer = PortfolioAnalyzer()
        self.trade_planner = TradePlanner(self.risk_config)
        self.trade_executer = TradeExecuter(rpc_url, self.risk_config.max_slippage_bps)

    def set_wallet_address(self, wallet_address: str):
        """Set wallet address for planning"""
        self.wallet_address = wallet_address

    def set_wallet_private_key(self, private_key: str):
        """Set wallet private key for trade execution"""
        try:
            private_key_bytes = base58.b58decode(private_key)
            keypair = Keypair.from_seed(private_key_bytes)
            self.wallet_private_key = private_key
            self.wallet_address = str(keypair.pubkey())
        except Exception as e:
            raise ValueError(f"Invalid private key: {e}")

    async def close(self):
        """Close all connections"""
        if self.client:
            await self.client.close()
            self.client = None
        await self.portfolio_analyzer.close()
        await self.trade_executer.close()

    async def initialize(self):
        """Initialize components"""
        await self.portfolio_analyzer.initialize()
        await self.trade_executer.initialize()

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
                logger.info(f"Portfolio for {address}:")
                logger.info(f"Total value: ${portfolio.total_value_usd:,.2f}")
                sorted_balances = sorted(
                    portfolio.token_balances.values(),
                    key=lambda x: float(x.usd_value),
                    reverse=True,
                )
                for balance in sorted_balances:
                    if float(balance.usd_value) >= 1:
                        logger.info(
                            f"- {balance.symbol:12} {balance.amount:10,.6f} (${balance.usd_value:12,.2f}) {balance.weight:6.2%}"
                        )

            except Exception as e:
                logger.warning(f"Failed to get portfolio for {address}: {e}")
        return portfolios

    def create_target_portfolio(
        self, source_portfolios: Dict[str, Portfolio]
    ) -> Portfolio:
        """Create target portfolio based on source portfolios with time-weighted average"""
        token_balances: Dict[str, TokenBalance] = {}
        current_time = Decimal(str(time.time()))
        total_value = Decimal("0")

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
                        mint=balance.mint,
                        amount=Decimal(0),
                        decimals=balance.decimals,
                        usd_value=Decimal(0),
                        symbol=balance.symbol,
                    )
                token_balances[mint].usd_value += balance.usd_value * weight
                token_balances[mint].amount += balance.amount * weight
                total_value += balance.usd_value * weight

        # Apply max allocation limit
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
        return await self.trade_planner.create_trade_plan(current_portfolio, target_portfolio)

    async def check_gas_balance(self) -> bool:
        """Check if wallet has enough SOL for gas"""
        if not self.wallet_address:
            raise ValueError("Wallet address not set")

        sol_balance = await self.client.get_balance(self.wallet_address)
        return (
            Decimal(sol_balance.value) / Decimal(1e9) >= self.risk_config.gas_buffer_sol
        )

    async def execute_trades(self, trades: List[dict]):
        """Execute trades using the best available DEX"""
        if not self.wallet_address or not self.wallet_private_key:
            raise ValueError(
                "Wallet private key not set. Call set_wallet_private_key() first."
            )

        await self.trade_executer.execute_trades(
            trades, self.wallet_address, self.wallet_private_key
        )


async def main():
    # Initialize agent with Solana mainnet RPC URL
    agent = CopyTradeAgent("https://api.mainnet-beta.solana.com")

    # Set wallet from environment variables
    if private_key := os.getenv("WALLET_PRIVATE_KEY"):
        agent.set_wallet_private_key(private_key)
    elif wallet_address := os.getenv("WALLET_ADDRESS"):
        agent.set_wallet_address(wallet_address)
    else:
        raise ValueError("Either WALLET_PRIVATE_KEY or WALLET_ADDRESS must be set")

    # Initialize components
    await agent.initialize()

    try:
        # Get current portfolio
        logger.info("Getting current portfolio...")
        current_portfolio = await agent.get_wallet_portfolio(agent.wallet_address)
        logger.info(
            f"Current portfolio value: ${current_portfolio.total_value_usd:,.2f}"
        )
        sorted_balances = sorted(
            current_portfolio.token_balances.values(),
            key=lambda x: float(x.usd_value),
            reverse=True,
        )
        for balance in sorted_balances:
            if float(balance.usd_value) >= 1:
                logger.info(
                    f"- {balance.symbol:12} {balance.amount:10,.6f} (${balance.usd_value:12,.2f}) {balance.weight:6.2%}"
                )

        # Get source portfolio
        logger.info("Analyzing source portfolio...")
        source_addresses = [
            "MfDuWeqSHEqTFVYZ7LoexgAK9dxk7cy4DFJWjWMGVWa",
        ]
        portfolios = await agent.analyze_source_portfolios(source_addresses)

        # Create target portfolio
        target_portfolio = agent.create_target_portfolio(portfolios)
        logger.info(f"Target portfolio value: ${target_portfolio.total_value_usd:,.2f}")
        sorted_balances = sorted(
            target_portfolio.token_balances.values(),
            key=lambda x: float(x.usd_value),
            reverse=True,
        )
        for balance in sorted_balances:
            if float(balance.usd_value) >= 1:
                logger.info(
                    f"- {balance.symbol:12} {balance.amount:10,.6f} (${balance.usd_value:12,.2f}) {balance.weight:6.2%}"
                )

        # Create trade plan
        logger.info("Creating trade plan...")
        trades = await agent.create_trade_plan(current_portfolio, target_portfolio)
        logger.info(f"Generated {len(trades)} trades")
        for trade in trades:
            if trade["type"] == "swap":
                logger.info(
                    f"- swap {trade['from_symbol']:12} -> {trade['to_symbol']:12} "
                    f"{trade['from_amount']:10,.6f} -> {trade['to_amount']:10,.6f} "
                    f"(${trade['usd_value']:12,.2f})"
                )
            else:
                logger.info(
                    f"- {trade['type']:4} {trade['symbol']:12} {trade['amount']:10,.6f} "
                    f"(${trade['usd_value']:12,.2f})"
                )

        # Execute trades only if private key is set
        if trades and agent.wallet_private_key:
            logger.info("Executing trades...")
            await agent.execute_trades(trades)
        elif trades:
            logger.info("Trades planned but not executed (private key not set)")
        else:
            logger.info("No trades needed")

    except Exception as e:
        logger.error(f"Error in main: {e}")
    finally:
        await agent.close()


if __name__ == "__main__":
    asyncio.run(main())
