import asyncio
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Optional, List
import aiohttp
from loguru import logger
import logging

from token_resolver import TokenResolver
from birdeye import BirdEyeClient
from jupiter import JupiterClient

USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


@dataclass
class TokenBalance:
    mint: str
    amount: Decimal
    decimals: int
    usd_value: float


@dataclass
class Portfolio:
    total_value_usd: float
    token_balances: List[TokenBalance]


class PortfolioAnalyzer:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.rpc_url = "https://api.mainnet-beta.solana.com"
        self.session = None
        self.token_resolver = TokenResolver()
        self.jupiter_client = JupiterClient()
        self.headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

    async def ensure_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession(headers=self.headers)
        return self.session

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None
        await self.token_resolver.close()
        await self.jupiter_client.close()

    async def _get_token_metadata(self, mint: str) -> dict:
        """Get token metadata from local database or BirdEye"""
        # まずローカルDBから検索
        metadata = self.token_resolver.get_token_info(mint)
        if metadata and metadata.get("symbol"):
            return metadata

        # DBに見つからない場合はBirdEyeから取得
        birdeye = None
        try:
            birdeye = BirdEyeClient()
            metadata = await birdeye.get_token_metadata(mint)
            if metadata and metadata.get("symbol"):
                # DBを更新
                self.token_resolver.update_token_info(
                    mint,
                    {
                        "symbol": metadata["symbol"],
                        "name": metadata.get("name", ""),
                        "decimals": metadata.get("decimals", 0),
                    },
                )
                return metadata
        except Exception as e:
            logger.debug(f"Failed to get metadata from BirdEye for {mint}: {e}")
        finally:
            if birdeye:
                await birdeye.close()

        # 見つからない場合はアドレスの短縮版を返す
        return {"symbol": mint[:8] + "...", "decimals": 0}

    async def get_wallet_portfolio(self, wallet_address: str) -> Portfolio:
        """Get wallet portfolio with token prices"""
        try:
            token_accounts = await self.token_resolver.get_token_accounts(
                wallet_address
            )
            if not token_accounts:
                self.logger.info(f"No token accounts found for {wallet_address}")
                return Portfolio(total_value_usd=0.0, token_balances=[])

            # Get all token mints
            mints = [account.mint for account in token_accounts if account.amount > 0]

            # Get prices for all tokens at once
            prices = await self.jupiter_client.get_token_prices(mints)

            # Create portfolio entries
            token_balances = []
            total_value_usd = 0.0

            for account in token_accounts:
                if account.amount == 0:
                    continue

                price = prices.get(account.mint, 0.0)
                if account.mint == USDC_MINT:  # USDC
                    price = 1.0

                # account.amount is already in decimal form, no need to divide by decimals
                usd_value = float(account.amount) * price
                total_value_usd += usd_value

                token_balances.append(
                    TokenBalance(
                        mint=account.mint,
                        amount=account.amount,
                        decimals=account.decimals,
                        usd_value=usd_value,
                    )
                )

            # Sort by USD value
            token_balances.sort(key=lambda x: x.usd_value, reverse=True)
            return Portfolio(
                total_value_usd=total_value_usd, token_balances=token_balances
            )

        except Exception as e:
            self.logger.error(f"Error getting wallet portfolio: {e}")
            raise

    async def analyze_portfolio(self, wallet_address: str) -> Optional[Portfolio]:
        """Analyze portfolio of a single address"""
        try:
            portfolio = await self.get_wallet_portfolio(wallet_address)

            # Display portfolio summary
            logger.info(f"Portfolio for {wallet_address}:")
            logger.info(f"Total value: ${portfolio.total_value_usd:,.2f}")

            # Print top holdings
            for token in portfolio.token_balances[:10]:  # Top 10 holdings
                percentage = (
                    (token.usd_value / portfolio.total_value_usd * 100)
                    if portfolio.total_value_usd > 0
                    else 0
                )
                logger.info(
                    f"{token.mint:<44} {token.amount:>15,.6f} (${token.usd_value:,.2f}, {percentage:.2f}%)"
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
