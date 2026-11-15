"""Demo payments, reservation expiry, ticket issuing, QR-token verification and statistics.

Framework-free on purpose (no FastAPI import): functions take a MongoDB database object and raise TicketError,
which the routes translate into HTTP errors. Money is Decimal in code and Decimal128 in MongoDB.

Key guarantees
  * Tickets exist only after a payment succeeded; issuing is idempotent (unique index on (booking_id, seq)).
  * Exactly one request can flip a booking pending_payment -> confirmed (atomic conditional update).
  * A ticket can be accepted for entry once: find_one_and_update on {ticket_status: "valid"} is atomic per document.
  * The QR carries only an opaque token. MongoDB stores only SHA-256(token); the token is re-derived on demand
    from HMAC(TICKET_SIGNING_KEY, ticket_id:nonce), so it can be shown again without storing it in clear text."""
import base64
import hashlib
import hmac
import random
import re
import secrets
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from .config import settings
from .database import next_id, d128, dec

METHODS = {"upi": "UPI", "debit_card": "Debit Card", "credit_card": "Credit Card", "net_banking": "Net Banking"}
TOKEN_PREFIX = "BMMT:"
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
FAILURE_REASONS = ["Simulated bank declined the payment", "Simulated payment timed out", "Simulated insufficient funds"]


class TicketError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


from .clock import now  # noqa: E402  (venue-local wall clock, shared with the rest of the app)


def _rand(n: int) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


def new_ticket_id() -> str:
    return "TKT-" + _rand(10)


def new_transaction_id(at: datetime | None = None) -> str:
    return f"TXN{(at or now()).strftime('%Y%m%d')}{_rand(8)}"


# ---------------------------------------------------------------- tokens
def derive_token(ticket_id: str, nonce: str) -> str:
    mac = hmac.new(settings.TICKET_SIGNING_KEY.encode(), f"{ticket_id}:{nonce}".encode(), hashlib.sha256).digest()[:24]
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")  # 32 url-safe chars, unpredictable without the key


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def qr_payload(ticket: dict) -> str:
    return TOKEN_PREFIX + derive_token(ticket["ticket_id"], ticket["token_nonce"])


def normalize_token(raw) -> str | None:
    """Accepts what the QR holds ('BMMT:<token>') or the bare token typed by hand. Anything else -> None."""
    if not isinstance(raw, str):
        return None
    t = raw.strip()
    if t.upper().startswith(TOKEN_PREFIX):
        t = t[len(TOKEN_PREFIX):]
    return t if re.fullmatch(r"[A-Za-z0-9_-]{32}", t) else None


# ---------------------------------------------------------------- reservations
def expire_booking(db, booking_id: int, at: datetime | None = None) -> bool:
    """Release an unpaid reservation whose hold ran out. Atomic: only one caller wins, so inventory is returned once."""
    at = at or now()
    res = db.bookings.update_one({"_id": booking_id, "status": "pending_payment", "expires_at": {"$lte": at}},
                                 {"$set": {"status": "expired", "payment_status": "failed", "refund_status": "not_applicable",
                                           "expired_at": at}})
    if res.modified_count != 1:
        return False
    b = db.bookings.find_one({"_id": booking_id})
    if b.get("booking_type") == "seat":   # seat booking: give the seats back (only rows still owned by this booking)
        from .seat_booking import release_seats
        release_seats(db, booking_id)
    else:
        db.ticket_inventory.update_one({"match_id": b["match_id"], "category_id": b["category_id"]}, {"$inc": {"available": b["quantity"]}})
    db.food_orders.update_many({"booking_id": booking_id}, {"$set": {"status": "cancelled"}})
    return True


def expire_stale(db, limit: int = 100, at: datetime | None = None) -> int:
    at = at or now()
    ids = [b["_id"] for b in db.bookings.find({"status": "pending_payment", "expires_at": {"$lte": at}}, {"_id": 1}).limit(limit)]
    return sum(1 for i in ids if expire_booking(db, i, at))


# ---------------------------------------------------------------- payments
def payable_amount(db, booking: dict) -> Decimal:
    food = sum((dec(o["total"]) for o in db.food_orders.find({"booking_id": booking["_id"], "status": "placed"}, {"total": 1})), Decimal(0))
    return dec(booking["total"]) + food


