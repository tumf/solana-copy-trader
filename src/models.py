from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Token(Base):
    __tablename__ = "tokens"

    address: Mapped[str] = mapped_column(String, primary_key=True)
    symbol: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    decimals: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String)
    last_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SwapTrade(BaseModel):
    """スワップ取引"""

    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)

    type: str = "swap"
    from_symbol: str
    from_mint: str
    from_amount: Decimal
    from_decimals: int
    to_symbol: str
    to_mint: str
    to_amount: Decimal
    to_decimals: int
    usd_value: Decimal
    matched: bool = False

    @property
    def from_amount_lamports(self) -> int:
        return int(self.from_amount * 10**self.from_decimals)


Trade = SwapTrade


class RiskConfig(BaseModel):
    """取引のリスク設定を管理するモデル"""

    model_config = ConfigDict(
        validate_assignment=True,
        json_schema_extra={
            "example": {
                "max_trade_size_usd": "1000",
                "min_trade_size_usd": "10",
                "max_slippage_bps": 100,
                "max_portfolio_allocation": "0.25",
                "gas_buffer_sol": "0.1",
                "weight_tolerance": "0.02",
                "min_weight_threshold": "0.01",
            }
        },
    )

    max_trade_size_usd: Decimal = Field(
        description="1回の取引の最大サイズ（USD）", gt=0
    )
    min_trade_size_usd: Decimal = Field(
        description="1回の取引の最小サイズ（USD）", gt=0
    )
    max_slippage_bps: int = Field(
        description="許容する最大スリッページ（ベーシスポイント）", ge=0, le=10000
    )
    max_portfolio_allocation: Decimal = Field(
        description="1つのトークンの最大配分比率", gt=0, le=1
    )
    gas_buffer_sol: Decimal = Field(description="ガス代のバッファ（SOL）", gt=0)
    weight_tolerance: Decimal = Field(
        description="ポートフォリオの重みの許容誤差", gt=0, le=1
    )
    min_weight_threshold: Decimal = Field(
        description="ポートフォリオの最小重み閾値", gt=0, le=1
    )


class TokenAlias(BaseModel):
    address: str
    aliases: List[str]


class SwapQuote(BaseModel):
    input_mint: str
    output_mint: str
    input_amount: int
    expected_output_amount: int
    price_impact_pct: Decimal
    minimum_output_amount: int
    dex_name: str


class SwapResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    success: bool
    tx_signature: Optional[str]
    error_message: Optional[str] = None
