import asyncio
import os
import sys
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


async def analyze_portfolios(source_addresses: list[str]):
    """Analyze source portfolios and create trade plan"""
    # Load environment variables
    load_dotenv()

    # Initialize agent with risk configuration
    risk_config = load_risk_config()
    agent = CopyTradeAgent(os.getenv("RPC_URL"), risk_config=risk_config)

    try:
        # Set wallet from private key or address
        if private_key := os.getenv("WALLET_PRIVATE_KEY"):
            agent.set_wallet_private_key(private_key)
        elif wallet_address := os.getenv("WALLET_ADDRESS"):
            agent.set_wallet_address(wallet_address)
        else:
            raise ValueError("Either WALLET_PRIVATE_KEY or WALLET_ADDRESS must be set")

        # Get current portfolio
        logger.info(f"Getting current portfolio for {agent.wallet_address}...")
        current_portfolio = await agent.get_wallet_portfolio(agent.wallet_address)

        # Log portfolio information
        logger.info("Current Portfolio:")
        logger.info(f"Total value: ${current_portfolio.total_value_usd:,.2f}")
        sorted_balances = sorted(
            current_portfolio.token_balances.values(),
            key=lambda x: float(x.usd_value),
            reverse=True,
        )
        for balance in sorted_balances:
            if float(balance.usd_value) >= 1:
                weight = current_portfolio.get_token_weight(balance.mint)
                logger.info(
                    f"- {balance.symbol:12} {balance.amount:10,.6f} (${balance.usd_value:12,.2f}) {weight:6.2%}"
                )

        # Analyze source portfolios
        logger.info("Analyzing source portfolios...")
        source_portfolios = await agent.analyze_source_portfolios(source_addresses)
        target_portfolio = agent.create_target_portfolio(
            source_portfolios, current_portfolio.total_value_usd
        )

        # Log target portfolio information
        logger.info("Target Portfolio:")
        logger.info(f"Total value: ${target_portfolio.total_value_usd:,.2f}")
        sorted_balances = sorted(
            target_portfolio.token_balances.values(),
            key=lambda x: float(x.usd_value),
            reverse=True,
        )
        for balance in sorted_balances:
            if float(balance.usd_value) >= 1:
                weight = target_portfolio.get_token_weight(balance.mint)
                logger.info(
                    f"- {balance.symbol:12} {balance.amount:10,.6f} (${balance.usd_value:12,.2f}) {weight:6.2%}"
                )

        # Create trade plan
        logger.info("Creating trade plan...")
        trades = await agent.create_trade_plan(current_portfolio, target_portfolio)

        if trades:
            logger.info(f"Found {len(trades)} trades to execute:")
            total_value = Decimal(str(sum(t.usd_value for t in trades)))
            for trade in trades:
                trade_value = trade.usd_value
                logger.info(
                    f"- {trade.type.upper()}: {trade.from_symbol} -> {trade.to_symbol} "
                    f"for ${trade_value:.2f} "
                    f"({trade_value/total_value:.1%} of total trades)"
                )
        else:
            logger.info("No trades needed")

    except Exception as e:
        logger.error(f"Error: {e}")
        raise SystemExit(1)
    finally:
        await agent.close()


def print_usage():
    """Print usage information"""
    logger.info(
        "Usage: uv run ./src/main.py analyze <source_address1> [source_address2 ...]"
    )
    logger.info(
        "Example: uv run ./src/main.py analyze Gh9ZwEmdLJ8DscKNTkTqPbNwLNNBjuSzaG9Vp2KGtKJr"
    )


async def main():
    """Main entry point"""
    if len(sys.argv) < 3 or sys.argv[1] != "analyze":
        print_usage()
        return

    source_addresses = sys.argv[2:]
    await analyze_portfolios(source_addresses)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SystemExit as e:
        raise e
    except KeyboardInterrupt:
        logger.info("Process interrupted by user")
    except Exception as e:
        logger.exception(f"Unhandled error: {e}")
        raise SystemExit(1)
