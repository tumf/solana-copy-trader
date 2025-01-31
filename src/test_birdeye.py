import asyncio
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

from birdeye import BirdEyeClient

# Well-known token addresses
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
SOL = "So11111111111111111111111111111111111111112"


async def main():
    # Load environment variables
    env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(env_path)

    # Initialize BirdEye client
    client = BirdEyeClient()

    try:
        # Test get_token_price
        logger.info("Testing get_token_price...")
        for token in [USDC, BONK, SOL]:
            price = await client.get_token_price(token)
            logger.info(f"Price of {token}: ${price}")
            await asyncio.sleep(1)  # Wait 1 second between requests

        # Test get_token_metadata
        logger.info("\nTesting get_token_metadata...")
        for token in [USDC, BONK, SOL]:
            metadata = await client.get_token_metadata(token)
            symbol = metadata.get("symbol", "Unknown")
            name = metadata.get("name", "Unknown")
            logger.info(f"Metadata of {token}:")
            logger.info(f"  Symbol: {symbol}")
            logger.info(f"  Name: {name}")
            await asyncio.sleep(1)  # Wait 1 second between requests

    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
