"""Phase 3B/3C/3D: seat holds (temporary locks), confirmation and release for seat-based bookings.

Design (details and indexes: docs/PHASE_3BCD_SEAT_BOOKING.md)
  * A seat booking is an ordinary document in `bookings` (booking_type == "seat", status pending_payment while it is a hold), so
    payment, expiry, cancellation, tickets, QR, PDF and My Bookings reuse the existing code. The booking IS the hold record.
  * The lock itself lives on the per-match seat row (`match_seat_inventory`): status "held" + booking_id + hold_expires_at.
  * Locking one seat = ONE conditional find_one_and_update that only matches a seat that is available OR whose hold has already
    expired (the expiry timestamp is checked inside the lock itself; no reliance on TTL deletion or on a sweeper having run).
    MongoDB applies it atomically per document, so two simultaneous requests can never both win the same seat.
  * If any seat of a request cannot be locked, every seat that request already locked is released (compensation) and nothing is created.
  * Confirmation (after payment) and release are conditional on booking_id, so a stale/expired booking can never touch seats that
    were meanwhile taken by somebody else. Framework-free: raises SeatingError like seating.py.
"""
from datetime import timedelta
from decimal import Decimal
from pymongo.errors import DuplicateKeyError
from .clock import now as clock_now
from .config import settings
from .database import d128, dec, next_id
from .models_util import new_reference
from .pricing import money, seat_quote
from .seating import SeatingError, seat_price, sweep_expired_holds

_UNHOLD = {"$set": {"status": "available"}, "$unset": {"booking_id": "", "hold_user_id": "", "hold_expires_at": "", "booked_at": ""}}


def _lockable(at):
    return {"$or": [{"status": "available"}, {"status": "held", "hold_expires_at": {"$lte": at}}]}


def release_seats(db, booking_id: int) -> int:
    """Give back every seat this booking holds or has booked (cancel / expiry / failed payment). Only touches rows still owned by it."""
    return db.match_seat_inventory.update_many({"booking_id": booking_id, "status": {"$in": ["held", "booked"]}}, _UNHOLD).modified_count


def confirm_seats(db, booking: dict, at=None) -> bool:
    """held -> booked for this booking's seats (idempotent). True only if ALL of its seats are now booked for it."""
    at = at or clock_now()
    db.match_seat_inventory.update_many({"booking_id": booking["_id"], "status": "held"},
                                        {"$set": {"status": "booked", "booked_at": at}, "$unset": {"hold_user_id": "", "hold_expires_at": ""}})
    return db.match_seat_inventory.count_documents({"booking_id": booking["_id"], "status": "booked"}) == booking["quantity"]


def seat_snapshot(db, seat_docs, prices):
    """What the ticket must show, frozen at booking time (later renames or price edits must not change a paid booking)."""
    stands = {s["_id"]: s["name"] for s in db.seat_stands.find({"_id": {"$in": list({x["stand_id"] for x in seat_docs})}})}
    secs = {s["_id"]: s["name"] for s in db.seat_sections.find({"_id": {"$in": list({x["section_id"] for x in seat_docs})}})}
    cats = {c["_id"]: c["name"] for c in db.seat_categories.find({"_id": {"$in": list({x["category_id"] for x in seat_docs})}})}
    return [{"seat_id": s["_id"], "seat_key": s["seat_key"], "stand": stands.get(s["stand_id"]), "section": secs.get(s["section_id"]),
             "row": s["row_label"], "number": s["seat_number"], "category": cats.get(s["category_id"]), "category_id": s["category_id"],
             "unit_price": d128(p)} for s, p in zip(seat_docs, prices)]


def seat_label(s: dict) -> str:
    return f'{s["stand"]} · {s["section"]} · Row {s["row"]} · Seat {s["number"]}'


