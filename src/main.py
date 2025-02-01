import asyncio
import os
import sys
from decimal import Decimal

from dotenv import load_dotenv
from loguru import logger

from copy_agent import CopyTradeAgent
from models import RiskConfig

# Configure logger
logger.remove()
logger.add(
    sys.stderr,
    format=("<level>{message}</level>"),
    level="INFO",
    colorize=True,
)


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


async def analyze_portfolios(source_addresses: list[str], execute_trades: bool = False):
    """Analyze source portfolios and create trade plan"""
    # Load environment variables
    load_dotenv()

    # Initialize agent with risk configuration
    risk_config = load_risk_config()
    agent = CopyTradeAgent(
        os.getenv("RPC_URL"),
        risk_config=risk_config,
    )

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
        if not trades:
            logger.info("No trades needed")
        else:
            logger.info("Planned trades:")
            for trade in trades:
                logger.info(
                    f"- swap {trade.from_symbol:12} -> {trade.to_symbol:12} "
                    f"{trade.from_amount:10,.6f} -> {trade.to_amount:10,.6f} "
                    f"(${trade.usd_value:12,.2f})"
                )

        # Execute trades if requested
        if execute_trades:
            if not agent.wallet_private_key:
                raise ValueError("WALLET_PRIVATE_KEY is required for trade execution")
            logger.info("Executing trades...")
            await agent.execute_trades(trades)
            logger.info("Trade execution completed")

    except Exception as e:
        logger.error(f"Error: {e}")
        raise SystemExit(1)
    finally:
        await agent.close()


def print_usage():
    """Print usage information"""
    print("Usage:")
    print("  Analyze portfolios:")
    print(
        "    uv run python ./src/main.py analyze <source_address1> [<source_address2> ...]"
    )
    print()
    print("  Execute trades:")
    print(
        "    uv run python ./src/main.py trade <source_address1> [<source_address2> ...]"
    )
    print()
    print("Environment variables:")
    print("  Required:")
    print("    - RPC_URL: Solana RPC URL")
    print(
        "    - WALLET_PRIVATE_KEY or WALLET_ADDRESS: Your wallet's private key (for trading) or address (for analysis)"
    )
    print()
    print("  Optional:")
    print("    - MAX_TRADE_SIZE_USD: Maximum trade size in USD (default: 1000)")
    print("    - MIN_TRADE_SIZE_USD: Minimum trade size in USD (default: 10)")
    print("    - MAX_SLIPPAGE_BPS: Maximum slippage in basis points (default: 100)")
    print(
        "    - MAX_PORTFOLIO_ALLOCATION: Maximum allocation per token (default: 0.25)"
    )
    print("    - GAS_BUFFER_SOL: SOL to keep for gas fees (default: 0.1)")
    print("    - WEIGHT_TOLERANCE: Portfolio weight tolerance (default: 0.02)")
    print("    - MIN_WEIGHT_THRESHOLD: Minimum weight to consider (default: 0.01)")


async def main():
    """Main entry point"""
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(1)

    command = sys.argv[1]
    if command == "analyze":
        if len(sys.argv) < 3:
            print("Error: No source addresses provided")
            print_usage()
            sys.exit(1)
        source_addresses = sys.argv[2:]
        await analyze_portfolios(source_addresses)
    elif command == "trade":
        if len(sys.argv) < 3:
            print("Error: No source addresses provided")
            print_usage()
            sys.exit(1)
        source_addresses = sys.argv[2:]
        await analyze_portfolios(source_addresses, execute_trades=True)
    else:
        print(f"Error: Unknown command '{command}'")
        print_usage()
        sys.exit(1)


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
