from decimal import Decimal
from typing import Dict, List
from portfolio import Portfolio
from token_price_resolver import TokenPriceResolver
from logger import logger
from network import USDC_MINT, SOL_MINT

logger = logger.bind(name="trade_planner")


class RiskConfig:
    def __init__(
        self,
        max_trade_size_usd: Decimal,
        min_trade_size_usd: Decimal,
        max_slippage_bps: int,
        max_portfolio_allocation: Decimal,
        gas_buffer_sol: Decimal,
        weight_tolerance: Decimal,
        min_weight_threshold: Decimal,
    ):
        self.max_trade_size_usd = max_trade_size_usd
        self.min_trade_size_usd = min_trade_size_usd
        self.max_slippage_bps = max_slippage_bps
        self.max_portfolio_allocation = max_portfolio_allocation
        self.gas_buffer_sol = gas_buffer_sol
        self.weight_tolerance = weight_tolerance
        self.min_weight_threshold = min_weight_threshold


class TradePlanner:
    def __init__(self, risk_config: RiskConfig):
        self.risk_config = risk_config
        self.token_price_resolver = TokenPriceResolver()

    async def initialize(self):
        """Initialize trade planner"""
        await self.token_price_resolver.initialize()

    async def close(self):
        """Close all connections"""
        await self.token_price_resolver.close()

    async def create_trade_plan(
        self, current_portfolio: Portfolio, target_portfolio: Portfolio
    ) -> List[Dict]:
        """Create trade plan to match target portfolio with risk management and tolerance"""
        trades = []

        # 現在と目標の総資産価値を取得
        current_total = Decimal(str(current_portfolio.total_value_usd))

        # すべてのユニークなトークンを収集
        all_tokens = set(
            list(current_portfolio.token_balances.keys())
            + list(target_portfolio.token_balances.keys())
        )

        # Get all token prices at once
        prices = await self.token_price_resolver.get_token_prices(list(all_tokens))

        for mint in all_tokens:
            # USDCとSOLの取引はスキップ
            if mint == USDC_MINT:
                logger.debug(f"Skipping USDC trade")
                continue
            if mint == SOL_MINT:
                logger.debug(f"Skipping SOL trade")
                continue

            # Get token price from cache
            price = prices.get(mint, Decimal(0))
            current = current_portfolio.token_balances.get(mint)
            target = target_portfolio.token_balances.get(mint)
            symbol = (
                current.symbol
                if current
                else target.symbol if target else mint[:8] + "..."
            )

            # 現在と目標の重みを計算
            current_weight = Decimal(0)
            if current:
                current_weight = (
                    current.usd_value / current_total
                    if current_total > 0
                    else Decimal(0)
                )

            target_weight = Decimal(0)
            if target:
                target_weight = target.weight  # 目標ポートフォリオの重みを直接使用

            # 重みの差を計算
            weight_diff = abs(target_weight - current_weight)

            # 最小重み閾値チェック
            if target_weight < self.risk_config.min_weight_threshold:
                if (
                    current_weight > 0
                    and current.usd_value > self.risk_config.min_trade_size_usd
                ):
                    # 保有していて、目標が最小閾値未満なら売却
                    trades.append(
                        {
                            "type": "sell",
                            "symbol": symbol,
                            "mint": mint,
                            "amount": current.amount,
                            "usd_value": current.usd_value,
                        }
                    )
                continue

            # 許容誤差内なら無視
            if weight_diff <= self.risk_config.weight_tolerance:
                logger.debug(
                    f"Skipping {symbol} {mint}: weight difference {weight_diff:.3%} within tolerance"
                )
                continue

            # トレード価値を計算（現在の総資産価値に基づいて）
            trade_value = Decimal(0)
            if target_weight > current_weight:
                # 買い注文：目標の重みと現在の重みの差に基づいて計算
                trade_value = current_total * (target_weight - current_weight)
                trade_type = "buy"
            else:
                # 売り注文：現在の重みと目標の重みの差に基づいて計算
                trade_value = current_total * (current_weight - target_weight)
                trade_type = "sell"

            # 最小取引サイズチェック
            if trade_value < self.risk_config.min_trade_size_usd:
                logger.debug(
                    f"Skipping small trade for {symbol} {mint}: ${trade_value:,.2f} < ${self.risk_config.min_trade_size_usd:,.2f}"
                )
                continue

            # 最小取引サイズ未満の残高は無視
            if current and current.usd_value < self.risk_config.min_trade_size_usd:
                logger.debug(
                    f"Skipping dust balance for {symbol} {mint}: ${current.usd_value:,.2f} < ${self.risk_config.min_trade_size_usd:,.2f}"
                )
                continue

            # 大きな取引を分割
            remaining_value = trade_value
            while remaining_value > 0:
                batch_value = min(remaining_value, self.risk_config.max_trade_size_usd)
                # 最小取引サイズ未満のバッチは無視
                if batch_value < self.risk_config.min_trade_size_usd:
                    logger.debug(
                        f"Skipping dust batch for {symbol} {mint}: ${batch_value:,.2f} < ${self.risk_config.min_trade_size_usd:,.2f}"
                    )
                    break

                batch_amount = (
                    batch_value / price if price > 0 else Decimal(0)
                )  # USDをトークン数に変換

                trades.append(
                    {
                        "type": trade_type,
                        "symbol": symbol,
                        "mint": mint,
                        "usd_value": batch_value,
                        "amount": batch_amount,
                    }
                )

                remaining_value -= batch_value  # 残りの取引価値を減少

        return trades