def decide_outcome(simulate: str | None) -> bool:
    mode = settings.DEMO_PAYMENT_MODE
    if mode == "success": return True
    if mode == "failure": return False
    if mode == "random": return random.random() < settings.DEMO_PAYMENT_SUCCESS_RATE
    return simulate != "failure"  # selectable: success unless the demo screen asked for a failure


def payment_out(p: dict) -> dict:
    return {"transaction_id": p["transaction_id"], "booking_id": p.get("booking_id"), "booking_reference": p.get("booking_reference"),
            "food_order_id": p.get("food_order_id"), "kind": p.get("kind", "booking"),
            "method": p["method"], "method_label": METHODS.get(p["method"], p["method"]), "amount": float(dec(p["amount"])),
            "currency": p.get("currency", "INR"), "status": p["status"], "failure_reason": p.get("failure_reason"),
            "simulated": True, "created_at": p["created_at"].isoformat() if p.get("created_at") else None,
            "completed_at": p["completed_at"].isoformat() if p.get("completed_at") else None}


def _save_payment(db, doc: dict) -> dict:
    """Insert a payment; if the same transaction id already exists (duplicate request) return the stored one."""
    try:
        db.payments.insert_one(doc)
        return doc
    except DuplicateKeyError:
        return db.payments.find_one({"transaction_id": doc["transaction_id"]})


def _insert_payment(db, b: dict, method: str, amount: Decimal, txn: str, status: str, at: datetime, reason=None) -> dict:
    return _save_payment(db, {"_id": next_id(db, "payments"), "transaction_id": txn, "booking_id": b["_id"], "booking_reference": b["reference"],
                              "user_id": b["user_id"], "method": method, "amount": d128(amount), "currency": "INR", "status": status,
                              "failure_reason": reason, "simulated": True, "created_at": at, "completed_at": at})


def finalize_confirmed_booking(db, b: dict):
    """Idempotent: make sure a paid booking has its payment record, paid food orders and ALL its tickets.
    Safe to call repeatedly (also used to self-heal if the server died right after the payment was accepted)."""
    if b.get("booking_type") == "seat":
        from .seat_booking import confirm_seats
        confirm_seats(db, b)   # idempotent: completes the held -> booked step if the server died right after the payment was accepted
    payment = db.payments.find_one({"transaction_id": b["payment_ref"]}) if b.get("payment_ref") else None
    if payment is None:
        payment = _insert_payment(db, b, b.get("payment_method") or "demo", dec(b.get("paid_amount") or b["total"]),
                                  b["payment_ref"], "successful", b.get("paid_at") or now())
    db.food_orders.update_many({"booking_id": b["_id"], "status": "placed", "payment_status": "pending",
                                "created_at": {"$lte": b.get("paid_at") or now()}},
                               {"$set": {"payment_status": "successful", "payment_ref": b.get("payment_ref"),
                                         "payment_method": b.get("payment_method"), "paid_at": b.get("paid_at") or now()}})
    return payment, issue_tickets(db, b, payment)