def create_seat_booking(db, user_id: int, match_id: int, seat_ids, idempotency_key=None) -> dict:
    """Validate, price (on the server) and lock the seats, then create the pending booking. Returns the booking document."""
    from .services import match_datetime
    m = db.matches.find_one({"_id": match_id})
    if not m:
        raise SeatingError(404, "Match not found")
    if m["status"] != "scheduled" or match_datetime(m) <= clock_now():
        raise SeatingError(400, "This match is not open for booking")
    if not m.get("seat_layout_id") or m.get("seat_inventory_status") != "ready":
        raise SeatingError(409, "This match does not have seat selection")
    ids = seat_ids if isinstance(seat_ids, list) else []
    if not ids or any(isinstance(i, bool) or not isinstance(i, int) for i in ids):
        raise SeatingError(422, "Select at least one seat")
    if len(set(ids)) != len(ids):
        raise SeatingError(422, "The same seat was selected twice")
    if len(ids) > settings.MAX_TICKETS:
        raise SeatingError(400, f"You can book at most {settings.MAX_TICKETS} tickets at a time")
    t = clock_now()
    sweep_expired_holds(db, match_id, t)
    from . import ticketing
    ticketing.expire_stale(db, limit=25, at=t)
    if db.bookings.find_one({"user_id": user_id, "match_id": match_id, "booking_type": "seat", "status": "pending_payment", "expires_at": {"$gt": t}}):
        raise SeatingError(409, "You already have seats on hold for this match. Pay for them or cancel that checkout first.")
    ids = sorted(ids)
    seats = {s["_id"]: s for s in db.seats.find({"_id": {"$in": ids}, "layout_id": m["seat_layout_id"]})}
    inv = {r["seat_id"]: r for r in db.match_seat_inventory.find({"match_id": match_id, "seat_id": {"$in": ids}})}
    missing = [i for i in ids if i not in seats or i not in inv]
    if missing:
        raise SeatingError(404, f"Seats not found for this match: {missing}")
    taken = [i for i in ids if not inv[i]["is_active"] or not (inv[i]["status"] == "available" or (inv[i]["status"] == "held" and inv[i].get("hold_expires_at") and inv[i]["hold_expires_at"] <= t))]
    if taken:
        raise SeatingError(409, f"Seats not available: {taken}")
    cats = {c["_id"]: c for c in db.seat_categories.find({"layout_id": m["seat_layout_id"]})}
    ordered = [seats[i] for i in ids]
    prices = [seat_price(s, cats[s["category_id"]]) for s in ordered]          # server-side price, never from the client
    q = seat_quote(prices, settings.FEE_PERCENT, settings.TICKET_TAX_PERCENT)
    booking_id, exp = next_id(db, "bookings"), t + timedelta(minutes=settings.RESERVATION_MINUTES)
    locked = []
    for sid in ids:   # ascending order for every request keeps contention predictable
        won = db.match_seat_inventory.find_one_and_update(
            {"match_id": match_id, "seat_id": sid, "is_active": True, **_lockable(t)},
            {"$set": {"status": "held", "booking_id": booking_id, "hold_user_id": user_id, "hold_expires_at": exp}})
        if won is None:
            release_seats(db, booking_id)   # undo what this request locked
            raise SeatingError(409, f"Seats not available: {[sid]}. Someone else just took a seat; please choose again.")
        locked.append(sid)
    try:
        snap = seat_snapshot(db, ordered, prices)
        booking = {"_id": booking_id, "user_id": user_id, "match_id": match_id, "booking_type": "seat", "category_id": None,
                   "quantity": len(ids), "seat_ids": ids, "seats": snap,
                   "unit_price": d128(money(q["subtotal"] / len(ids))), "subtotal": d128(q["subtotal"]), "fee": d128(q["fee"]),
                   "tax": d128(q["tax"]), "total": d128(q["total"]),
                   "status": "pending_payment", "payment_status": "pending", "payment_ref": None, "refund_status": None,
                   "expires_at": exp, "idempotency_key": idempotency_key, "created_at": t, "cancelled_at": None}
        for _ in range(5):
            booking["reference"] = new_reference()
            try:
                db.bookings.insert_one(booking)
                break
            except DuplicateKeyError:
                continue
        else:
            raise RuntimeError("Could not allocate a unique booking reference")
    except Exception:
        release_seats(db, booking_id)
        db.bookings.delete_one({"_id": booking_id})
        raise
    return booking
