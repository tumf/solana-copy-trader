from decimal import Decimal
from typing import Dict, List, Optional

from logger import logger
from models import RiskConfig, SwapTrade, TokenAlias, Trade
from network.solana import SOL_MINT, USDC_MINT
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
        sell_trades: List[SwapTrade] = []  # USDCへの売り取引
        buy_trades: List[SwapTrade] = []  # USDCからの買い取引

        # 現在の総資産価値を取得
        current_total = Decimal(str(current_portfolio.total_value_usd))

        # すべてのユニークなトークン（元の表記）を収集し、あらかじめ解決済みのアドレスを算出
        all_tokens = set(current_portfolio.token_balances.keys()).union(
            target_portfolio.token_balances.keys()
        )
        resolved_map: Dict[str, str] = {
            token: self.resolve_address(token) for token in all_tokens
        }

        # 価格取得のため、解決済みアドレスのリストを作成
        prices = await self.token_price_resolver.get_token_prices(
            list(resolved_map.values())
        )

        # --- 目標ポートフォリオの重み計算 ---
        def get_weight(token: str) -> Decimal:
            if token == USDC_MINT:
                scale = Decimal(1)
            elif token == SOL_MINT:
                scale = Decimal(1)
            else:
                scale = Decimal(self.risk_config.scaling_factor)

            return Decimal(str(target_portfolio.token_balances[token].weight)) * scale

        adjusted_target: Dict[str, Decimal] = {}
        total_adjusted_weight = Decimal(0)

        # まず全ての重みを集計
        for token in all_tokens:
            resolved_token = resolved_map[token]
            if token in target_portfolio.token_balances:
                weight = get_weight(token)
                adjusted_target[resolved_token] = (
                    adjusted_target.get(resolved_token, Decimal(0)) + weight
                )
                total_adjusted_weight += weight

        # 重みを正規化して合計を1にする
        if total_adjusted_weight > 0:
            for token in adjusted_target:
                adjusted_target[token] = adjusted_target[token] / total_adjusted_weight
            total_adjusted_weight = Decimal(1)

        # --- 現在のポートフォリオの重み計算 ---
        current_weights: Dict[str, Decimal] = {}
        for token in all_tokens:
            resolved_token = resolved_map[token]
            current = current_portfolio.token_balances.get(token)
            if current:
                cw = (
                    (Decimal(str(current.usd_value)) / current_total)
                    if current_total > 0
                    else Decimal(0)
                )
                current_weights[resolved_token] = (
                    current_weights.get(resolved_token, Decimal(0)) + cw
                )

        # --- トークンごとのトレード生成 ---
        for token in all_tokens:
            resolved_token = resolved_map[token]
            if resolved_token == USDC_MINT:
                continue

            price = prices.get(resolved_token, Decimal(0))
            current = current_portfolio.token_balances.get(token)
            target = target_portfolio.token_balances.get(token)
            symbol = (
                current.symbol
                if current
                else target.symbol if target else token[:8] + "..."
            )

            cw = current_weights.get(resolved_token, Decimal(0))
            tw = adjusted_target.get(resolved_token, Decimal(0))
            weight_diff = abs(tw - cw)

            # 目標の重みが閾値未満の場合は売却トレードのみ（保有中の場合）
            if tw < self.risk_config.min_weight_threshold:
                if (
                    current
                    and cw > 0
                    and current.usd_value > self.risk_config.min_trade_size_usd
                ):
                    sell_trades.append(
                        SwapTrade(
                            type="swap",
                            from_symbol=symbol,
                            from_mint=resolved_token,
                            from_amount=current.amount,
                            from_decimals=current.decimals,
                            to_symbol="USDC",
                            to_mint=USDC_MINT,
                            to_amount=current.usd_value,  # USDCは1:1換算
                            to_decimals=6,
                            usd_value=current.usd_value,
                        )
                    )
                continue

            # 許容誤差内の場合はスキップ
            if weight_diff <= self.risk_config.weight_tolerance:
                logger.debug(
                    f"Skipping {symbol} {token}: weight difference {weight_diff:.3%} within tolerance"
                )
                continue

            # 売り・買いの判断
            if tw > cw:
                # 買い注文：USDCからのスワップ
                trade_value = current_total * (tw - cw)
                batch_amount = trade_value / price if price > 0 else Decimal(0)
                if not (current or target):
                    logger.warning(
                        f"Skipping buy trade for {symbol}: No token information available"
                    )
                    continue

                token_decimals = (
                    current.decimals
                    if current and current.decimals
                    else target.decimals if target else None
                )
                if token_decimals is None:
                    logger.warning(
                        f"Skipping buy trade for {symbol}: Could not determine token decimals"
                    )
                    continue

                buy_trades.append(
                    SwapTrade(
                        type="swap",
                        from_symbol="USDC",
                        from_mint=USDC_MINT,
                        from_amount=trade_value,  # USDCは1:1換算
                        from_decimals=6,
                        to_symbol=symbol,
                        to_mint=resolved_token,
                        to_amount=batch_amount,
                        to_decimals=token_decimals,
                        usd_value=trade_value,
                    )
                )
            else:
                # 売り注文：USDCへのスワップ
                trade_value = current_total * (cw - tw)
                batch_amount = trade_value / price if price > 0 else Decimal(0)
                if not current:
                    logger.warning(
                        f"Skipping sell trade for {symbol}: No token information available"
                    )
                    continue

                token_decimals = current.decimals
                if not token_decimals:
                    logger.warning(
                        f"Skipping sell trade for {symbol}: Could not determine token decimals"
                    )
                    continue

                sell_trades.append(
                    SwapTrade(
                        type="swap",
                        from_symbol=symbol,
                        from_mint=resolved_token,
                        from_amount=batch_amount,
                        from_decimals=token_decimals,
                        to_symbol="USDC",
                        to_mint=USDC_MINT,
                        to_amount=trade_value,  # USDCは1:1換算
                        to_decimals=6,
                        usd_value=trade_value,
                    )
                )

        # --- 売り・買いトレードのマッチング（直接取引の生成） ---
        direct_trades: List[SwapTrade] = []
        remaining_sells: List[SwapTrade] = []
        remaining_buys: List[SwapTrade] = []

        # 買いトレードにフラグを追加（マッチ済みかどうか）
        for bt in buy_trades:
            bt.matched = False

        for sell in sell_trades:
            matched = False
            for buy in buy_trades:
                if not buy.matched:
                    # エイリアス解決を再度実施（既に解決済みであるはず）
                    if self.resolve_address(sell.to_mint) == self.resolve_address(
                        buy.from_mint
                    ):
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
                                    from_decimals=sell.from_decimals,
                                    to_symbol=buy.to_symbol,
                                    to_mint=self.resolve_address(buy.to_mint),
                                    to_amount=buy.to_amount * buy_ratio,
                                    to_decimals=buy.to_decimals,
                                    usd_value=match_value,
                                )
                            )

                            # 残余分を個別トレードとして記録
                            if sell.usd_value > match_value:
                                remaining_sells.append(
                                    SwapTrade(
                                        type="swap",
                                        from_symbol=sell.from_symbol,
                                        from_mint=self.resolve_address(sell.from_mint),
                                        from_amount=sell.from_amount * (1 - sell_ratio),
                                        from_decimals=sell.from_decimals,
                                        to_symbol=sell.to_symbol,
                                        to_mint=self.resolve_address(sell.to_mint),
                                        to_amount=sell.to_amount * (1 - sell_ratio),
                                        to_decimals=6,
                                        usd_value=sell.usd_value - match_value,
                                    )
                                )
                            if buy.usd_value > match_value:
                                remaining_buys.append(
                                    SwapTrade(
                                        type="swap",
                                        from_symbol=buy.from_symbol,
                                        from_mint=self.resolve_address(buy.from_mint),
                                        from_amount=buy.from_amount * (1 - buy_ratio),
                                        from_decimals=6,
                                        to_symbol=buy.to_symbol,
                                        to_mint=self.resolve_address(buy.to_mint),
                                        to_amount=buy.to_amount * (1 - buy_ratio),
                                        to_decimals=buy.to_decimals,
                                        usd_value=buy.usd_value - match_value,
                                    )
                                )
                            buy.matched = True
                            matched = True
                            break
            if not matched:
                remaining_sells.append(sell)

        # Collect unmatched buy trades
        remaining_buys.extend([buy for buy in buy_trades if not buy.matched])

        # --- Combine and optimize all trades ---
        all_trades = direct_trades + remaining_sells + remaining_buys

        # Aggregate trades by token pair
        pair_trades: Dict[str, SwapTrade] = {}

        # First pass to aggregate trades
        for trade in all_trades:
            # Validate addresses before processing
            from_mint = self.resolve_address(trade.from_mint)
            to_mint = self.resolve_address(trade.to_mint)

            # Skip trades with invalid addresses (longer than 44 chars)
            if len(from_mint) > 44 or len(to_mint) > 44:
                logger.warning(
                    f"Skipping trade with invalid addresses: {trade.from_symbol} -> {trade.to_symbol}"
                )
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

            # Record trades using intermediate tokens
            if to_mint == USDC_MINT:
                if from_mint not in pair_trades:
                    pair_trades[from_mint] = SwapTrade(
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
                pair_trades[from_mint].from_amount += trade.from_amount
                pair_trades[from_mint].to_amount += trade.to_amount
                pair_trades[from_mint].usd_value += trade.usd_value

        # Convert trades using intermediate tokens to direct trades
        optimized_pairs: Dict[str, SwapTrade] = {}

        # Find trade pairs using the same intermediate token
        for from_mint, from_trades in pair_trades.items():
            # Skip invalid addresses
            if len(from_mint) > 44:
                continue

            for to_mint, to_trades in pair_trades.items():
                # Skip invalid addresses
                if len(to_mint) > 44:
                    continue

                if from_mint != to_mint:
                    # Find trade pairs using the same intermediate token
                    for from_trade in from_trades:
                        for to_trade in to_trades:
                            if (
                                from_trade.to_mint == USDC_MINT
                                and to_trade.from_mint == USDC_MINT
                            ):
                                # Create direct trade based on the smaller trade size
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

                                    # Reduce used portion from original trades
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

        # Add remaining trades
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

        # Optimize and combine trades
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
