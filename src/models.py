from datetime import datetime
from sqlalchemy import String, Integer, DateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict


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
    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)

    type: Literal["swap"]
    from_symbol: str
    from_mint: str
    from_amount: Decimal
    to_symbol: str
    to_mint: str
    to_amount: Decimal
    usd_value: Decimal


Trade = SwapTrade 