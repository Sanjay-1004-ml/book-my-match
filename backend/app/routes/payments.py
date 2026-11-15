from fastapi import APIRouter, Depends, HTTPException
from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..schemas import PayIn
from ..services import booking_out
from ..food_service import food_order_detail
from .. import ticketing
from ..ticketing import TicketError, METHODS, payment_out

router = APIRouter(prefix="/payments", tags=["payments"])


@router.get("/config")
def payment_config():
    """Lets the demo payment screen know whether to offer a success/failure switch."""
    return {"demo": True, "enabled": settings.PAYMENTS_DEMO_ENABLED, "mode": settings.DEMO_PAYMENT_MODE,
            "methods": [{"id": k, "label": v} for k, v in METHODS.items()], "reservation_minutes": settings.RESERVATION_MINUTES}


@router.post("/pay")
def pay(body: PayIn, user: dict = Depends(get_current_user), db=Depends(get_db)):
    """SIMULATED payment: no real money moves and no card/UPI/bank details are accepted (extra fields are rejected).
    Pay a booking (booking_id) or one standalone food order (food_order_id).
    Returns 200 with outcome = success | failed | already_paid. Repeating the call can never pay or issue tickets twice."""
    if body.food_order_id is not None:
        try:
            r = ticketing.process_food_payment(db, user["_id"], body.food_order_id, body.method, body.simulate)
        except TicketError as e:
            raise HTTPException(e.status, e.message)
        order = db.food_orders.find_one({"_id": body.food_order_id})
        return {"outcome": r["outcome"], "payment": payment_out(r["payment"]) if r["payment"] else None, "booking": None,
                "food_order": food_order_detail(db, order, with_qr=True)}
    try:
        r = ticketing.process_payment(db, user["_id"], body.booking_id, body.method, body.simulate)
    except TicketError as e:
        raise HTTPException(e.status, e.message)
    return {"outcome": r["outcome"], "payment": payment_out(r["payment"]),
            "booking": booking_out(db, db.bookings.find_one({"_id": body.booking_id}), detail=True)}


@router.get("")
def my_payments(user: dict = Depends(get_current_user), db=Depends(get_db)):
    return [payment_out(p) for p in db.payments.find({"user_id": user["_id"]}).sort("_id", -1)]


@router.get("/{transaction_id}")
def my_payment(transaction_id: str, user: dict = Depends(get_current_user), db=Depends(get_db)):
    p = db.payments.find_one({"transaction_id": transaction_id})
    if not p or p["user_id"] != user["_id"]:
        raise HTTPException(404, "Payment not found")
    return payment_out(p)
