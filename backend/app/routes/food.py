from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from ..config import settings
from ..clock import now as clock_now
from ..database import get_db, next_id, d128, dec
from ..deps import get_current_user
from ..pricing import food_quote
from ..schemas import FoodOrderIn, FoodQuoteIn
from ..services import resolve_food, food_order_doc_items, d
from ..food_service import quote_cart, food_orders_out, food_order_detail
from .bookings import _claim

router = APIRouter(prefix="/food", tags=["food"])


def items_out(db, items: list) -> list:
    cats = {c["_id"]: c for c in db.food_categories.find({"_id": {"$in": list({i["category_id"] for i in items})}})}
    return [{"id": i["_id"], "name": i["name"], "price": d(i["price"]), "category_id": i["category_id"],
             "category": cats[i["category_id"]]["name"], "is_active": i["is_active"],
             "description": i.get("description") or None, "image_url": i.get("image_url") or None} for i in items]


@router.get("/categories")
def categories(db=Depends(get_db)):
    return [{"id": c["_id"], "name": c["name"]} for c in db.food_categories.find().sort("_id", 1)]


@router.get("/items")
def items(category_id: Optional[int] = None, db=Depends(get_db)):
    flt = {"is_active": True}
    if category_id:
        flt["category_id"] = category_id
    return items_out(db, list(db.food_items.find(flt).sort([("category_id", 1), ("_id", 1)])))


@router.get("/items/{item_id}")
def item(item_id: int, db=Depends(get_db)):
    i = db.food_items.find_one({"_id": item_id})
    if not i or not i["is_active"]:
        raise HTTPException(404, "Food item not found")
    return items_out(db, [i])[0]


@router.post("/quote")
def quote(body: FoodQuoteIn, db=Depends(get_db)):
    """Itemised food bill priced by the server (the only place food prices are computed). Unavailable cart items are
    listed under `unavailable` and are not charged. Public, like the menu itself."""
    return quote_cart(db, body.items)


def _own_order(db, order_id: int, user: dict) -> dict:
    o = db.food_orders.find_one({"_id": order_id})
    if not o or o["user_id"] != user["_id"]:
        raise HTTPException(404, "Food order not found")  # 404 (not 403) so other users' order ids are not revealed
    return o


@router.post("/orders", status_code=201)
def place_order(body: FoodOrderIn, response: Response, idempotency_key: Optional[str] = Header(default=None, max_length=64),
                user: dict = Depends(get_current_user), db=Depends(get_db)):
    """Create a food order. Attached to a booking that is still awaiting payment it is paid together with the tickets
    (`pay_with_booking`); otherwise it is paid on its own via POST /payments/pay {food_order_id}.
    An Idempotency-Key header makes a double submit return the same order instead of creating a second one."""
    claim_id = f"food:{user['_id']}:{idempotency_key}" if idempotency_key else None
    if claim_id:
        prev = db.idempotency_keys.find_one({"_id": claim_id})
        if prev and prev.get("food_order_id"):
            o = db.food_orders.find_one({"_id": prev["food_order_id"]})
            if o:
                response.status_code = 200
                return food_order_detail(db, o, with_qr=True)
    booking = None
    if body.booking_id is not None:
        booking = db.bookings.find_one({"_id": body.booking_id})
        if not booking or booking["user_id"] != user["_id"]:
            raise HTTPException(404, "Booking not found")
        if booking["status"] not in ("pending_payment", "confirmed"):
            raise HTTPException(400, "Food can only be added to a live booking")
    lines = resolve_food(db, body.items)
    fq = food_quote([(dec(i["price"]), q) for i, q in lines], settings.FOOD_TAX_PERCENT, settings.FOOD_FEE_PERCENT)
    if claim_id:
        state, oid = _claim(db, claim_id, "food_order_id")
        if state == "replay":
            response.status_code = 200
            return food_order_detail(db, db.food_orders.find_one({"_id": oid}), with_qr=True)
        if state == "busy":
            raise HTTPException(409, "This order is already being processed")
    try:
        order = {"_id": next_id(db, "food_orders"), "user_id": user["_id"], "booking_id": booking["_id"] if booking else None,
                 "subtotal": d128(fq["subtotal"]), "fee": d128(fq["fee"]), "tax": d128(fq["tax"]), "total": d128(fq["total"]),
                 "status": "placed", "payment_status": "pending", "created_at": clock_now(),
                 "items": food_order_doc_items(lines)}
        db.food_orders.insert_one(order)
        if claim_id:
            db.idempotency_keys.update_one({"_id": claim_id}, {"$set": {"food_order_id": order["_id"]}})
    except Exception:
        if claim_id:
            db.idempotency_keys.delete_one({"_id": claim_id})
        raise
    return food_order_detail(db, order, with_qr=True)


@router.get("/orders")
def my_orders(user: dict = Depends(get_current_user), db=Depends(get_db)):
    return food_orders_out(db, list(db.food_orders.find({"user_id": user["_id"]}).sort("_id", -1)))


@router.get("/orders/{order_id}")
def order_detail(order_id: int, user: dict = Depends(get_current_user), db=Depends(get_db)):
    return food_order_detail(db, _own_order(db, order_id, user), with_qr=True)


@router.post("/orders/{order_id}/cancel")
def cancel_order(order_id: int, user: dict = Depends(get_current_user), db=Depends(get_db)):
    """Cancel an UNPAID food order that is payable on its own. Paid orders and orders that belong to a booking still
    awaiting payment are cancelled by cancelling the booking (so the amount being paid never changes under the payer)."""
    o = _own_order(db, order_id, user)
    if o["status"] == "cancelled":
        raise HTTPException(409, "This food order is already cancelled")
    if o["payment_status"] == "successful":
        raise HTTPException(400, "A paid food order cannot be cancelled here")
    if food_order_detail(db, o)["pay_with_booking"]:
        raise HTTPException(409, "This order is part of a booking that is awaiting payment. Cancel the booking instead.")
    res = db.food_orders.update_one({"_id": order_id, "user_id": user["_id"], "status": "placed",
                                     "payment_status": {"$in": ["pending", "failed"]}},
                                    {"$set": {"status": "cancelled", "cancelled_at": clock_now()}})
    if res.modified_count != 1:
        raise HTTPException(409, "This food order changed just now. Please refresh.")
    return food_order_detail(db, db.food_orders.find_one({"_id": order_id}))


@router.get("/orders/{order_id}/receipt")
def order_receipt(order_id: int, user: dict = Depends(get_current_user), db=Depends(get_db)):
    from ..food_receipt import build_receipt_pdf
    o = food_order_detail(db, _own_order(db, order_id, user), with_qr=True)
    if not o["receipt_available"]:
        raise HTTPException(409, "The receipt is available once the food order is paid")
    return Response(build_receipt_pdf(o), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{o["order_number"]}-receipt.pdf"'})
