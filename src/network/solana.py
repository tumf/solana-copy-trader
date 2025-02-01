"""Solana network constants"""

from models import TokenAlias

USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
SOL_MINT = "So11111111111111111111111111111111111111112"
TOKEN_ALIAS = [
    TokenAlias(
        address=USDC_MINT,
        aliases=[
            "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
            "USDSwr9ApdHk5bvJKMjzff41FfuX8bSxdKcR81vTwcA",  # USDS
        ],
    )
]
RPC_URL = "https://api.mainnet-beta.solana.com"
