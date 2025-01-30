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
        if self.token_price_resolver:
            await self.token_price_resolver.initialize()

    async def close(self):
        """Close all connections"""
        if self.token_price_resolver:
            await self.token_price_resolver.close()
            self.token_price_resolver = None

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

        adjusted_target = {}
        total_adjusted_weight = Decimal(0)
        for mint in all_tokens:
            if mint == USDC_MINT:
                continue
            if mint in target_portfolio.token_balances:
                weight = target_portfolio.token_balances[mint].weight * Decimal(1)
                adjusted_target[mint] = weight
                total_adjusted_weight += weight

        # USDCのウェイトを残りの割合に設定
        usdc_weight = Decimal(1) - total_adjusted_weight
        if USDC_MINT in target_portfolio.token_balances:
            adjusted_target[USDC_MINT] = usdc_weight

        for mint in all_tokens:
            if mint == USDC_MINT:
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

            target_weight = adjusted_target.get(mint, Decimal(0))

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
                # 売却後の残高が最小取引サイズを下回る場合は全て売却
                remaining_value = (
                    current.usd_value - trade_value if current else Decimal(0)
                )
                if Decimal("0") < remaining_value < self.risk_config.min_trade_size_usd:
                    logger.debug(
                        f"Remaining balance would be too small (${remaining_value:,.2f}), selling entire position for {symbol}"
                    )
                    trade_value = current.usd_value
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

        # トークンペアごとに取引を集約
        pair_trades: Dict[str, Dict] = {}
        for sell_trade in sell_trades:
            for buy_trade in buy_trades:
                pair_key = f"{sell_trade['mint']}->{buy_trade['mint']}"
                if pair_key not in pair_trades:
                    pair_trades[pair_key] = {
                        "type": "swap",
                        "from_symbol": sell_trade["symbol"],
                        "from_mint": sell_trade["mint"],
                        "from_amount": Decimal(0),
                        "to_symbol": buy_trade["symbol"],
                        "to_mint": buy_trade["mint"],
                        "to_amount": Decimal(0),
                        "usd_value": Decimal(0),
                    }

                # 取引価値の小さい方を基準に合成
                trade_value = min(sell_trade["usd_value"], buy_trade["usd_value"])
                if trade_value < self.risk_config.min_trade_size_usd:
                    continue

                pair_trades[pair_key]["from_amount"] += (
                    trade_value / sell_trade["usd_value"]
                ) * sell_trade["amount"]
                pair_trades[pair_key]["to_amount"] += (
                    trade_value / buy_trade["usd_value"]
                ) * buy_trade["amount"]
                pair_trades[pair_key]["usd_value"] += trade_value

                # 使用した取引価値を減算
                sell_trade["usd_value"] -= trade_value
                buy_trade["usd_value"] -= trade_value

        # 残りの売り注文を集約
        sell_aggregated: Dict[str, Dict] = {}
        for trade in sell_trades:
            if trade["usd_value"] < self.risk_config.min_trade_size_usd:
                continue

            mint = trade["mint"]
            if mint not in sell_aggregated:
                sell_aggregated[mint] = {
                    "type": "sell",
                    "symbol": trade["symbol"],
                    "mint": mint,
                    "amount": Decimal(0),
                    "usd_value": Decimal(0),
                }
            sell_aggregated[mint]["amount"] += trade["amount"]
            sell_aggregated[mint]["usd_value"] += trade["usd_value"]

        # 集約された取引を最大取引サイズで分割
        optimized_trades = []

        # スワップ取引の分割
        for trade in pair_trades.values():
            if trade["usd_value"] < self.risk_config.min_trade_size_usd:
                continue

            remaining_value = trade["usd_value"]
            while remaining_value > 0:
                batch_value = min(remaining_value, self.risk_config.max_trade_size_usd)
                if batch_value < self.risk_config.min_trade_size_usd:
                    break

                ratio = batch_value / trade["usd_value"]
                optimized_trades.append(
                    {
                        "type": "swap",
                        "from_symbol": trade["from_symbol"],
                        "from_mint": trade["from_mint"],
                        "from_amount": trade["from_amount"] * ratio,
                        "to_symbol": trade["to_symbol"],
                        "to_mint": trade["to_mint"],
                        "to_amount": trade["to_amount"] * ratio,
                        "usd_value": batch_value,
                    }
                )
                remaining_value -= batch_value

        # 売り取引の分割
        for trade in sell_aggregated.values():
            remaining_value = trade["usd_value"]
            while remaining_value > 0:
                batch_value = min(remaining_value, self.risk_config.max_trade_size_usd)
                if batch_value < self.risk_config.min_trade_size_usd:
                    break

                ratio = batch_value / trade["usd_value"]
                optimized_trades.append(
                    {
                        "type": "sell",
                        "symbol": trade["symbol"],
                        "mint": trade["mint"],
                        "amount": trade["amount"] * ratio,
                        "usd_value": batch_value,
                    }
                )
                remaining_value -= batch_value

        # 残りの買い注文を追加（未使用分）
        remaining_buys = [
            t
            for t in buy_trades
            if t["usd_value"] >= self.risk_config.min_trade_size_usd
        ]
        optimized_trades.extend(remaining_buys)

        return optimized_trades
