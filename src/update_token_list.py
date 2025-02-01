#!/usr/bin/env python3
import asyncio
import logging
import os
from datetime import UTC, datetime
from typing import Dict, List

import aiohttp
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from models import Base, Token

# Load environment variables from .env file
load_dotenv()

# API endpoints
JUPITER_TOKEN_LIST_URL = os.getenv(
    "JUPITER_TOKEN_LIST_URL", "https://tokens.jup.ag/tokens?tags=strict"
)

# Database settings
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/solana.db")

# Constants for retry logic
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds
REQUEST_TIMEOUT = 10  # seconds


def log_info(msg: str):
    print(f"INFO: {msg}")


def log_warning(msg: str):
    print(f"WARNING: {msg}")


def log_error(msg: str):
    print(f"ERROR: {msg}")


async def fetch_with_retry(session, url, retries=MAX_RETRIES):
    for attempt in range(retries):
        try:
            async with session.get(url, timeout=REQUEST_TIMEOUT) as response:
                if response.status != 200:
                    logging.warning(
                        f"Request failed with status {response.status} for URL: {url}"
                    )
                    if attempt < retries - 1:
                        await asyncio.sleep(RETRY_DELAY)
                        continue
                    return None

                try:
                    return await response.json()
                except Exception as e:
                    logging.warning(
                        f"Failed to parse JSON response from {url}: {str(e)}"
                    )
                    if attempt < retries - 1:
                        await asyncio.sleep(RETRY_DELAY)
                        continue
                    return None

        except asyncio.TimeoutError:
            logging.warning(
                f"Request timed out after {REQUEST_TIMEOUT} seconds for URL: {url}"
            )
        except Exception as e:
            logging.warning(f"Failed to fetch data from {url}: {str(e)}")
        if attempt < retries - 1:
            await asyncio.sleep(RETRY_DELAY)
    return None


async def fetch_jupiter_tokens(session: aiohttp.ClientSession) -> List[Dict]:
    """Fetch token list from Jupiter"""
    try:
        data = await fetch_with_retry(session, JUPITER_TOKEN_LIST_URL)
        if data:
            log_info(f"Loaded {len(data)} tokens from Jupiter API")
            return data
        return []
    except Exception as e:
        log_error(f"Failed to fetch Jupiter tokens: {str(e)}")
        return []


def create_token_data(jupiter_tokens: List[Dict]) -> List[Token]:
    """Create token data from Jupiter token list"""
    tokens = []
    for token in jupiter_tokens:
        tokens.append(
            Token(
                address=token["address"],
                symbol=token["symbol"],
                name=token["name"],
                decimals=token["decimals"],
                source="jupiter",
                last_updated=datetime.now(UTC),
            )
        )
    return tokens


def save_token_data(tokens: List[Token]):
    """Save token data to SQLite database"""
    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        # Get existing token addresses
        existing_addresses = {addr[0] for addr in session.query(Token.address).all()}

        # Filter out tokens that already exist
        new_tokens = [
            token for token in tokens if token.address not in existing_addresses
        ]

        if new_tokens:
            session.add_all(new_tokens)
            session.commit()
            log_info(f"Added {len(new_tokens)} new tokens to database")
        else:
            log_info("No new tokens to add")

        # remove tokens that are not in the Jupiter token list
        session.query(Token).filter(Token.source != "jupiter").delete()
        session.commit()
        log_info("Removed tokens that are not in the Jupiter token list")


async def main():
    """Main function to update token lists"""
    # Create data directory if it doesn't exist
    os.makedirs("data", exist_ok=True)

    async with aiohttp.ClientSession() as session:
        # Fetch token data from Jupiter
        jupiter_tokens = await fetch_jupiter_tokens(session)

        # Create token data
        tokens = create_token_data(jupiter_tokens)

        # Save token data
        save_token_data(tokens)


if __name__ == "__main__":
    asyncio.run(main())
