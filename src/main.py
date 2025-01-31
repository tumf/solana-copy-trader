import asyncio
import os
from decimal import Decimal

from dotenv import load_dotenv
from loguru import logger

from copy_agent import CopyTradeAgent
from models import RiskConfig


def load_risk_config() -> RiskConfig:
    """Load risk configuration from environment variables"""

    def clean_value(value: str) -> str:
        return value.split("#")[0].strip() if value else ""

    return RiskConfig(
        max_trade_size_usd=Decimal(
            clean_value(os.getenv("MAX_TRADE_SIZE_USD", "1000"))
        ),
        min_trade_size_usd=Decimal(clean_value(os.getenv("MIN_TRADE_SIZE_USD", "10"))),
        max_slippage_bps=int(clean_value(os.getenv("MAX_SLIPPAGE_BPS", "100"))),
        max_portfolio_allocation=Decimal(
            clean_value(os.getenv("MAX_PORTFOLIO_ALLOCATION", "0.25"))
        ),
        gas_buffer_sol=Decimal(clean_value(os.getenv("GAS_BUFFER_SOL", "0.1"))),
        weight_tolerance=Decimal(clean_value(os.getenv("WEIGHT_TOLERANCE", "0.02"))),
        min_weight_threshold=Decimal(
            clean_value(os.getenv("MIN_WEIGHT_THRESHOLD", "0.01"))
        ),
    )


async def main():
    # Load environment variables
    # env_path = Path(__file__).parent.parent / ".env"
    # load_dotenv(env_path)
    load_dotenv()

    # Initialize agent with risk configuration
    risk_config = load_risk_config()
    agent = CopyTradeAgent(os.getenv("RPC_URL"), risk_config=risk_config)

    try:
        # Set wallet from private key
        private_key = os.getenv("WALLET_PRIVATE_KEY")
        if not private_key:
            raise ValueError("WALLET_PRIVATE_KEY not set in .env")
        agent.set_wallet(private_key)

        # Get source addresses
        source_addresses = [
            addr.strip()
            for addr in os.getenv("SOURCE_ADDRESSES", "").split(",")
            if addr.strip()
        ]

        if not source_addresses:
            raise ValueError("No source addresses configured")

        # Analyze source portfolios
        logger.info("Analyzing source portfolios...")
        source_portfolios = await agent.analyze_source_portfolios(source_addresses)
        target_portfolio = agent.create_target_portfolio(source_portfolios)

        # Get current portfolio
        logger.info(f"Getting current portfolio for {agent.wallet_address}...")
        current_portfolio = await agent.get_wallet_portfolio(agent.wallet_address)

        # Log portfolio information
        logger.info("Current Portfolio:")
        for mint, balance in current_portfolio.token_balances.items():
            weight = current_portfolio.get_token_weight(mint)
            logger.info(f"  {mint}: ${balance.usd_value:.2f} ({weight:.1%})")

        logger.info("Target Portfolio:")
        # Sort by USD value
        sorted_balances = sorted(
            target_portfolio.token_balances.values(),
            key=lambda x: float(x.usd_value),  # Convert to float for sorting
            reverse=True,
        )
        logger.info(f"Total value: ${target_portfolio.total_value_usd:,.2f}")
        for balance in sorted_balances:
            if float(balance.usd_value) >= 1:  # Convert to float for comparison
                logger.info(
                    f"- {balance.symbol:12} {balance.amount:10,.6f} (${balance.usd_value:12,.2f}) {balance.weight:6.2%}"
                )

        logger.info("Creating trade plan...")
        trades = await agent.create_trade_plan(current_portfolio, target_portfolio)

        if trades:
            logger.info(f"Found {len(trades)} trades to execute:")
            total_value = Decimal(str(sum(t["usd_value"] for t in trades)))
            for trade in trades:
                trade_value = Decimal(str(trade["usd_value"]))
                logger.info(
                    f"  {trade['type'].upper()}: {trade['mint']} "
                    f"for ${trade_value:.2f} "
                    f"({trade_value/total_value:.1%} of total trades)"
                )

            confirmation = input("Execute these trades? [y/N]: ")
            if confirmation.lower() == "y":
                logger.info("Executing trades...")
                await agent.execute_trades(trades)
                logger.info("All trades completed")
            else:
                logger.info("Trade execution cancelled")
        else:
            logger.info("No trades needed")

    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        await agent.close()


if __name__ == "__main__":
    asyncio.run(main())