def process_payment(db, user_id: int, booking_id: int, method: str, simulate: str | None = None) -> dict:
    """Simulated payment. No card/UPI/bank data is accepted or stored. Returns {outcome, payment, booking}."""
    if not settings.PAYMENTS_DEMO_ENABLED:
        raise TicketError(503, "Demo payments are disabled on this server")
    if method not in METHODS:
        raise TicketError(400, "Unsupported payment method")
    b = db.bookings.find_one({"_id": booking_id})
    if not b or b["user_id"] != user_id:
        raise TicketError(404, "Booking not found")  # same answer for 'not yours' so ids are not revealed
    if b["status"] == "confirmed" and b["payment_status"] == "successful":
        payment, _ = finalize_confirmed_booking(db, b)
        return {"outcome": "already_paid", "payment": payment, "booking": b}
    if b["status"] != "pending_payment":
        raise TicketError(409, f"This booking is {b['status'].replace('_', ' ')} and cannot be paid")
    t = now()
    if b["expires_at"] <= t:
        expire_booking(db, booking_id, t)
        raise TicketError(410, "Your reservation expired and the tickets were released. Please book again.")
    amount = payable_amount(db, b)
    txn = new_transaction_id(t)
    if not decide_outcome(simulate):
        pay = _insert_payment(db, b, method, amount, txn, "failed", t, random.choice(FAILURE_REASONS))
        db.bookings.update_one({"_id": booking_id, "status": "pending_payment"}, {"$set": {"payment_status": "failed"}})
        return {"outcome": "failed", "payment": pay, "booking": db.bookings.find_one({"_id": booking_id})}
    # Only ONE request can win this conditional update, even if the button is double-clicked or two tabs pay at once.
    flip = db.bookings.update_one(
        {"_id": booking_id, "status": "pending_payment", "expires_at": {"$gt": t}},
        {"$set": {"status": "confirmed", "payment_status": "successful", "payment_ref": txn, "payment_method": method,
                  "paid_at": t, "paid_amount": d128(amount), "expires_at": None}})
    cur = db.bookings.find_one({"_id": booking_id})
    if flip.modified_count == 1 and cur.get("booking_type") == "seat":
        from .seat_booking import confirm_seats, release_seats
        if not confirm_seats(db, cur, t):   # a seat was lost in a tiny expiry race: nothing is charged, everything is rolled back
            release_seats(db, booking_id)
            db.bookings.update_one({"_id": booking_id, "payment_ref": txn}, {"$set": {"status": "expired", "payment_status": "failed", "refund_status": "not_applicable",
                                                                                   "expired_at": now(), "payment_ref": None, "paid_amount": None, "paid_at": None}})
            raise TicketError(409, "Your seats are no longer held for you, so no payment was taken. Please choose seats again.")
    if flip.modified_count != 1:
        if cur["status"] == "confirmed" and cur["payment_status"] == "successful":  # lost the race to a duplicate request
            payment, _ = finalize_confirmed_booking(db, cur)
            return {"outcome": "already_paid", "payment": payment, "booking": cur}
        expire_booking(db, booking_id, now())
        raise TicketError(410, "Your reservation expired and the tickets were released. Please book again.")
    payment, _ = finalize_confirmed_booking(db, cur)
    return {"outcome": "success", "payment": payment, "booking": cur}


# ---------------------------------------------------------------- food-only payments
def _insert_food_payment(db, o: dict, method: str, amount: Decimal, txn: str, status: str, at: datetime, reason=None) -> dict:
    b = db.bookings.find_one({"_id": o["booking_id"]}, {"reference": 1}) if o.get("booking_id") else None
    return _save_payment(db, {"_id": next_id(db, "payments"), "transaction_id": txn, "kind": "food", "food_order_id": o["_id"],
                              "booking_id": o.get("booking_id"), "booking_reference": b["reference"] if b else None,
                              "user_id": o["user_id"], "method": method, "amount": d128(amount), "currency": "INR", "status": status,
                              "failure_reason": reason, "simulated": True, "created_at": at, "completed_at": at})


def payment_for_food_order(db, o: dict):
    """The successful payment record behind a paid food order: its own, or the booking checkout that included it."""
    ref = o.get("payment_ref")
    if not ref and o.get("booking_id"):
        b = db.bookings.find_one({"_id": o["booking_id"]}, {"payment_ref": 1})
        ref = b.get("payment_ref") if b else None
    return db.payments.find_one({"transaction_id": ref}) if ref else None


def finalize_paid_food_order(db, o: dict):
    """Idempotent: make sure a paid standalone food order has its payment record (self-heals a crash right after the flip)."""
    payment = payment_for_food_order(db, o)
    if payment is None and o.get("payment_ref"):
        payment = _insert_food_payment(db, o, o.get("payment_method") or "demo", dec(o.get("paid_amount") or o["total"]),
                                       o["payment_ref"], "successful", o.get("paid_at") or now())
    return payment


