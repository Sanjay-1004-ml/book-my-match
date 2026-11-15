"""Hybrid booking API routes.

Provides an endpoint that mirrors the console workflow but is accessible via
HTTP so the existing web UI (or any client) can use the new SQLite persistence
without breaking existing routes.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Literal

from ..booking_service_hybrid import hybrid_book_ticket, HybridBookingError

router = APIRouter()

class HybridBookingRequest(BaseModel):
    event_id: str = Field(..., description="Stable event identifier stored in MongoDB")
    category: str = Field(..., description="Ticket category name (e.g. VIP, Regular)")
    quantity: int = Field(..., gt=0, description="Number of tickets to purchase")
    customer_name: str = Field(..., description="Customer full name")
    customer_email: str = Field(..., description="Customer email address")
    customer_phone: str | None = Field(None, description="Optional phone number")
    payment_method: Literal["credit_card", "upi", "cash", "debit_card", "selectable"] = Field(..., description="Payment method identifier")

@router.post("/hybrid/book", summary="Create a booking using the hybrid MongoDB/SQLite workflow")
def book_hybrid(req: HybridBookingRequest):
    try:
        result = hybrid_book_ticket(
            event_id=req.event_id,
            category=req.category,
            qty=req.quantity,
            customer={"name": req.customer_name, "email": req.customer_email, "phone": req.customer_phone},
            payment_method=req.payment_method,
        )
        return {"status": "success", "booking": result}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except HybridBookingError as he:
        raise HTTPException(status_code=500, detail=f"Booking transaction failed: {he}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
