"""Request bodies for the Phase 3A seating API. Unknown fields are rejected (so a client can never smuggle a price into a quote).
seating.py validates everything again; these models give clear 422 messages and OpenAPI docs."""
from decimal import Decimal
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


Price = Decimal


class CategorySpec(_Strict):
    name: str = Field(min_length=1, max_length=50)
    base_price: Decimal = Field(ge=0, le=1000000, max_digits=10, decimal_places=2)
    color: Optional[str] = Field(default=None, max_length=7)
    description: Optional[str] = Field(default=None, max_length=200)


class RowSpec(_Strict):
    label: str = Field(min_length=1, max_length=8)
    seat_count: int = Field(ge=1, le=500)
    start_number: int = Field(default=1, ge=1, le=9999)
    category: Optional[str] = Field(default=None, max_length=50)


class SectionSpec(_Strict):
    name: str = Field(min_length=2, max_length=60)
    code: Optional[str] = Field(default=None, max_length=12)
    category: str = Field(min_length=1, max_length=50)
    rows: list[RowSpec] = Field(default_factory=list, max_length=200)


class StandSpec(_Strict):
    name: str = Field(min_length=2, max_length=60)
    code: Optional[str] = Field(default=None, max_length=12)
    sections: list[SectionSpec] = Field(default_factory=list, max_length=100)


class LayoutIn(_Strict):
    stadium_id: int
    name: str = Field(min_length=2, max_length=80)
    categories: list[CategorySpec] = Field(default_factory=list, max_length=30)
    stands: list[StandSpec] = Field(default_factory=list, max_length=30)


class LayoutUpdate(_Strict):
    name: Optional[str] = Field(default=None, min_length=2, max_length=80)
    is_active: Optional[bool] = None


class CategoryIn(_Strict):
    name: str = Field(min_length=1, max_length=50)
    base_price: Decimal = Field(ge=0, le=1000000, max_digits=10, decimal_places=2)
    color: Optional[str] = Field(default=None, max_length=7)
    description: Optional[str] = Field(default=None, max_length=200)


class CategoryUpdate(_Strict):
    name: Optional[str] = Field(default=None, min_length=1, max_length=50)
    base_price: Optional[Decimal] = Field(default=None, ge=0, le=1000000, max_digits=10, decimal_places=2)
    color: Optional[str] = Field(default=None, max_length=7)
    description: Optional[str] = Field(default=None, max_length=200)


class StandIn(_Strict):
    name: str = Field(min_length=2, max_length=60)
    code: Optional[str] = Field(default=None, max_length=12)


class StandUpdate(_Strict):
    name: Optional[str] = Field(default=None, min_length=2, max_length=60)
    is_active: Optional[bool] = None


class SectionIn(_Strict):
    name: str = Field(min_length=2, max_length=60)
    code: Optional[str] = Field(default=None, max_length=12)
    category_id: int


class SectionUpdate(_Strict):
    name: Optional[str] = Field(default=None, min_length=2, max_length=60)
    category_id: Optional[int] = None
    is_active: Optional[bool] = None


class RowIn(_Strict):
    label: str = Field(min_length=1, max_length=8)
    seat_count: int = Field(ge=1, le=500)
    start_number: int = Field(default=1, ge=1, le=9999)
    category_id: Optional[int] = None


class RowUpdate(_Strict):
    label: Optional[str] = Field(default=None, min_length=1, max_length=8)
    seat_count: Optional[int] = Field(default=None, ge=1, le=500)
    category_id: Optional[int] = None


class SeatIn(_Strict):
    seat_number: int = Field(ge=1, le=9999)
    category_id: Optional[int] = None
    price_override: Optional[Decimal] = Field(default=None, ge=0, le=1000000, max_digits=10, decimal_places=2)


class SeatUpdate(_Strict):
    category_id: Optional[int] = None
    price_override: Optional[Decimal] = Field(default=None, ge=0, le=1000000, max_digits=10, decimal_places=2)   # explicit null clears it
    is_active: Optional[bool] = None


class AssignLayoutIn(_Strict):
    layout_id: int


class SeatStatusIn(_Strict):
    seat_ids: list[int] = Field(min_length=1, max_length=2000)
    status: Literal["blocked", "available"]


class SeatQuoteIn(_Strict):
    seat_ids: list[int] = Field(min_length=1, max_length=100)   # ids only: there is deliberately no price field


class SeatBookingIn(_Strict):
    match_id: int
    seat_ids: list[int] = Field(min_length=1, max_length=100)   # ids only: prices, fees and tax are always calculated by the server