def process_food_payment(db, user_id: int, order_id: int, method: str, simulate: str | None = None) -> dict:
    """Simulated payment of ONE food order. No card/UPI/bank data is accepted or stored. Returns {outcome, payment, order}.
    Same guarantees as process_payment: only one request can flip the order to paid, so a double click or two tabs can
    never create two successful payments for it.
    Orders that belong to a booking still awaiting payment are paid with that booking, not here."""
    if not settings.PAYMENTS_DEMO_ENABLED:
        raise TicketError(503, "Demo payments are disabled on this server")
    if method not in METHODS:
        raise TicketError(400, "Unsupported payment method")
    o = db.food_orders.find_one({"_id": order_id})
    if not o or o["user_id"] != user_id:
        raise TicketError(404, "Food order not found")  # same answer for 'not yours'
    if o["status"] == "placed" and o["payment_status"] == "successful":
        return {"outcome": "already_paid", "payment": finalize_paid_food_order(db, o), "order": o}
    if o["status"] != "placed":
        raise TicketError(409, f"This food order is {o['status'].replace('_', ' ')} and cannot be paid")
    if o.get("booking_id") is not None:
        b = db.bookings.find_one({"_id": o["booking_id"]})
        if b and b["status"] == "pending_payment":
            raise TicketError(409, "This food order is part of a booking that is awaiting payment. Pay it together with the tickets.")
        if not b or b["status"] != "confirmed":
            raise TicketError(409, "The booking for this food order is no longer active")
    gone = [i["name"] for i in o.get("items", [])
            if not (db.food_items.find_one({"_id": i["food_item_id"]}) or {}).get("is_active")]
    if gone:
        raise TicketError(409, f"{', '.join(gone)} is no longer available. Cancel this order and place a new one.")
    t = now()
    amount = dec(o["total"])  # the total stored when the order was placed (server-priced); the client never sends an amount
    txn = new_transaction_id(t)
    if not decide_outcome(simulate):
        pay = _insert_food_payment(db, o, method, amount, txn, "failed", t, random.choice(FAILURE_REASONS))
        db.food_orders.update_one({"_id": order_id, "status": "placed", "payment_status": {"$in": ["pending", "failed"]}},
                                  {"$set": {"payment_status": "failed"}})
        return {"outcome": "failed", "payment": pay, "order": db.food_orders.find_one({"_id": order_id})}
    flip = db.food_orders.update_one(
        {"_id": order_id, "user_id": user_id, "status": "placed", "payment_status": {"$in": ["pending", "failed"]}},
        {"$set": {"payment_status": "successful", "payment_ref": txn, "payment_method": method, "paid_at": t, "paid_amount": d128(amount)}})
    cur = db.food_orders.find_one({"_id": order_id})
    if flip.modified_count != 1:
        if cur["status"] == "placed" and cur["payment_status"] == "successful":  # lost the race to a duplicate request
            return {"outcome": "already_paid", "payment": finalize_paid_food_order(db, cur), "order": cur}
        raise TicketError(409, "This food order changed while you were paying. Please check it and try again.")
    return {"outcome": "success", "payment": finalize_paid_food_order(db, cur), "order": cur}


# ---------------------------------------------------------------- tickets
def issue_tickets(db, b: dict, payment: dict) -> list:
    """One ticket document per purchased ticket, each with its own token. Never issues more than b['quantity']."""
    have = {t["seq"] for t in db.tickets.find({"booking_id": b["_id"]}, {"seq": 1})}
    if len(have) < b["quantity"]:
        cat = db.ticket_categories.find_one({"_id": b["category_id"]}) if b.get("category_id") is not None else None
        m = db.matches.find_one({"_id": b["match_id"]})
        for seq in range(1, b["quantity"] + 1):
            if seq in have:
                continue
            seat = b["seats"][seq - 1] if b.get("booking_type") == "seat" else None
            for _ in range(5):
                tid, nonce = new_ticket_id(), secrets.token_hex(8)
                doc = {"_id": next_id(db, "tickets"), "ticket_id": tid, "booking_id": b["_id"], "booking_reference": b["reference"],
                       "user_id": b["user_id"], "match_id": b["match_id"], "stadium_id": m["stadium_id"],
                       "category_id": seat["category_id"] if seat else b["category_id"],
                       "ticket_category": seat["category"] if seat else cat["name"],
                       # category tickets have no individual seat; seat tickets carry a printable label plus the structured seat
                       "seat_number": f'{seat["stand"]} · {seat["section"]} · Row {seat["row"]} · Seat {seat["number"]}' if seat else None,
                       "seat": ({k: seat[k] for k in ("seat_id", "seat_key", "stand", "section", "row", "number")} if seat else None),
                       "seq": seq, "payment_id": payment["_id"], "payment_status": "successful", "ticket_status": "valid",
                       "token_nonce": nonce, "verification_token_hash": token_hash(derive_token(tid, nonce)),
                       "created_at": now(), "used_at": None, "verified_by": None, "verified_by_name": None, "cancelled_at": None}
                try:
                    db.tickets.insert_one(doc)
                    break
                except DuplicateKeyError:
                    if db.tickets.find_one({"booking_id": b["_id"], "seq": seq}, {"_id": 1}):
                        break  # another request already issued this one
    return list(db.tickets.find({"booking_id": b["_id"]}).sort("seq", 1))


