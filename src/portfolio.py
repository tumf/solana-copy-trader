import asyncio
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Optional

from logger import logger
from token_price_resolver import TokenPriceResolver
from token_resolver import TokenResolver

# Set logger name for this module
logger = logger.bind(name="portfolio")


@dataclass
class TokenBalance:
    mint: str
    amount: Decimal
    decimals: int
    usd_value: Decimal
    symbol: str = ""
    _portfolio_total_value: Decimal = Decimal("0")

    @property
    def weight(self) -> Decimal:
        """Calculate weight of token in portfolio"""
        if self._portfolio_total_value <= 0:
            return Decimal("0")
        return self.usd_value / self._portfolio_total_value


@dataclass
class Portfolio:
    total_value_usd: Decimal
    token_balances: Dict[str, TokenBalance]
    timestamp: float = time.time()

    def __post_init__(self):
        """Set portfolio total value for each token balance"""
        for balance in self.token_balances.values():
            balance._portfolio_total_value = self.total_value_usd

    def get_token_weight(self, mint: str) -> Decimal:
        """Get weight of token in portfolio"""
        if mint not in self.token_balances:
            return Decimal("0")
        if self.total_value_usd <= 0:
            return Decimal("0")
        return self.token_balances[mint].usd_value / self.total_value_usd


class PortfolioAnalyzer:
    def __init__(self, token_resolver: Optional[TokenResolver] = None):
        self.token_resolver = token_resolver or TokenResolver()
        self.token_price_resolver = TokenPriceResolver()

    @logger.catch
    async def close(self):
        """Close all connections"""
        if self.token_resolver:
            await self.token_resolver.close()
            await asyncio.sleep(0.1)  # Give time for the session to close properly
            self.token_resolver = None
        if self.token_price_resolver:
            await self.token_price_resolver.close()
            await asyncio.sleep(0.1)  # Give time for the session to close properly
            self.token_price_resolver = None

    @logger.catch
    async def initialize(self):
        """Initialize portfolio analyzer"""
        await self.token_resolver.initialize()
        await self.token_price_resolver.initialize()

    @logger.catch
    async def _get_token_metadata(self, mint: str) -> dict:
        """Get token metadata from local database"""
        metadata = self.token_resolver.get_token_info(mint)
        if metadata and metadata.get("symbol"):
            logger.debug(
                f"Found token metadata in local DB for {mint}: {metadata['symbol']}"
            )
            return metadata

        # If not found, use shortened address as symbol
        logger.debug(f"Using shortened address as symbol for {mint}")
        return {"symbol": mint[:8] + "...", "decimals": 0}

    @logger.catch
    async def get_wallet_portfolio(self, wallet_address: str) -> Portfolio:
        """Get wallet portfolio with token prices"""
        try:
            logger.info(f"Fetching token accounts for {wallet_address}")
            token_accounts = await self.token_resolver.get_token_accounts(
                wallet_address
            )
            if not token_accounts:
                logger.info(f"No token accounts found for {wallet_address}")
                return Portfolio(total_value_usd=Decimal("0"), token_balances={})

            # Get all token mints
            mints = [account.mint for account in token_accounts if account.amount > 0]
            logger.debug(f"Found {len(mints)} active token accounts")

            # Create portfolio entries
            token_balances: Dict[str, TokenBalance] = {}
            total_value_usd = Decimal("0")
            # Get prices for all tokens at once
            active_mints = [
                self.token_resolver.resolve_address(account.mint)
                for account in token_accounts
                if account.amount > 0
            ]
            prices = await self.token_price_resolver.get_token_prices(active_mints)

            for account in token_accounts:
                if account.amount == 0:
                    continue

                resolved_mint = self.token_resolver.resolve_address(account.mint)
                price = prices.get(resolved_mint, Decimal(0))
                usd_value = account.amount * price
                total_value_usd += usd_value

                metadata = await self._get_token_metadata(resolved_mint)
                symbol = metadata.get("symbol", resolved_mint[:8] + "...")

                # 同じトークンの場合は合算する
                if resolved_mint in token_balances:
                    token_balances[resolved_mint].amount += account.amount
                    token_balances[resolved_mint].usd_value += usd_value
                else:
                    token_balances[resolved_mint] = TokenBalance(
                        mint=resolved_mint,
                        amount=account.amount,
                        decimals=account.decimals,
                        usd_value=usd_value,
                        symbol=symbol,
                    )
                logger.debug(
                    f"Processed token {symbol}: {account.amount} (${usd_value:,.2f})"
                )

            logger.info(
                f"Portfolio analysis complete. Total value: ${total_value_usd:,.2f}"
            )
            return Portfolio(
                total_value_usd=total_value_usd, token_balances=token_balances
            )

        except Exception as e:
            logger.error(f"Error getting wallet portfolio: {e}")
            raise

    @logger.catch
    async def analyze_portfolio(self, wallet_address: str) -> Optional[Portfolio]:
        """Analyze portfolio of a single address"""
        try:
            logger.info(f"Starting portfolio analysis for {wallet_address}")
            portfolio = await self.get_wallet_portfolio(wallet_address)

            # Display portfolio summary
            logger.info(f"Portfolio for {wallet_address}:")
            logger.info(f"Total value: ${portfolio.total_value_usd:,.2f}")

            # Print top holdings
            for token in portfolio.token_balances.values():  # Top 10 holdings
                percentage = (
                    (token.usd_value / portfolio.total_value_usd * Decimal("100"))
                    if portfolio.total_value_usd > 0
                    else Decimal("0")
                )
                logger.info(
                    f"{token.symbol:<12} {token.amount:>15,.6f} (${token.usd_value:,.2f}, {percentage:.2f}%)"
                )

            return portfolio
        except Exception as e:
            logger.warning(f"Failed to get portfolio for {wallet_address}: {e}")
            return None


async def main(wallet_address: str):
    # Initialize analyzer with Solana mainnet RPC URL
    analyzer = PortfolioAnalyzer()

    try:
        await analyzer.analyze_portfolio(wallet_address)
    except Exception as e:
        logger.error(f"Error analyzing portfolio: {e}")
    finally:
        await analyzer.close()


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        logger.error("Please provide a wallet address as argument")
        sys.exit(1)
    wallet_address = sys.argv[1]
    asyncio.run(main(wallet_address))
