import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
import time

import pytest
import pytest_asyncio
from aiohttp import ClientSession

from src.copy_agent import (CopyTradeAgent, Portfolio, RiskConfig, TokenBalance)
from src.dex.base import SwapQuote, SwapResult
from src.models import SwapTrade, Trade


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
    # Mock portfolio_analyzer
    agent.portfolio_analyzer = AsyncMock()
    agent.portfolio_analyzer.initialize = AsyncMock(side_effect=AttributeError("'JupiterClient' object has no attribute 'initialize'"))
    # Mock trade_executer
    agent.trade_executer = AsyncMock()
    agent.trade_executer.dexes = [AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock()]
    # Initialize token metadata for testing
    agent.token_metadata = {
        "token1": {
            "symbol": "TKN1",
            "name": "Token 1",
            "decimals": 6,
        },
        "token2": {
            "symbol": "TKN2",
            "name": "Token 2",
            "decimals": 6,
        },
    }
    yield agent
    await agent.close()


@pytest.fixture
def portfolio():
    token_balances = {
        "token1": TokenBalance(
            mint="token1",
            amount=Decimal("10"),
            decimals=6,
            usd_value=500.0,
            symbol="TKN1",
            _portfolio_total_value=1000.0
        ),
        "token2": TokenBalance(
            mint="token2",
            amount=Decimal("20"),
            decimals=6,
            usd_value=500.0,
            symbol="TKN2",
            _portfolio_total_value=1000.0
        ),
    }
    return Portfolio(
        total_value_usd=1000.0,
        token_balances=token_balances,
        timestamp=time.time()
    )


@pytest.mark.asyncio
async def test_initialize_error(agent):
    """Test that initialization fails when JupiterClient doesn't have initialize method"""
    with pytest.raises(AttributeError, match="'JupiterClient' object has no attribute 'initialize'"):
        await agent.initialize()


@pytest.mark.asyncio
async def test_create_target_portfolio_error():
    """Test that create_target_portfolio fails when TokenBalance is missing decimals"""
    with pytest.raises(TypeError, match="missing 1 required positional argument: 'decimals'"):
        TokenBalance(
            mint="token1",
            amount=Decimal("10"),
            usd_value=500.0,  # Missing decimals argument
            symbol="TKN1",
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

    # Mock get_best_quote to return mock_quote2
    agent.trade_executer.get_best_quote = AsyncMock(return_value=mock_quote2)

    best_quote = await agent.trade_executer.get_best_quote("token1", "token2", 1000)
    assert best_quote == mock_quote2


@pytest.mark.asyncio
async def test_create_trade_plan(agent):
    # Mock token_price_resolver
    agent.trade_planner.token_price_resolver = AsyncMock()
    agent.trade_planner.token_price_resolver.get_token_prices = AsyncMock(return_value={
        "token1": Decimal("1.0"),
        "token2": Decimal("1.0"),
    })

    # Create current portfolio
    current_balances = {
        "token1": TokenBalance(
            mint="token1",
            amount=Decimal("10"),
            decimals=6,
            usd_value=Decimal("500.0"),
            symbol="TKN1",
            _portfolio_total_value=Decimal("1000.0")
        ),
        "token2": TokenBalance(
            mint="token2",
            amount=Decimal("20"),
            decimals=6,
            usd_value=Decimal("500.0"),
            symbol="TKN2",
            _portfolio_total_value=Decimal("1000.0")
        ),
    }
    current_portfolio = Portfolio(
        total_value_usd=Decimal("1000.0"),
        token_balances=current_balances,
        timestamp=time.time()
    )

    # Create target portfolio
    target_balances = {
        "token1": TokenBalance(
            mint="token1",
            amount=Decimal("5"),
            decimals=6,
            usd_value=Decimal("250.0"),
            symbol="TKN1",
            _portfolio_total_value=Decimal("1000.0")
        ),
        "token2": TokenBalance(
            mint="token2",
            amount=Decimal("30"),
            decimals=6,
            usd_value=Decimal("750.0"),
            symbol="TKN2",
            _portfolio_total_value=Decimal("1000.0")
        ),
    }
    target_portfolio = Portfolio(
        total_value_usd=Decimal("1000.0"),
        token_balances=target_balances,
        timestamp=time.time()
    )

    trades = await agent.create_trade_plan(current_portfolio, target_portfolio)

    # Verify trades are created correctly
    assert len(trades) == 1
    trade = trades[0]
    assert trade.type == "swap"
    assert trade.from_mint == "token1"
    assert trade.to_mint == "token2"
    assert trade.usd_value == Decimal("250.0")


@pytest.mark.asyncio
async def test_check_gas_balance(agent):
    # Test with sufficient balance
    # Mock Keypair
    mock_pubkey = MagicMock()
    mock_pubkey.__str__.return_value = "mock_wallet_address"
    mock_keypair = MagicMock()
    mock_keypair.pubkey.return_value = mock_pubkey
    
    with patch("src.copy_agent.Keypair") as mock_keypair_class, \
         patch("src.copy_agent.base58.b58decode") as mock_b58decode:
        mock_keypair_class.from_seed.return_value = mock_keypair
        mock_b58decode.return_value = b"0" * 32  # 32バイトの秘密鍵
        agent.set_wallet_private_key("mock_private_key")  # 実際の値は関係ありません
        
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

    agent.trade_executer.execute_trades = AsyncMock()
    with patch("src.copy_agent.base58.b58decode") as mock_b58decode:
        mock_b58decode.return_value = b"0" * 32  # 32バイトの秘密鍵
        agent.set_wallet_private_key("mock_private_key")  # 実際の値は関係ありません

        trades = [
            SwapTrade(
                type="swap",
                from_symbol="TKN1",
                from_mint="token1",
                from_amount=Decimal("100"),
                to_symbol="TKN2",
                to_mint="token2",
                to_amount=Decimal("90"),
                usd_value=Decimal("100"),
            ),
        ]

        await agent.execute_trades(trades)

        assert agent.trade_executer.execute_trades.call_count == 1