def heal_user(db, user_id: int):
    """Self-heal paid bookings that are missing tickets (e.g. crash between payment and issuing)."""
    for b in db.bookings.find({"user_id": user_id, "status": "confirmed", "payment_status": "successful"}):
        if db.tickets.count_documents({"booking_id": b["_id"]}) < b["quantity"]:
            finalize_confirmed_booking(db, b)


def cancel_tickets_for_booking(db, booking_id: int, at: datetime | None = None):
    db.tickets.update_many({"booking_id": booking_id, "ticket_status": "valid"},
                           {"$set": {"ticket_status": "cancelled", "cancelled_at": at or now()}})


def ticket_context(db, tickets: list) -> dict:
    from .services import match_context
    matches = {m["_id"]: m for m in db.matches.find({"_id": {"$in": list({t["match_id"] for t in tickets})}})}
    ctx = match_context(db, list(matches.values())) if matches else {"stadiums": {}, "sports": {}}
    users = {u["_id"]: u for u in db.users.find({"_id": {"$in": list({t["user_id"] for t in tickets})}}, {"name": 1})}
    return {"matches": matches, "ctx": ctx, "users": users}


def _iso(v):
    return v.isoformat() if v else None


def tickets_out(db, tickets: list, with_qr: bool = False) -> list:
    if not tickets:
        return []
    from .qr import qr_data_uri
    c = ticket_context(db, tickets)
    today = date.today().isoformat()
    out = []
    for t in tickets:
        m = c["matches"].get(t["match_id"])
        st = c["ctx"]["stadiums"].get(m["stadium_id"]) if m else None
        sp = c["ctx"]["sports"].get(st["sport_id"]) if st else None
        phase = "completed" if m and (m["match_date"] < today or m["status"] == "completed") else "upcoming"
        if t["ticket_status"] == "valid" and (phase == "completed" or (m and m["status"] == "cancelled")):
            validity = "expired" if phase == "completed" else "cancelled"
        else:
            validity = t["ticket_status"]
        o = {"ticket_id": t["ticket_id"], "booking_id": t["booking_id"], "booking_reference": t["booking_reference"],
             "ticket_status": t["ticket_status"], "validity": validity, "payment_status": t["payment_status"],
             "category": t["ticket_category"], "seat_number": t.get("seat_number"), "seat": t.get("seat"), "seq": t["seq"],
             "customer_name": c["users"].get(t["user_id"], {}).get("name"),
             "sport": sp["name"] if sp else None, "match": f'{m["team_a"]} vs {m["team_b"]}' if m else None,
             "match_id": t["match_id"], "stadium": st["name"] if st else None, "location": st["location"] if st else None,
             "match_date": m["match_date"] if m else None, "timing": m["timing"] if m else None, "phase": phase,
             "created_at": _iso(t.get("created_at")), "used_at": _iso(t.get("used_at")),
             "verified_by": t.get("verified_by_name"), "cancelled_at": _iso(t.get("cancelled_at"))}
        if with_qr:
            payload = qr_payload(t)
            o["qr_payload"] = payload
            o["backup_code"] = payload[len(TOKEN_PREFIX):]
            o["qr_image"] = qr_data_uri(payload)
        out.append(o)
    return out


def user_tickets(db, user_id: int, scope: str = "all") -> list:
    heal_user(db, user_id)
    rows = tickets_out(db, list(db.tickets.find({"user_id": user_id}).sort([("created_at", -1), ("_id", -1)])))
    return [r for r in rows if scope == "all" or r["phase"] == scope]


