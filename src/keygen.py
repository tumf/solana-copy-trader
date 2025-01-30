from pathlib import Path
from typing import Optional

import base58
from loguru import logger
from mnemonic import Mnemonic
from solders.keypair import Keypair

def generate_keypair(save_path: Optional[Path] = None) -> Keypair:
    """Generate a new Solana keypair"""
    keypair = Keypair()
    _save_and_log_keypair(keypair, save_path)
    return keypair


def keypair_from_mnemonic(mnemonic_phrase: str, save_path: Optional[Path] = None) -> Keypair:
    """Generate a keypair from a mnemonic phrase"""
    mnemo = Mnemonic("english")
    seed = mnemo.to_seed(mnemonic_phrase)
    keypair = Keypair.from_seed(seed[:32])
    _save_and_log_keypair(keypair, save_path)
    return keypair


def keypair_from_base58(secret_key: str, save_path: Optional[Path] = None) -> Keypair:
    """Generate a keypair from a base58 encoded secret key"""
    secret_bytes = base58.b58decode(secret_key)
    keypair = Keypair.from_bytes(secret_bytes[:32])  # Use first 32 bytes as secret key
    _save_and_log_keypair(keypair, save_path)
    return keypair


def keypair_from_bytes(secret_key: bytes, save_path: Optional[Path] = None) -> Keypair:
    """Generate a keypair from raw bytes"""
    keypair = Keypair.from_bytes(secret_key[:32])  # Use first 32 bytes as secret key
    _save_and_log_keypair(keypair, save_path)
    return keypair


def _save_and_log_keypair(keypair: Keypair, save_path: Optional[Path] = None) -> None:
    """Save keypair to file and log information"""
    pubkey = str(keypair.pubkey())
    secret = bytes(keypair)[:32]  # Only save the secret key
    secret_b58 = base58.b58encode(secret).decode('utf-8')
    
    logger.info(f"Public Key: {pubkey}")
    logger.info(f"Private Key (base58): {secret_b58}")
    
    if save_path:
        save_path.write_bytes(secret)  # Save only the secret key
        logger.info(f"Keypair saved to: {save_path}")
        logger.info("⚠️  Keep your private key safe and never share it with anyone!")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Generate a new Solana keypair")
    parser.add_argument(
        "--save",
        type=Path,
        help="Save the keypair to a file",
        default=None
    )
    parser.add_argument(
        "--mnemonic",
        type=str,
        help="Generate keypair from mnemonic phrase",
        default=None
    )
    parser.add_argument(
        "--base58",
        type=str,
        help="Generate keypair from base58 encoded secret key",
        default=None
    )
    
    args = parser.parse_args()
    
    if args.mnemonic:
        keypair_from_mnemonic(args.mnemonic, args.save)
    elif args.base58:
        keypair_from_base58(args.base58, args.save)
    else:
        generate_keypair(args.save)
