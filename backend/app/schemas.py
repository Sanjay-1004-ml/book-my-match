import re
from datetime import date, time
from decimal import Decimal
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    phone: Optional[str] = Field(default=None, max_length=20)
    password: str = Field(min_length=8, max_length=72)
    confirm_password: str

    @field_validator("phone")
    @classmethod
    def _phone(cls, v):
        if v and not re.fullmatch(r"[+0-9 \-]{7,20}", v):
            raise ValueError("Invalid phone number")
        return v or None

    @field_validator("password")
    @classmethod
    def _pw(cls, v):
        if not (re.search(r"[A-Za-z]", v) and re.search(r"\d", v)):
            raise ValueError("Password must contain a letter and a digit")
        return v

    @model_validator(mode="after")
    def _match(self):
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match")
        return self


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=72)


class ProfileIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    phone: Optional[str] = Field(default=None, max_length=20)


class FoodLineIn(BaseModel):
    item_id: int
    quantity: int = Field(ge=1, le=20)


class BookingIn(BaseModel):
    match_id: int
    category_id: int
    quantity: int = Field(ge=1, le=100)
    food: list[FoodLineIn] = Field(default_factory=list, max_length=30)


class FoodOrderIn(BaseModel):
    items: list[FoodLineIn] = Field(min_length=1, max_length=30)
    booking_id: Optional[int] = None


class FoodQuoteIn(BaseModel):
    """Cart contents to price. The client never sends a price; an empty cart is allowed (returns an empty bill)."""
    items: list[FoodLineIn] = Field(default_factory=list, max_length=30)


def _clean_image_url(v):
    if v is None:
        return v
    v = v.strip()
    if v and not re.match(r"(https?://|/)", v, re.I):
        raise ValueError("Image URL must start with http://, https:// or /")
    return v


# ---- admin ----
class SportIn(BaseModel):
    name: str = Field(min_length=2, max_length=50)
    description: Optional[str] = Field(default=None, max_length=1000)


class StadiumIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    location: str = Field(min_length=2, max_length=100)
    capacity: int = Field(gt=0, le=500000)
    sport_id: int
    image_url: Optional[str] = Field(default=None, max_length=500)

    @field_validator("image_url")
    @classmethod
    def _img(cls, v):
        return _clean_image_url(v) or None


class StadiumUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=100)
    location: Optional[str] = Field(default=None, min_length=2, max_length=100)
    capacity: Optional[int] = Field(default=None, gt=0, le=500000)
    sport_id: Optional[int] = None
    image_url: Optional[str] = Field(default=None, max_length=500)   # "" clears it

    @field_validator("image_url")
    @classmethod
    def _img(cls, v):
        return _clean_image_url(v)


class MatchIn(BaseModel):
    stadium_id: int
    team_a: str = Field(min_length=1, max_length=60)
    team_b: str = Field(min_length=1, max_length=60)
    match_date: date
    start_time: time
    timing: Optional[str] = Field(default=None, max_length=50)
    status: str = Field(default="scheduled", pattern="^(scheduled|cancelled|completed)$")
    tournament: Optional[str] = Field(default=None, max_length=80)

    @field_validator("tournament")
    @classmethod
    def _tour(cls, v):
        return (v or "").strip() or None


class MatchUpdate(BaseModel):
    stadium_id: Optional[int] = None
    team_a: Optional[str] = Field(default=None, min_length=1, max_length=60)
    team_b: Optional[str] = Field(default=None, min_length=1, max_length=60)
    match_date: Optional[date] = None
    start_time: Optional[time] = None
    timing: Optional[str] = Field(default=None, max_length=50)
    status: Optional[str] = Field(default=None, pattern="^(scheduled|cancelled|completed)$")
    tournament: Optional[str] = Field(default=None, max_length=80)   # null or "" clears it

    @field_validator("tournament")
    @classmethod
    def _tour(cls, v):
        return (v or "").strip() or None


class CategoryIn(BaseModel):
    stadium_id: int
    name: str = Field(min_length=1, max_length=50)
    price: Decimal = Field(ge=0, max_digits=10, decimal_places=2)
    tickets_per_match: int = Field(default=100, ge=0)


class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=50)
    price: Optional[Decimal] = Field(default=None, ge=0, max_digits=10, decimal_places=2)


class InventoryIn(BaseModel):
    match_id: int
    category_id: int
    total: int = Field(ge=0, le=500000)


class FoodCategoryIn(BaseModel):
    name: str = Field(min_length=2, max_length=50)


class FoodItemIn(BaseModel):
    category_id: int
    name: str = Field(min_length=1, max_length=80)
    price: Decimal = Field(ge=0, max_digits=10, decimal_places=2)
    is_active: bool = True
    description: Optional[str] = Field(default=None, max_length=300)
    image_url: Optional[str] = Field(default=None, max_length=500)

    @field_validator("image_url")
    @classmethod
    def _img(cls, v):
        return _clean_image_url(v) or None


class FoodItemUpdate(BaseModel):
    category_id: Optional[int] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    price: Optional[Decimal] = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    is_active: Optional[bool] = None
    description: Optional[str] = Field(default=None, max_length=300)   # "" clears it
    image_url: Optional[str] = Field(default=None, max_length=500)     # "" clears it

    @field_validator("image_url")
    @classmethod
    def _img(cls, v):
        return _clean_image_url(v)


class UserUpdate(BaseModel):
    role: Optional[str] = Field(default=None, pattern="^(user|staff|admin)$")
    is_active: Optional[bool] = None


# ---- demo payments / ticket verification ----
class PayIn(BaseModel):
    """Deliberately has no card / UPI / bank fields, and extra fields are rejected, so sensitive data can never be posted.
    Pay either a booking (tickets + any food attached to it) or one standalone food order, never both at once."""
    model_config = ConfigDict(extra="forbid")
    booking_id: Optional[int] = None
    food_order_id: Optional[int] = None
    method: Literal["upi", "debit_card", "credit_card", "net_banking"]
    simulate: Optional[Literal["success", "failure"]] = None  # honoured only when DEMO_PAYMENT_MODE=selectable

    @model_validator(mode="after")
    def _one_target(self):
        if (self.booking_id is None) == (self.food_order_id is None):
            raise ValueError("Provide exactly one of booking_id or food_order_id")
        return self


class VerifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=200)
    expected_match_id: Optional[int] = None