# ---------------------------------------------------------------- verification at the gate
MESSAGES = {"success": "ENTRY ALLOWED", "already_used": "WARNING: TICKET ALREADY USED", "invalid": "ERROR: INVALID TICKET",
            "cancelled": "ERROR: TICKET CANCELLED", "unpaid": "ERROR: TICKET NOT PAID", "wrong_match": "ERROR: WRONG MATCH FOR THIS GATE",
            "not_match_day": "ERROR: NOT THE MATCH DAY"}


def _diagnose(ticket: dict, match: dict | None, expected_match_id: int | None) -> str | None:
    """Reason to refuse entry, or None if nothing in the stored data prevents entry."""
    if expected_match_id is not None and ticket["match_id"] != expected_match_id:
        return "wrong_match"
    if ticket["ticket_status"] == "cancelled" or (match and match["status"] == "cancelled"):
        return "cancelled"
    if ticket["payment_status"] != "successful":
        return "unpaid"
    if settings.ENFORCE_ENTRY_DATE and match and match["match_date"] != date.today().isoformat():
        return "not_match_day"
    if ticket["ticket_status"] == "used":
        return "already_used"
    return None


def verify_ticket(db, staff: dict, raw_token, expected_match_id: int | None = None) -> dict:
    t = now()
    token = normalize_token(raw_token)
    ticket = db.tickets.find_one({"verification_token_hash": token_hash(token)}) if token else None
    if not ticket:
        _log(db, staff, t, "invalid", None, expected_match_id, fingerprint=token_hash(token)[:12] if token else None)
        return _result("invalid", None, None, None, t)
    match = db.matches.find_one({"_id": ticket["match_id"]})
    reason = _diagnose(ticket, match, expected_match_id)
    if reason is None:
        flt = {"_id": ticket["_id"], "ticket_status": "valid", "payment_status": "successful"}
        if expected_match_id is not None:
            flt["match_id"] = expected_match_id
        # ATOMIC: of any number of simultaneous scans of this ticket, MongoDB lets exactly one match 'valid'.
        won = db.tickets.find_one_and_update(
            flt, {"$set": {"ticket_status": "used", "used_at": t, "verified_by": staff["_id"], "verified_by_name": staff["name"]}},
            return_document=ReturnDocument.AFTER)
        if won:
            _log(db, staff, t, "success", won, expected_match_id)
            return _result("success", won, match, db, t)
        ticket = db.tickets.find_one({"_id": ticket["_id"]})  # lost the race or status changed: explain why
        reason = _diagnose(ticket, match, expected_match_id) or "already_used"
    _log(db, staff, t, reason, ticket, expected_match_id)
    return _result(reason, ticket, match, db, t)


def _result(result: str, ticket: dict | None, match: dict | None, db, at: datetime) -> dict:
    out = {"result": result, "ok": result == "success", "message": MESSAGES[result], "scanned_at": at.isoformat(),
           "ticket": None, "previous_entry": None}
    if ticket is not None:
        info = tickets_out(db, [ticket])[0]
        out["ticket"] = {k: info[k] for k in ("ticket_id", "booking_reference", "customer_name", "category", "sport", "match",
                                              "stadium", "location", "match_date", "timing", "ticket_status", "payment_status", "seat_number", "seat")}
        if result == "already_used":
            out["previous_entry"] = {"used_at": info["used_at"], "verified_by": info["verified_by"]}
    return out


def _log(db, staff: dict, at: datetime, result: str, ticket: dict | None, expected_match_id, fingerprint=None):
    db.entry_logs.insert_one({"_id": next_id(db, "entry_logs"), "result": result, "scanned_at": at, "scanned_by": staff["_id"],
                              "scanned_by_name": staff["name"], "ticket_id": ticket["ticket_id"] if ticket else None,
                              "booking_reference": ticket["booking_reference"] if ticket else None,
                              "match_id": ticket["match_id"] if ticket else None, "expected_match_id": expected_match_id,
                              "token_fingerprint": fingerprint})  # never the token itself


