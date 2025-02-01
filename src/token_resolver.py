import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional

import aiohttp
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from logger import logger
from models import Token, TokenAlias
from network.solana import TOKEN_PROGRAM_ID, RPC_URL

logger = logger.bind(name="token_resolver")


@dataclass
class TokenAccount:
    mint: str
    amount: Decimal
    decimals: int


class TokenResolver:
    def __init__(self):
        self.rpc_url = RPC_URL
        self.session = None
        self.client = None
        self.token_db = {}
        self.engine = create_engine("sqlite:///data/solana.db")
        self._cache: Dict[str, Token] = {}
        self.logger = logging.getLogger(__name__)
        self.token_replacement_map: Dict[str, str] = {}

    async def initialize(self):
        """Initialize token resolver"""
        await self.ensure_session()

    def get_token_info(self, address: str) -> Optional[Dict]:
        """Get token information from cache or database"""
        # Check cache first
        if address in self._cache:
            token = self._cache[address]
            return {
                "symbol": token.symbol,
                "name": token.name,
                "decimals": token.decimals,
            }

        # Query database
        with Session(self.engine) as session:
            stmt = select(Token).where(Token.address == address)
            token = session.scalar(stmt)
            if token:
                # Cache the result
                self._cache[address] = token
                return {
                    "symbol": token.symbol,
                    "name": token.name,
                    "decimals": token.decimals,
                }

        return None

    def update_token_info(self, address: str, info: Dict) -> None:
        """Update or insert token information in database"""
        with Session(self.engine) as session:
            # Check if token exists
            stmt = select(Token).where(Token.address == address)
            token = session.scalar(stmt)

            if token:
                # Update existing token
                token.symbol = info.get("symbol", token.symbol)
                token.name = info.get("name", token.name)
                token.decimals = info.get("decimals", token.decimals)
            else:
                # Create new token
                token = Token(
                    address=address,
                    symbol=info.get("symbol", ""),
                    name=info.get("name", ""),
                    decimals=info.get("decimals", 0),
                )
                session.add(token)

            # Commit changes
            session.commit()

            # Update cache
            self._cache[address] = token

    def get_token_symbol(self, address: str) -> str:
        """Get token symbol or fallback to address"""
        token_info = self.get_token_info(address)
        if token_info:
            return token_info["symbol"]
        return address[:8]  # Fallback to first 8 characters of address

    def get_token_decimals(self, address: str) -> int:
        """Get token decimals or fallback to 0"""
        token_info = self.get_token_info(address)
        if token_info:
            return token_info["decimals"]
        return 0  # Fallback to 0 decimals

    def get_token_name(self, address: str) -> str:
        """Get token name or fallback to symbol"""
        token_info = self.get_token_info(address)
        if token_info:
            return token_info["name"]
        return self.get_token_symbol(address)  # Fallback to symbol

    async def ensure_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return self.session

    async def get_token_accounts(self, wallet_address: str) -> list[TokenAccount]:
        """Get token accounts for a wallet"""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenAccountsByOwner",
                "params": [
                    wallet_address,
                    {"programId": TOKEN_PROGRAM_ID},
                    {"encoding": "jsonParsed"},
                ],
            }

            session = await self.ensure_session()
            async with session.post(self.rpc_url, json=payload) as response:
                data = await response.json()

                if "error" in data:
                    raise Exception(f"RPC error: {data['error']}")

                accounts = data["result"]["value"]
                self.logger.debug(f"Found {len(accounts)} token accounts")

                token_accounts = []
                for account in accounts:
                    try:
                        parsed_info = account["account"]["data"]["parsed"]["info"]
                        # Skip tokens not in our database
                        if not self.get_token_info(parsed_info["mint"]):
                            continue
                        token_accounts.append(
                            TokenAccount(
                                mint=parsed_info["mint"],
                                amount=Decimal(
                                    str(parsed_info["tokenAmount"]["uiAmount"])
                                ),
                                decimals=parsed_info["tokenAmount"]["decimals"],
                            )
                        )
                    except Exception as e:
                        self.logger.warning(f"Error processing account: {e}")
                        continue

                return token_accounts

        except Exception as e:
            self.logger.error(f"Error getting token accounts: {e}")
            raise

    async def close(self):
        """Close the aiohttp session"""
        if self.session:
            await self.session.close()
            await asyncio.sleep(0.1)  # Give time for the session to close properly
            self.session = None
