"""Phase 3B-3D: create a seat booking (= temporary seat hold + pending booking). Everything after this step reuses the existing
endpoints: POST /payments/pay, POST /bookings/{id}/cancel, GET /bookings, GET /bookings/{id}/tickets[/pdf], staff verification."""
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from ..database import get_db
from ..deps import get_current_user
from ..seating_schemas import SeatBookingIn
from ..services import booking_out
from .. import seat_booking
from .bookings import _claim

router = APIRouter(prefix="/bookings", tags=["seat-booking"])


@router.post("/seats", status_code=201)
def book_seats(body: SeatBookingIn, response: Response, idempotency_key: Optional[str] = Header(default=None, max_length=64),
               user: dict = Depends(get_current_user), db=Depends(get_db)):
    """Lock the chosen seats for RESERVATION_MINUTES and create the pending booking. The response carries expires_at /
    expires_in_seconds for the checkout countdown. Authentication is required; prices in the request are not accepted."""
    claim_id = f"{user['_id']}:seats:{idempotency_key}" if idempotency_key else None
    if claim_id:
        prev = db.idempotency_keys.find_one({"_id": claim_id})
        if prev and prev.get("booking_id"):
            b = db.bookings.find_one({"_id": prev["booking_id"]})
            if b:
                response.status_code = 200
                return booking_out(db, b, detail=True)
        state, bid = _claim(db, claim_id)
        if state == "replay":
            response.status_code = 200
            return booking_out(db, db.bookings.find_one({"_id": bid}), detail=True)
        if state == "busy":
            raise HTTPException(409, "This booking request is already being processed")
    try:
        b = seat_booking.create_seat_booking(db, user["_id"], body.match_id, body.seat_ids, idempotency_key)
    except Exception:
        if claim_id:
            db.idempotency_keys.delete_one({"_id": claim_id})
        raise
    if claim_id:
        db.idempotency_keys.update_one({"_id": claim_id}, {"$set": {"booking_id": b["_id"]}})
    return booking_out(db, b, detail=True)
