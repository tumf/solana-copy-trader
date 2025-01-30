from loguru import logger
import sys
import os

# Remove default handler
logger.remove()

# Create logs directory if it doesn't exist
os.makedirs("logs", exist_ok=True)

# Add custom handlers
logger.add(
    sys.stdout,
    colorize=True,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan> - <level>{message}</level>",
    level="INFO"
)

# Add file handler for debugging
logger.add(
    "logs/debug.log",
    rotation="500 MB",
    retention="10 days",
    compression="zip",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name} - {message}",
    level="DEBUG"
)

def log_debug(msg: str):
    """Debug level log message"""
    logger.debug(msg)


def log_info(msg: str):
    """Info level log message"""
    logger.info(msg)


def log_warning(msg: str):
    """Warning level log message"""
    logger.warning(msg)


def log_error(msg: str):
    """Error level log message"""
    logger.error(msg) 