def entry_logs_out(rows: list) -> list:
    return [{"id": r["_id"], "result": r["result"], "scanned_at": _iso(r["scanned_at"]), "scanned_by": r["scanned_by_name"],
             "ticket_id": r.get("ticket_id"), "booking_reference": r.get("booking_reference"), "match_id": r.get("match_id"),
             "expected_match_id": r.get("expected_match_id")} for r in rows]


# ---------------------------------------------------------------- admin queries
def search_tickets(db, q=None, match_id=None, status=None, limit=100, offset=0) -> list:
    conds = []
    if q and q.strip():
        rx = {"$regex": re.escape(q.strip().upper())}
        conds.append({"$or": [{"ticket_id": rx}, {"booking_reference": rx}]})
    if match_id:
        conds.append({"match_id": match_id})
    if status in ("valid", "used", "cancelled"):
        conds.append({"ticket_status": status})
    flt = {"$and": conds} if conds else {}
    rows = db.tickets.find(flt).sort([("_id", -1)]).skip(max(offset, 0)).limit(min(max(limit, 1), 200))
    return tickets_out(db, list(rows))


def list_entry_logs(db, match_id=None, result=None, ticket_id=None, staff_id=None, limit=100, offset=0) -> list:
    flt = {}
    if match_id: flt["$or"] = [{"match_id": match_id}, {"expected_match_id": match_id}]
    if result: flt["result"] = result
    if ticket_id: flt["ticket_id"] = ticket_id.strip().upper()
    if staff_id: flt["scanned_by"] = staff_id
    rows = db.entry_logs.find(flt).sort([("scanned_at", -1), ("_id", -1)]).skip(max(offset, 0)).limit(min(max(limit, 1), 200))
    return entry_logs_out(list(rows))


def ticket_stats(db, match_id: int | None = None) -> dict:
    mflt = {"match_id": match_id} if match_id else {}
    per = defaultdict(lambda: {"valid": 0, "used": 0, "cancelled": 0})
    for r in db.tickets.aggregate([{"$match": mflt}, {"$group": {"_id": {"m": "$match_id", "s": "$ticket_status"}, "n": {"$sum": 1}}}]):
        per[r["_id"]["m"]][r["_id"]["s"]] = r["n"]
    inv = {r["_id"]: r for r in db.ticket_inventory.aggregate(
        [{"$match": mflt}, {"$group": {"_id": "$match_id", "total": {"$sum": "$total"}, "available": {"$sum": "$available"}}}])}
    held = {r["_id"]: r["n"] for r in db.bookings.aggregate(
        [{"$match": {**mflt, "status": "pending_payment"}}, {"$group": {"_id": "$match_id", "n": {"$sum": "$quantity"}}}])}
    ids = sorted(set(per) | ({match_id} if match_id else set()))
    matches = {m["_id"]: m for m in db.matches.find({"_id": {"$in": ids}})}
    rows = []
    for mid in ids:
        c, m = per[mid], matches.get(mid)
        issued = c["valid"] + c["used"] + c["cancelled"]
        sold = issued - c["cancelled"]
        rows.append({"match_id": mid, "match": f'{m["team_a"]} vs {m["team_b"]}' if m else None, "match_date": m["match_date"] if m else None,
                     "issued": issued, "sold": sold, "valid": c["valid"], "used": c["used"], "cancelled": c["cancelled"],
                     "remaining_for_sale": inv.get(mid, {}).get("available", 0), "held_unpaid": held.get(mid, 0),
                     "capacity_tickets": inv.get(mid, {}).get("total", 0),
                     "attendance_percent": round(100 * c["used"] / sold, 1) if sold else 0.0})
    rows.sort(key=lambda r: (r["match_date"] or "", r["match_id"]))
    keys = ("issued", "sold", "valid", "used", "cancelled", "remaining_for_sale", "held_unpaid")
    totals = {k: sum(r[k] for r in rows) for k in keys}
    totals["attendance_percent"] = round(100 * totals["used"] / totals["sold"], 1) if totals["sold"] else 0.0
    return {"totals": totals, "matches": rows,
            "definitions": {"sold": "issued minus cancelled", "valid": "sold and not yet used (still allowed to enter)",
                            "remaining_for_sale": "tickets still on sale (excludes unpaid holds)", "held_unpaid": "reserved but not yet paid"}}
