# Solana Copy Trader

A Python-based Solana copy trading bot that analyzes portfolios of specified addresses and replicates their trading strategies.

## Features

- Portfolio analysis of source addresses
- Token price tracking using Jupiter API
- Automated portfolio rebalancing
- DEX trading via Jupiter (coming soon)

## Setup

1. Clone the repository
2. Install dependencies:

```bash
uv add solders python-dotenv loguru requests
```

3. Copy `.env.example` to `.env` and configure your settings:

```bash
cp .env.example .env
```

4. Edit `.env` with your settings:
- `RPC_URL`: Solana RPC endpoint
- `WALLET_PRIVATE_KEY`: Your wallet's private key
- `SOURCE_ADDRESSES`: Comma-separated list of addresses to copy

## Usage

```python
from solders.pubkey import Pubkey
from src.copy_agent import CopyTradeAgent

async def main():
    agent = CopyTradeAgent(os.getenv("RPC_URL"))
    
    # Analyze source portfolios
    source_addresses = [Pubkey.from_string(addr) for addr in os.getenv("SOURCE_ADDRESSES").split(",")]
    source_portfolios = await agent.analyze_source_portfolios(source_addresses)
    
    # Get current portfolio
    wallet = Pubkey.from_string(os.getenv("WALLET_ADDRESS"))
    current_portfolio = await agent.get_wallet_portfolio(wallet)
    
    # Create target portfolio
    target_portfolio = agent.create_target_portfolio(source_portfolios)
    
    # Create and execute trade plan
    trades = await agent.create_trade_plan(current_portfolio, target_portfolio)
    await agent.execute_trades(trades)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

## Development

- Format code: `make format`
- Run linter: `make lint`
- Run type checker: `make typecheck`
- Run tests: `make test`
