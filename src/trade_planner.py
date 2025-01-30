from decimal import Decimal
from typing import Dict, List
from pydantic import BaseModel, Field, ConfigDict
from portfolio import Portfolio
from token_price_resolver import TokenPriceResolver
from logger import logger
from network import USDC_MINT, SOL_MINT
from models import Trade, SwapTrade, RiskConfig

logger = logger.bind(name="trade_planner")


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
    ) -> List[Trade]:
        """Create trade plan to match target portfolio with risk management and tolerance"""
        # 売りと買いの取引を分けて集計
        sell_trades = []  # USDCへの売り取引
        buy_trades = []   # USDCからの買い取引

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
                weight = Decimal(str(target_portfolio.token_balances[mint].weight))
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
                    Decimal(str(current.usd_value)) / current_total
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
                    # 保有していて、目標が最小閾値未満なら売却（USDCへのスワップ）
                    sell_trades.append(
                        {
                            "type": "swap",
                            "from_symbol": symbol,
                            "from_mint": mint,
                            "from_amount": current.amount,
                            "to_symbol": "USDC",
                            "to_mint": USDC_MINT,
                            "to_amount": current.usd_value,  # USDCは1:1
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
                # 買い注文：USDCからのスワップ
                trade_value = current_total * (target_weight - current_weight)
                batch_amount = trade_value / price if price > 0 else Decimal(0)
                buy_trades.append(
                    {
                        "type": "swap",
                        "from_symbol": "USDC",
                        "from_mint": USDC_MINT,
                        "from_amount": trade_value,  # USDCは1:1
                        "to_symbol": symbol,
                        "to_mint": mint,
                        "to_amount": batch_amount,
                        "usd_value": trade_value,
                    }
                )
            else:
                # 売り注文：USDCへのスワップ
                trade_value = current_total * (current_weight - target_weight)
                batch_amount = trade_value / price if price > 0 else Decimal(0)
                sell_trades.append(
                    {
                        "type": "swap",
                        "from_symbol": symbol,
                        "from_mint": mint,
                        "from_amount": batch_amount,
                        "to_symbol": "USDC",
                        "to_mint": USDC_MINT,
                        "to_amount": trade_value,  # USDCは1:1
                        "usd_value": trade_value,
                    }
                )

        # 直接のトークン間取引を作成
        direct_trades = []
        remaining_sells = []
        remaining_buys = []

        # 売り取引と買い取引をマッチング
        for sell in sell_trades:
            matched = False
            for buy in buy_trades:
                if not buy.get("matched"):
                    # 取引サイズの小さい方を基準に取引を作成
                    match_value = min(sell["usd_value"], buy["usd_value"])
                    if match_value >= self.risk_config.min_trade_size_usd:
                        sell_ratio = match_value / sell["usd_value"]
                        buy_ratio = match_value / buy["usd_value"]
                        
                        direct_trades.append({
                            "type": "swap",
                            "from_symbol": sell["from_symbol"],
                            "from_mint": sell["from_mint"],
                            "from_amount": sell["from_amount"] * sell_ratio,
                            "to_symbol": buy["to_symbol"],
                            "to_mint": buy["to_mint"],
                            "to_amount": buy["to_amount"] * buy_ratio,
                            "usd_value": match_value,
                        })

                        # 残りの取引を更新
                        if sell["usd_value"] > match_value:
                            remaining_value = sell["usd_value"] - match_value
                            remaining_ratio = remaining_value / sell["usd_value"]
                            remaining_sells.append({
                                **sell,
                                "from_amount": sell["from_amount"] * remaining_ratio,
                                "to_amount": sell["to_amount"] * remaining_ratio,
                                "usd_value": remaining_value,
                            })
                        
                        if buy["usd_value"] > match_value:
                            remaining_value = buy["usd_value"] - match_value
                            remaining_ratio = remaining_value / buy["usd_value"]
                            remaining_buys.append({
                                **buy,
                                "from_amount": buy["from_amount"] * remaining_ratio,
                                "to_amount": buy["to_amount"] * remaining_ratio,
                                "usd_value": remaining_value,
                            })
                        
                        buy["matched"] = True
                        matched = True
                        break
            
            if not matched:
                remaining_sells.append(sell)

        # マッチしなかった買い取引を追加
        remaining_buys.extend([buy for buy in buy_trades if not buy.get("matched")])

        # 全ての取引を結合して最適化
        all_trades = direct_trades + remaining_sells + remaining_buys
        return self._optimize_trades(all_trades)

    def _optimize_trades(self, trades: List[Dict]) -> List[Trade]:
        """取引を最適化して合成する"""
        if not trades:
            return []

        # トークンペアごとに取引を集約
        pair_trades: Dict[str, SwapTrade] = {}
        intermediate_trades: Dict[str, List[SwapTrade]] = {}

        # 最初のパスで取引を集約
        for trade in trades:
            pair_key = f"{trade['from_mint']}->{trade['to_mint']}"
            if pair_key not in pair_trades:
                pair_trades[pair_key] = SwapTrade(
                    type="swap",
                    from_symbol=trade["from_symbol"],
                    from_mint=trade["from_mint"],
                    from_amount=Decimal(0),
                    to_symbol=trade["to_symbol"],
                    to_mint=trade["to_mint"],
                    to_amount=Decimal(0),
                    usd_value=Decimal(0),
                )

            pair_trades[pair_key].from_amount += trade["from_amount"]
            pair_trades[pair_key].to_amount += trade["to_amount"]
            pair_trades[pair_key].usd_value += trade["usd_value"]

            # 中間トークンを使用する取引を記録
            if trade["to_mint"] == USDC_MINT:
                from_key = trade["from_mint"]
                if from_key not in intermediate_trades:
                    intermediate_trades[from_key] = []
                intermediate_trades[from_key].append(pair_trades[pair_key])
            elif trade["from_mint"] == USDC_MINT:
                to_key = trade["to_mint"]
                if to_key not in intermediate_trades:
                    intermediate_trades[to_key] = []
                intermediate_trades[to_key].append(pair_trades[pair_key])

        # 中間トークンを使用する取引を直接取引に変換
        optimized_pairs: Dict[str, SwapTrade] = {}
        for from_mint, from_trades in intermediate_trades.items():
            for to_mint, to_trades in intermediate_trades.items():
                if from_mint != to_mint:
                    # 同じ中間トークンを使用する取引ペアを見つける
                    for from_trade in from_trades:
                        for to_trade in to_trades:
                            if (from_trade.to_mint == USDC_MINT and 
                                to_trade.from_mint == USDC_MINT):
                                # 取引サイズの小さい方を基準に直接取引を作成
                                match_value = min(from_trade.usd_value, to_trade.usd_value)
                                if match_value >= self.risk_config.min_trade_size_usd:
                                    from_ratio = match_value / from_trade.usd_value
                                    to_ratio = match_value / to_trade.usd_value

                                    direct_key = f"{from_mint}->{to_mint}"
                                    if direct_key not in optimized_pairs:
                                        optimized_pairs[direct_key] = SwapTrade(
                                            type="swap",
                                            from_symbol=from_trade.from_symbol,
                                            from_mint=from_mint,
                                            from_amount=Decimal(0),
                                            to_symbol=to_trade.to_symbol,
                                            to_mint=to_mint,
                                            to_amount=Decimal(0),
                                            usd_value=Decimal(0),
                                        )

                                    direct_trade = optimized_pairs[direct_key]
                                    direct_trade.from_amount += from_trade.from_amount * from_ratio
                                    direct_trade.to_amount += to_trade.to_amount * to_ratio
                                    direct_trade.usd_value += match_value

                                    # 元の取引から使用した分を減らす
                                    from_trade.from_amount -= from_trade.from_amount * from_ratio
                                    from_trade.to_amount -= from_trade.to_amount * from_ratio
                                    from_trade.usd_value -= match_value

                                    to_trade.from_amount -= to_trade.from_amount * to_ratio
                                    to_trade.to_amount -= to_trade.to_amount * to_ratio
                                    to_trade.usd_value -= match_value

        # 残りの取引を追加
        for trade in pair_trades.values():
            if trade.usd_value >= self.risk_config.min_trade_size_usd:
                pair_key = f"{trade.from_mint}->{trade.to_mint}"
                if pair_key not in optimized_pairs:
                    optimized_pairs[pair_key] = trade
                else:
                    optimized_pairs[pair_key].from_amount += trade.from_amount
                    optimized_pairs[pair_key].to_amount += trade.to_amount
                    optimized_pairs[pair_key].usd_value += trade.usd_value

        # 最適化された取引を最大取引サイズで分割
        optimized_trades: List[Trade] = []
        for trade in optimized_pairs.values():
            if trade.usd_value < self.risk_config.min_trade_size_usd:
                continue

            remaining_value = trade.usd_value
            while remaining_value > 0:
                batch_value = min(remaining_value, self.risk_config.max_trade_size_usd)
                if batch_value < self.risk_config.min_trade_size_usd:
                    break

                ratio = batch_value / trade.usd_value
                optimized_trades.append(
                    SwapTrade(
                        type="swap",
                        from_symbol=trade.from_symbol,
                        from_mint=trade.from_mint,
                        from_amount=trade.from_amount * ratio,
                        to_symbol=trade.to_symbol,
                        to_mint=trade.to_mint,
                        to_amount=trade.to_amount * ratio,
                        usd_value=batch_value,
                    )
                )
                remaining_value -= batch_value

        return optimized_trades
