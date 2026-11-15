import io
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pymongo.errors import DuplicateKeyError
from ..clock import now as clock_now
from ..config import settings
from ..database import get_db, next_id, d128, dec
from ..deps import get_current_user
from ..pricing import ticket_quote, food_quote
from ..schemas import BookingIn
from ..models_util import new_reference
from ..services import booking_out, bookings_out, match_datetime, resolve_food, food_order_doc_items, d
from .. import ticketing, seat_booking

router = APIRouter(prefix="/bookings", tags=["bookings"])
CLAIM_STALE_SECONDS = 60


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _validate(db, body: BookingIn):
    """Shared by quote and create. The client never supplies a price."""
    if body.quantity > settings.MAX_TICKETS:
        raise HTTPException(400, f"You can book at most {settings.MAX_TICKETS} tickets at a time")
    m = db.matches.find_one({"_id": body.match_id})
    if not m:
        raise HTTPException(404, "Match not found")
    if m["status"] != "scheduled" or match_datetime(m) <= clock_now():
        raise HTTPException(400, "This match is not open for booking")
    if m.get("seat_layout_id") and m.get("seat_inventory_status") == "ready":
        raise HTTPException(409, "This match uses seat selection. Please choose your seats on the match page.")
    inv = db.ticket_inventory.find_one({"match_id": m["_id"], "category_id": body.category_id})
    if not inv:
        raise HTTPException(400, "Ticket category does not belong to this match")
    if inv["available"] < body.quantity:
        raise HTTPException(409, f"Only {inv['available']} tickets left in this category")
    cat = db.ticket_categories.find_one({"_id": inv["category_id"]})
    food = resolve_food(db, body.food)
    tq = ticket_quote(dec(cat["price"]), body.quantity, settings.FEE_PERCENT, settings.TICKET_TAX_PERCENT)
    fq = food_quote([(dec(i["price"]), q) for i, q in food], settings.FOOD_TAX_PERCENT, settings.FOOD_FEE_PERCENT) if food else None
    return m, inv, food, tq, fq


def _q(tq, fq, food=None):
    f = {k: d(v) for k, v in fq.items()} if fq else None
    grand = tq["total"] + (fq["total"] if fq else Decimal(0))
    return {"tickets": {k: (d(v) if isinstance(v, Decimal) else v) for k, v in tq.items()}, "food": f, "grand_total": d(grand),
            # itemised food (additive field): what the food total is made of
            "food_lines": [{"item_id": i["_id"], "name": i["name"], "unit_price": d(i["price"]), "quantity": q,
                            "line_total": d(dec(i["price"]) * q)} for i, q in (food or [])],
            "rates": {"fee_percent": float(settings.FEE_PERCENT), "ticket_tax_percent": float(settings.TICKET_TAX_PERCENT),
                      "food_tax_percent": float(settings.FOOD_TAX_PERCENT), "food_fee_percent": float(settings.FOOD_FEE_PERCENT)},
            "currency": "INR"}


@router.post("/quote")
def quote(body: BookingIn, db=Depends(get_db)):
    _, _, food, tq, fq = _validate(db, body)
    return _q(tq, fq, food)


def _claim(db, claim_id: str, field: str = "booking_id"):
    """Reserve an Idempotency-Key. Returns ('new', None) | ('replay', record_id) | ('busy', None).
    `field` names the id the key resolves to ("booking_id" for bookings; food orders reuse this with "food_order_id")."""
    for _ in range(2):
        try:
            db.idempotency_keys.insert_one({"_id": claim_id, field: None, "created_at": _utcnow()})
            return "new", None
        except DuplicateKeyError:
            prev = db.idempotency_keys.find_one({"_id": claim_id})
            if prev and prev.get(field):
                return "replay", prev[field]
            if prev and (_utcnow() - prev["created_at"]).total_seconds() > CLAIM_STALE_SECONDS:
                # abandoned in-flight request (e.g. server crashed); free it and try once more
                db.idempotency_keys.delete_one({"_id": claim_id, field: None})
                continue
            return "busy", None
    return "busy", None


