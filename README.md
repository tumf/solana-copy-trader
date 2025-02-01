# Solana Copy Trader

A tool to copy trade Solana wallets by monitoring their portfolio and replicating their trades.

## Features

- Monitor source wallet portfolio in real-time
- Analyze portfolio changes and create trade plans
- Execute trades automatically using Jupiter Exchange
- Risk management and portfolio rebalancing
- Configurable trade parameters and risk settings

## Setup

1. Copy the example environment file:

```bash
cp .env.example .env
```

2. Configure the required environment variables in `.env`:

```
RPC_URL=            # Your Solana RPC URL
WALLET_PRIVATE_KEY= # Your wallet's private key
SOURCE_ADDRESS=     # Address to copy trade
```

3. Run with Docker:

```bash
docker compose up --build
```

## Configuration

See `.env.example` for all available configuration options:

- Trade interval settings
- Risk management parameters
- Portfolio tolerance settings
- API configurations

## License

MIT
