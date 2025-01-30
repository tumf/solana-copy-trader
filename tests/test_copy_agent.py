import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from aiohttp import ClientSession

from src.copy_agent import (CopyTradeAgent, Portfolio, RiskConfig, SwapQuote,
                            SwapResult, TokenBalance)


@pytest.fixture
def risk_config():
    return RiskConfig(
        max_trade_size_usd=Decimal("1000"),
        min_trade_size_usd=Decimal("10"),
        max_slippage_bps=100,
        max_portfolio_allocation=Decimal("0.25"),
        gas_buffer_sol=Decimal("0.1"),
        weight_tolerance=Decimal("0.02"),
        min_weight_threshold=Decimal("0.01"),
    )


@pytest_asyncio.fixture
async def agent(risk_config):
    agent = CopyTradeAgent("http://test-rpc.url", risk_config)
    # Mock the session to avoid actual HTTP requests
    agent.session = AsyncMock(spec=ClientSession)
    yield agent
    await agent.close()


@pytest.fixture
def portfolio():
    token_balances = {
        "token1": TokenBalance(
            mint="token1", amount=Decimal("10"), usd_value=Decimal("500"), weight=Decimal("0.5")
        ),
        "token2": TokenBalance(
            mint="token2", amount=Decimal("20"), usd_value=Decimal("500"), weight=Decimal("0.5")
        ),
    }
    return Portfolio(
        total_value_usd=Decimal("1000"),
        token_balances=token_balances,
    )


@pytest.mark.asyncio
async def test_get_best_quote(agent):
    # Mock DEX responses
    mock_quote1 = SwapQuote(
        dex_name="DEX1",
        input_mint="token1",
        output_mint="token2",
        input_amount=1000,
        expected_output_amount=900,
        price_impact_pct=Decimal("1.0"),
        minimum_output_amount=800,
    )
    mock_quote2 = SwapQuote(
        dex_name="DEX2",
        input_mint="token1",
        output_mint="token2",
        input_amount=1000,
        expected_output_amount=950,  # Better quote
        price_impact_pct=Decimal("0.5"),
        minimum_output_amount=850,
    )

    # Mock DEX get_quote methods
    agent.dexes[0].get_quote = AsyncMock(return_value=mock_quote1)
    agent.dexes[1].get_quote = AsyncMock(return_value=mock_quote2)
    agent.dexes[2].get_quote = AsyncMock(side_effect=Exception("DEX error"))
    agent.dexes[3].get_quote = AsyncMock(side_effect=Exception("DEX error"))

    best_quote = await agent.get_best_quote("token1", "token2", 1000)
    assert best_quote == mock_quote2


@pytest.mark.asyncio
async def test_create_trade_plan(agent, portfolio):
    token_balances = {
        "token1": TokenBalance(
            mint="token1", amount=Decimal("5"), usd_value=Decimal("250"), weight=Decimal("0.25")
        ),
        "token2": TokenBalance(
            mint="token2", amount=Decimal("30"), usd_value=Decimal("750"), weight=Decimal("0.75")
        ),
    }
    target_portfolio = Portfolio(
        total_value_usd=Decimal("1000"),
        token_balances=token_balances,
    )

    trades = await agent.create_trade_plan(portfolio, target_portfolio)

    # Verify trades are created correctly
    assert len(trades) == 2
    sell_trade = next(t for t in trades if t["type"] == "sell" and t["mint"] == "token1")
    buy_trade = next(t for t in trades if t["type"] == "buy" and t["mint"] == "token2")

    assert sell_trade["usd_value"] == Decimal("250")
    assert buy_trade["usd_value"] == Decimal("250")


@pytest.mark.asyncio
async def test_check_gas_balance(agent):
    # Test with sufficient balance
    # Mock Keypair
    mock_pubkey = MagicMock()
    mock_pubkey.__str__.return_value = "mock_wallet_address"
    mock_keypair = MagicMock()
    mock_keypair.pubkey.return_value = mock_pubkey
    
    with patch("src.copy_agent.Keypair") as mock_keypair_class:
        mock_keypair_class.from_seed.return_value = mock_keypair
        agent.set_wallet("mock_private_key")  # Set wallet before checking balance
        
        # Mock the client
        agent.client = AsyncMock()
        agent.client.get_balance = AsyncMock(return_value=MagicMock(value=200000000))  # 0.2 SOL
        assert await agent.check_gas_balance()

        # Test with insufficient balance
        agent.client.get_balance = AsyncMock(return_value=MagicMock(value=50000000))  # 0.05 SOL
        assert not await agent.check_gas_balance()


@pytest.mark.asyncio
async def test_execute_trades(agent):
    mock_quote = SwapQuote(
        dex_name="TestDEX",
        input_mint="token1",
        output_mint="token2",
        input_amount=1000000,
        expected_output_amount=900000,
        price_impact_pct=Decimal("1.0"),
        minimum_output_amount=890000,
    )

    mock_result = SwapResult(
        success=True, tx_signature="test_signature", error_message=None
    )

    agent.get_best_quote = AsyncMock(return_value=mock_quote)
    agent.execute_swap_with_retry = AsyncMock(return_value=mock_result)
    agent.get_token_price = AsyncMock(return_value=Decimal("1.0"))
    agent.wallet_address = "test_wallet"

    trades = [
        {"type": "buy", "mint": "token2", "usd_value": Decimal("100")},
        {"type": "sell", "mint": "token1", "usd_value": Decimal("100")},
    ]

    await agent.execute_trades(trades)

    assert agent.get_best_quote.call_count == 2
    assert agent.execute_swap_with_retry.call_count == 2
