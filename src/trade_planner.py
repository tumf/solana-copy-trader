from decimal import Decimal
from typing import Dict, List, Optional

from logger import logger
from models import RiskConfig, SwapTrade, TokenAlias, Trade
from network import USDC_MINT
from portfolio import Portfolio
from token_price_resolver import TokenPriceResolver
from token_resolver import TokenResolver

logger = logger.bind(name="trade_planner")


class TradePlanner:
    def __init__(
        self,
        risk_config: RiskConfig,
        token_aliases: Optional[List[TokenAlias]] = None,
        token_price_resolver: Optional[TokenPriceResolver] = None,
        token_resolver: Optional[TokenResolver] = None,
    ):
        self.risk_config = risk_config
        self.token_price_resolver = token_price_resolver or TokenPriceResolver()
        self.token_resolver = token_resolver or TokenResolver()

        # トークンの置換マップを作成 (例: USDT -> USDC)
        self.token_replacement_map: Dict[str, str] = {}
        if token_aliases:
            for alias in token_aliases:
                # 置換対象のトークン(USDT)から、置換先のトークン(USDC)へのマッピング
                for replaceable_token in alias.aliases:
                    self.token_replacement_map[replaceable_token] = alias.address

    def resolve_address(self, address: str) -> str:
        """Resolve token address using replacement map"""
        # Base58 encoded Solana addresses are typically 32-44 characters
        # If longer, it's likely a signature data, so return it unchanged
        if len(address) > 44:
            return address
        return self.token_replacement_map.get(address, address)

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
        sell_trades: List[SwapTrade] = []  # USDCへの売り取引
        buy_trades: List[SwapTrade] = []  # USDCからの買い取引

        # 現在と目標の総資産価値を取得
        current_total = Decimal(str(current_portfolio.total_value_usd))

        # すべてのユニークなトークンを収集
        all_tokens = set(
            list(current_portfolio.token_balances.keys())
            + list(target_portfolio.token_balances.keys())
        )

        # Get all token prices at once
        # トークンの置換を適用してからpricesを取得 (例: USDT -> USDC)
        resolved_tokens = [self.resolve_address(token) for token in all_tokens]
        prices = await self.token_price_resolver.get_token_prices(resolved_tokens)

        # トークンの重みを計算
        adjusted_target: Dict[str, Decimal] = {}
        total_adjusted_weight = Decimal(0)
        for mint in all_tokens:
            resolved_mint = self.resolve_address(mint)
            if resolved_mint == USDC_MINT:
                continue
            if mint in target_portfolio.token_balances:
                weight = Decimal(str(target_portfolio.token_balances[mint].weight))
                # 置換されたトークンの重みを合算 (例: USDTの重みをUSDCに合算)
                if resolved_mint in adjusted_target:
                    adjusted_target[resolved_mint] += weight
                else:
                    adjusted_target[resolved_mint] = weight
                total_adjusted_weight += weight

        # USDCのウェイトを残りの割合に設定
        usdc_weight = Decimal(1) - total_adjusted_weight
        if USDC_MINT in target_portfolio.token_balances:
            adjusted_target[USDC_MINT] = usdc_weight

        # 現在のポートフォリオの重みを計算
        current_weights: Dict[str, Decimal] = {}
        for mint in all_tokens:
            resolved_mint = self.resolve_address(mint)
            current = current_portfolio.token_balances.get(mint)
            if current:
                # 置換されたトークンの重みを合算 (例: USDTの重みをUSDCに合算)
                current_weight = (
                    Decimal(str(current.usd_value)) / current_total
                    if current_total > 0
                    else Decimal(0)
                )
                if resolved_mint in current_weights:
                    current_weights[resolved_mint] += current_weight
                else:
                    current_weights[resolved_mint] = current_weight

        # トレードを生成
        for mint in all_tokens:
            resolved_mint = self.resolve_address(mint)
            if resolved_mint == USDC_MINT:
                continue
            # Get token price from cache
            price = prices.get(resolved_mint, Decimal(0))
            current = current_portfolio.token_balances.get(mint)
            target = target_portfolio.token_balances.get(mint)
            symbol = (
                current.symbol
                if current
                else target.symbol if target else mint[:8] + "..."
            )

            # 現在と目標の重みを取得
            current_weight = current_weights.get(resolved_mint, Decimal(0))
            target_weight = adjusted_target.get(resolved_mint, Decimal(0))

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
                        SwapTrade(
                            type="swap",
                            from_symbol=symbol,
                            from_mint=resolved_mint,  # 置換後のアドレスを使用
                            from_amount=current.amount,
                            from_decimals=current.decimals,
                            to_symbol="USDC",
                            to_mint=USDC_MINT,  # 直接USDC_MINTを使用
                            to_amount=current.usd_value,  # USDCは1:1
                            to_decimals=6,
                            usd_value=current.usd_value,
                        )
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

                # トークン情報が取得できない場合はスキップ
                if not current and not target:
                    logger.warning(f"Skipping buy trade for {symbol}: No token information available")
                    continue

                # decimalsはcurrentかtargetから取得
                token_decimals = (current and current.decimals) or (target and target.decimals)
                if not token_decimals:
                    logger.warning(f"Skipping buy trade for {symbol}: Could not determine token decimals")
                    continue

                buy_trades.append(
                    SwapTrade(
                        type="swap",
                        from_symbol="USDC",
                        from_mint=USDC_MINT,  # 直接USDC_MINTを使用
                        from_amount=trade_value,  # USDCは1:1
                        from_decimals=6,  # USDCは常に6 decimals
                        to_symbol=symbol,
                        to_mint=resolved_mint,  # 置換後のアドレスを使用
                        to_amount=batch_amount,
                        to_decimals=token_decimals,
                        usd_value=trade_value,
                    )
                )
            else:
                # 売り注文：USDCへのスワップ
                trade_value = current_total * (current_weight - target_weight)
                batch_amount = trade_value / price if price > 0 else Decimal(0)

                # トークン情報が取得できない場合はスキップ
                if not current:
                    logger.warning(f"Skipping sell trade for {symbol}: No token information available")
                    continue

                # decimalsを取得
                token_decimals = current.decimals
                if not token_decimals:
                    logger.warning(f"Skipping sell trade for {symbol}: Could not determine token decimals")
                    continue

                sell_trades.append(
                    SwapTrade(
                        type="swap",
                        from_symbol=symbol,
                        from_mint=resolved_mint,  # 置換後のアドレスを使用
                        from_amount=batch_amount,
                        from_decimals=token_decimals,
                        to_symbol="USDC",
                        to_mint=USDC_MINT,  # 直接USDC_MINTを使用
                        to_amount=trade_value,  # USDCは1:1
                        to_decimals=6,  # USDCは常に6 decimals
                        usd_value=trade_value,
                    )
                )

        # 直接のトークン間取引を作成
        direct_trades: List[SwapTrade] = []
        remaining_sells: List[SwapTrade] = []
        remaining_buys: List[SwapTrade] = []

        # 売り取引と買い取引をマッチング
        i = 0
        while i < len(sell_trades):
            sell = sell_trades[i]
            matched = False
            j = 0
            while j < len(buy_trades):
                buy = buy_trades[j]
                if not buy.matched:
                    # エイリアスを考慮してトークンを比較
                    sell_to_mint = self.resolve_address(sell.to_mint)
                    buy_from_mint = self.resolve_address(buy.from_mint)

                    if sell_to_mint == buy_from_mint:
                        # 取引サイズの小さい方を基準に取引を作成
                        match_value = min(sell.usd_value, buy.usd_value)
                        if match_value >= self.risk_config.min_trade_size_usd:
                            sell_ratio = match_value / sell.usd_value
                            buy_ratio = match_value / buy.usd_value

                            direct_trades.append(
                                SwapTrade(
                                    type="swap",
                                    from_symbol=sell.from_symbol,
                                    from_mint=self.resolve_address(sell.from_mint),
                                    from_amount=sell.from_amount * sell_ratio,
                                    from_decimals=sell.from_decimals,  # 売り注文のdecimals
                                    to_symbol=buy.to_symbol,
                                    to_mint=self.resolve_address(buy.to_mint),
                                    to_amount=buy.to_amount * buy_ratio,
                                    to_decimals=buy.to_decimals,  # 買い注文のdecimals
                                    usd_value=match_value,
                                )
                            )

                            # 残りの取引を更新
                            if sell.usd_value > match_value:
                                remaining_value = sell.usd_value - match_value
                                remaining_ratio = remaining_value / sell.usd_value
                                remaining_sells.append(
                                    SwapTrade(
                                        type="swap",
                                        from_symbol=sell.from_symbol,
                                        from_mint=self.resolve_address(sell.from_mint),
                                        from_amount=sell.from_amount * remaining_ratio,
                                        from_decimals=sell.from_decimals,  # 売り注文のdecimals
                                        to_symbol=sell.to_symbol,
                                        to_mint=self.resolve_address(sell.to_mint),
                                        to_amount=sell.to_amount * remaining_ratio,
                                        to_decimals=6,  # USDCは常に6 decimals
                                        usd_value=remaining_value,
                                    )
                                )

                            if buy.usd_value > match_value:
                                remaining_value = buy.usd_value - match_value
                                remaining_ratio = remaining_value / buy.usd_value
                                remaining_buys.append(
                                    SwapTrade(
                                        type="swap",
                                        from_symbol=buy.from_symbol,
                                        from_mint=self.resolve_address(buy.from_mint),
                                        from_amount=buy.from_amount * remaining_ratio,
                                        from_decimals=6,  # USDCは常に6 decimals
                                        to_symbol=buy.to_symbol,
                                        to_mint=self.resolve_address(buy.to_mint),
                                        to_amount=buy.to_amount * remaining_ratio,
                                        to_decimals=buy.to_decimals,  # 買い注文のdecimals
                                        usd_value=remaining_value,
                                    )
                                )

                            buy.matched = True
                            matched = True
                            break
                j += 1

            if not matched:
                remaining_sells.append(sell)
            i += 1

        # マッチしなかった買い取引を追加
        remaining_buys.extend([buy for buy in buy_trades if not buy.matched])

        # 全ての取引を結合して最適化
        all_trades = direct_trades + remaining_sells + remaining_buys
        return self._optimize_trades(all_trades)

    def _optimize_trades(self, trades: List[Trade]) -> List[Trade]:
        """取引を最適化して合成する"""
        if not trades:
            return []

        # トークンペアごとに取引を集約
        pair_trades: Dict[str, SwapTrade] = {}
        intermediate_trades: Dict[str, List[SwapTrade]] = {}

        # 最初のパスで取引を集約
        for trade in trades:
            # Validate addresses before processing
            from_mint = self.resolve_address(trade.from_mint)
            to_mint = self.resolve_address(trade.to_mint)
            
            # Skip trades with invalid addresses (longer than 44 chars)
            if len(from_mint) > 44 or len(to_mint) > 44:
                logger.warning(f"Skipping trade with invalid addresses: {trade.from_symbol} -> {trade.to_symbol}")
                continue
                
            pair_key = f"{from_mint}->{to_mint}"
            if pair_key not in pair_trades:
                pair_trades[pair_key] = SwapTrade(
                    type="swap",
                    from_symbol=trade.from_symbol,
                    from_mint=from_mint,
                    from_amount=Decimal(0),
                    from_decimals=trade.from_decimals,
                    to_symbol=trade.to_symbol,
                    to_mint=to_mint,
                    to_amount=Decimal(0),
                    to_decimals=trade.to_decimals,
                    usd_value=Decimal(0),
                )

            pair_trades[pair_key].from_amount += trade.from_amount
            pair_trades[pair_key].to_amount += trade.to_amount
            pair_trades[pair_key].usd_value += trade.usd_value

            # 中間トークンを使用する取引を記録
            if to_mint == USDC_MINT:
                if from_mint not in intermediate_trades:
                    intermediate_trades[from_mint] = []
                intermediate_trades[from_mint].append(pair_trades[pair_key])
            elif from_mint == USDC_MINT:
                if to_mint not in intermediate_trades:
                    intermediate_trades[to_mint] = []
                intermediate_trades[to_mint].append(pair_trades[pair_key])

        # 中間トークンを使用する取引を直接取引に変換
        optimized_pairs: Dict[str, SwapTrade] = {}
        for from_mint, from_trades in intermediate_trades.items():
            # Skip invalid addresses
            if len(from_mint) > 44:
                continue
                
            for to_mint, to_trades in intermediate_trades.items():
                # Skip invalid addresses
                if len(to_mint) > 44:
                    continue
                    
                if from_mint != to_mint:
                    # 同じ中間トークンを使用する取引ペアを見つける
                    for from_trade in from_trades:
                        for to_trade in to_trades:
                            if (
                                from_trade.to_mint == USDC_MINT
                                and to_trade.from_mint == USDC_MINT
                            ):
                                # 取引サイズの小さい方を基準に直接取引を作成
                                match_value = min(
                                    from_trade.usd_value, to_trade.usd_value
                                )
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
                                            from_decimals=from_trade.from_decimals,
                                            to_symbol=to_trade.to_symbol,
                                            to_mint=to_mint,
                                            to_amount=Decimal(0),
                                            to_decimals=to_trade.to_decimals,
                                            usd_value=Decimal(0),
                                        )

                                    direct_trade = optimized_pairs[direct_key]
                                    direct_trade.from_amount += (
                                        from_trade.from_amount * from_ratio
                                    )
                                    direct_trade.to_amount += (
                                        to_trade.to_amount * to_ratio
                                    )
                                    direct_trade.usd_value += match_value

                                    # 元の取引から使用した分を減らす
                                    from_trade.from_amount -= (
                                        from_trade.from_amount * from_ratio
                                    )
                                    from_trade.to_amount -= (
                                        from_trade.to_amount * from_ratio
                                    )
                                    from_trade.usd_value -= match_value

                                    to_trade.from_amount -= (
                                        to_trade.from_amount * to_ratio
                                    )
                                    to_trade.to_amount -= to_trade.to_amount * to_ratio
                                    to_trade.usd_value -= match_value

        # 残りの取引を追加
        for trade in pair_trades.values():
            if trade.usd_value >= self.risk_config.min_trade_size_usd:
                # Skip trades with invalid addresses
                if len(trade.from_mint) > 44 or len(trade.to_mint) > 44:
                    continue
                    
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

            # Final validation of addresses
            if len(trade.from_mint) > 44 or len(trade.to_mint) > 44:
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
                        from_decimals=trade.from_decimals,
                        to_symbol=trade.to_symbol,
                        to_mint=trade.to_mint,
                        to_amount=trade.to_amount * ratio,
                        to_decimals=trade.to_decimals,
                        usd_value=batch_value,
                    )
                )
                remaining_value -= batch_value

        return optimized_trades
