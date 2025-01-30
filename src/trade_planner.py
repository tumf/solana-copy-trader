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
        unoptimized_trades = []  # 最適化前の取引リスト

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
                    unoptimized_trades.append(
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

                unoptimized_trades.append(
                    {
                        "type": trade_type,
                        "symbol": symbol,
                        "mint": mint,
                        "usd_value": batch_value,
                        "amount": batch_amount,
                    }
                )

                remaining_value -= batch_value  # 残りの取引価値を減少

        # 取引を最適化
        return self._optimize_trades(unoptimized_trades)

    def _optimize_trades(self, trades: List[Dict]) -> List[Dict]:
        """取引を最適化して合成する"""
        if not trades:
            return []

        # 売り注文と買い注文を分離
        sell_trades = [t for t in trades if t["type"] == "sell"]
        buy_trades = [t for t in trades if t["type"] == "buy"]

        optimized_trades = []
        sell_index = 0
        buy_index = 0

        while sell_index < len(sell_trades) and buy_index < len(buy_trades):
            sell_trade = sell_trades[sell_index]
            buy_trade = buy_trades[buy_index]

            # 取引価値の小さい方を基準に合成
            trade_value = min(sell_trade["usd_value"], buy_trade["usd_value"])

            # 合成取引を作成
            optimized_trades.append({
                "type": "swap",
                "from_symbol": sell_trade["symbol"],
                "from_mint": sell_trade["mint"],
                "from_amount": (trade_value / sell_trade["usd_value"]) * sell_trade["amount"],
                "to_symbol": buy_trade["symbol"],
                "to_mint": buy_trade["mint"],
                "to_amount": (trade_value / buy_trade["usd_value"]) * buy_trade["amount"],
                "usd_value": trade_value,
            })

            # 残りの取引価値を更新
            sell_trade["usd_value"] -= trade_value
            buy_trade["usd_value"] -= trade_value
            sell_trade["amount"] = (sell_trade["usd_value"] / trade_value) * sell_trade["amount"] if trade_value > 0 else Decimal(0)
            buy_trade["amount"] = (buy_trade["usd_value"] / trade_value) * buy_trade["amount"] if trade_value > 0 else Decimal(0)

            # 完了した取引を次に進める
            if sell_trade["usd_value"] < self.risk_config.min_trade_size_usd:
                sell_index += 1
            if buy_trade["usd_value"] < self.risk_config.min_trade_size_usd:
                buy_index += 1

        # 残りの取引を追加
        remaining_trades = []
        remaining_trades.extend(sell_trades[sell_index:])
        remaining_trades.extend(buy_trades[buy_index:])
        optimized_trades.extend([t for t in remaining_trades if t["usd_value"] >= self.risk_config.min_trade_size_usd])

        return optimized_trades