@router.post("", status_code=201)
def create(body: BookingIn, response: Response, idempotency_key: Optional[str] = Header(default=None, max_length=64),
           user: dict = Depends(get_current_user), db=Depends(get_db)):
    claim_id = f"{user['_id']}:{idempotency_key}" if idempotency_key else None
    if claim_id:
        prev = db.idempotency_keys.find_one({"_id": claim_id})
        if prev and prev.get("booking_id"):
            b = db.bookings.find_one({"_id": prev["booking_id"]})
            if b:
                response.status_code = 200
                return booking_out(db, b, detail=True)
    ticketing.expire_stale(db, limit=25)  # give back tickets from abandoned, unpaid reservations first
    m, inv, food, tq, fq = _validate(db, body)
    if claim_id:
        state, bid = _claim(db, claim_id)
        if state == "replay":
            response.status_code = 200
            return booking_out(db, db.bookings.find_one({"_id": bid}), detail=True)
        if state == "busy":
            raise HTTPException(409, "This booking request is already being processed")

    # 1) Atomic reservation: the filter and the decrement happen in ONE server-side operation,
    #    so two concurrent requests can never both take the last ticket.
    res = db.ticket_inventory.update_one({"_id": inv["_id"], "available": {"$gte": body.quantity}},
                                         {"$inc": {"available": -body.quantity}})
    if res.modified_count != 1:
        if claim_id:
            db.idempotency_keys.delete_one({"_id": claim_id})
        raise HTTPException(409, "Those tickets were just booked by someone else")

    booking_id = order_id = None
    try:
        # 2) Create the booking; if anything fails, step 3 undoes the reservation.
        now = clock_now()
        booking_id = next_id(db, "bookings")
        booking = {"_id": booking_id, "user_id": user["_id"], "match_id": m["_id"], "category_id": body.category_id,
                   "quantity": body.quantity, "unit_price": d128(tq["unit_price"]), "subtotal": d128(tq["subtotal"]),
                   "fee": d128(tq["fee"]), "tax": d128(tq["tax"]), "total": d128(tq["total"]),
                   # Tickets are held (not sold) until the demo payment succeeds; unpaid holds expire automatically.
                   "status": "pending_payment", "payment_status": "pending", "payment_ref": None, "refund_status": None,
                   "expires_at": now + timedelta(minutes=settings.RESERVATION_MINUTES),
                   "idempotency_key": idempotency_key, "created_at": now, "cancelled_at": None}
        for _ in range(5):  # reference is unique-indexed; regenerate on the (very unlikely) collision
            booking["reference"] = new_reference()
            try:
                db.bookings.insert_one(booking)
                break
            except DuplicateKeyError:
                continue
        else:
            raise RuntimeError("Could not allocate a unique booking reference")
        if food:
            order_id = next_id(db, "food_orders")
            db.food_orders.insert_one({"_id": order_id, "user_id": user["_id"], "booking_id": booking_id,
                                       "subtotal": d128(fq["subtotal"]), "fee": d128(fq["fee"]), "tax": d128(fq["tax"]),
                                       "total": d128(fq["total"]),
                                       "status": "placed", "payment_status": "pending", "created_at": now,
                                       "items": food_order_doc_items(food)})
        if claim_id:
            db.idempotency_keys.update_one({"_id": claim_id}, {"$set": {"booking_id": booking_id}})
    except Exception:
        # 3) Compensate: put tickets back and remove partial records.
        db.ticket_inventory.update_one({"_id": inv["_id"]}, {"$inc": {"available": body.quantity}})
        if booking_id is not None:
            db.bookings.delete_one({"_id": booking_id})
        if order_id is not None:
            db.food_orders.delete_one({"_id": order_id})
        if claim_id:
            db.idempotency_keys.delete_one({"_id": claim_id})
        raise
    return booking_out(db, booking, detail=True)


def _own(db, booking_id: int, user: dict) -> dict:
    b = db.bookings.find_one({"_id": booking_id})
    # 404 (not 403) so other users' booking ids are not revealed
    if not b or b["user_id"] != user["_id"]:
        raise HTTPException(404, "Booking not found")
    return b


