from unittest.mock import AsyncMock, MagicMock

import pytest


def pytest_configure(config):
    """Configure pytest."""
    config.addinivalue_line("markers", "asyncio: mark test as async")


pytest_plugins = ["pytest_asyncio"]


@pytest.fixture
async def agent():
    # Create a mock agent for testing
    agent = MagicMock()
    agent.trade_executer = MagicMock()
    agent.trade_executer.get_best_quote = AsyncMock()
    agent.trade_executer.execute_trades = AsyncMock()
    return agent