@router.get("")
def my_bookings(user: dict = Depends(get_current_user), db=Depends(get_db)):
    rows = list(db.bookings.find({"user_id": user["_id"]}).sort([("created_at", -1), ("_id", -1)]))
    return bookings_out(db, rows)


@router.get("/{booking_id}")
def booking_detail(booking_id: int, user: dict = Depends(get_current_user), db=Depends(get_db)):
    return booking_out(db, _own(db, booking_id, user), detail=True)


@router.post("/{booking_id}/cancel")
def cancel(booking_id: int, user: dict = Depends(get_current_user), db=Depends(get_db)):
    b = _own(db, booking_id, user)
    if b["status"] in ("cancelled", "expired"):
        raise HTTPException(409, f"Booking is already {b['status']}")
    m = db.matches.find_one({"_id": b["match_id"]})
    was_paid = b["status"] == "confirmed" and b["payment_status"] == "successful"
    if was_paid:
        if db.tickets.count_documents({"booking_id": b["_id"], "ticket_status": "used"}, limit=1):
            raise HTTPException(400, "Some tickets from this booking were already used for entry; it cannot be cancelled")
        if match_datetime(m) - clock_now() < timedelta(hours=settings.CANCEL_CUTOFF_HOURS):
            raise HTTPException(400, f"Bookings cannot be cancelled within {settings.CANCEL_CUTOFF_HOURS} hours of the match")
    now = clock_now()
    if was_paid:
        ticketing.cancel_tickets_for_booking(db, b["_id"], now)  # QR codes stop working before anything else changes
    # Flip status atomically first (only if still live) so a double-click cannot release inventory twice.
    res = db.bookings.update_one({"_id": b["_id"], "status": {"$in": ["pending_payment", "confirmed"]}},
                                 {"$set": {"status": "cancelled", "cancelled_at": now, "expires_at": None,
                                           "refund_status": "demo_refund_pending" if was_paid else "not_applicable",
                                           "payment_status": "successful" if was_paid else "failed"}})
    if res.modified_count != 1:
        raise HTTPException(409, "Booking is already cancelled")
    if b.get("booking_type") == "seat":
        seat_booking.release_seats(db, b["_id"])   # held or booked seats become available again for this match
    else:
        db.ticket_inventory.update_one({"match_id": b["match_id"], "category_id": b["category_id"]},
                                       {"$inc": {"available": b["quantity"]}})
    db.food_orders.update_many({"booking_id": b["_id"]}, {"$set": {"status": "cancelled"}})
    return booking_out(db, db.bookings.find_one({"_id": b["_id"]}), detail=True)


@router.get("/{booking_id}/pdf")
def booking_pdf(booking_id: int, user: dict = Depends(get_current_user), db=Depends(get_db)):
    from reportlab.lib.pagesizes import A5
    from reportlab.pdfgen import canvas
    o = booking_out(db, _own(db, booking_id, user), detail=True)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A5)
    w, h = A5
    y = h - 50
    c.setFont("Helvetica-Bold", 18); c.drawString(40, y, "BOOK MY MATCH - Booking Confirmation"); y -= 24
    c.setFont("Helvetica", 9); c.drawString(40, y, "Demo booking: no real payment was processed."); y -= 28
    rows = [("Reference", o["reference"]), ("Status", o["status"].title()), ("Payment (demo)", o["payment_status"].title()),
            ("Sport", o["sport"]), ("Match", o["match"]), ("Stadium", f'{o["stadium"]}, {o["location"]}'),
            ("Date", o["match_date"]), ("Time", o["timing"]), ("Category", o["category"]), ("Tickets", str(o["quantity"])),
            ("Ticket total", f'INR {o["ticket_total"]:.2f}'), ("Food total", f'INR {o["food_total"]:.2f}'),
            ("Grand total", f'INR {o["grand_total"]:.2f}')]
    for k, v in rows:
        c.setFont("Helvetica-Bold", 10); c.drawString(40, y, k)
        c.setFont("Helvetica", 10); c.drawString(150, y, str(v)); y -= 18
    c.showPage(); c.save()
    return Response(buf.getvalue(), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{o["reference"]}.pdf"'})
