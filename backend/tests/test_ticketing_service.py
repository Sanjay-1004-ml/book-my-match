"""Service-level tests for payments, ticket issuing and gate verification.
They only need a `db` (see conftest.py) and no HTTP layer, so every rule is tested directly against MongoDB."""
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from app import ticketing
from app.clock import now
from app.config import settings
from app.database import dec
from app.pricing import ticket_quote
from app.ticketing import TicketError


@contextmanager
def patched(obj, **vals):
    old = {k: getattr(obj, k) for k in vals}
    for k, v in vals.items(): setattr(obj, k, v)
    try:
        yield
    finally:
        for k, v in old.items(): setattr(obj, k, v)


def user(db, email="u1@test.com"):
    """Find or create a normal user (API tests register u1@test.com themselves, so it is not pre-seeded)."""
    from app.database import next_id
    u = db.users.find_one({"email": email})
    if not u:
        u = {"_id": next_id(db, "users"), "name": "User One" if email.startswith("u1") else "User Two", "email": email,
             "password_hash": "x", "role": "user", "is_active": True, "phone": None, "created_at": now()}
        db.users.insert_one(u)
    return u
def staff(db): return db.users.find_one({"email": "staff@test.com"})


def hold(db, user_id=None, qty=2, match_index=0, minutes=10):
    """What POST /api/bookings does: reserve inventory and create an unpaid booking."""
    uid = user_id or user(db)["_id"]
    m = list(db.matches.find({}).sort([("match_date", 1), ("_id", 1)]))[match_index]
    inv = db.ticket_inventory.find_one({"match_id": m["_id"]}, sort=[("_id", 1)])
    cat = db.ticket_categories.find_one({"_id": inv["category_id"]})
    assert db.ticket_inventory.update_one({"_id": inv["_id"], "available": {"$gte": qty}}, {"$inc": {"available": -qty}}).modified_count == 1
    q = ticket_quote(dec(cat["price"]), qty, settings.FEE_PERCENT, settings.TICKET_TAX_PERCENT)
    from app.database import next_id, d128
    from app.models_util import new_reference
    b = {"_id": next_id(db, "bookings"), "reference": new_reference(), "user_id": uid, "match_id": m["_id"], "category_id": cat["_id"],
         "quantity": qty, "unit_price": d128(q["unit_price"]), "subtotal": d128(q["subtotal"]), "fee": d128(q["fee"]),
         "tax": d128(q["tax"]), "total": d128(q["total"]), "status": "pending_payment", "payment_status": "pending",
         "payment_ref": None, "refund_status": None, "expires_at": now() + timedelta(minutes=minutes),
         "created_at": now(), "cancelled_at": None}
    db.bookings.insert_one(b)
    return b


def available(db, b):
    return db.ticket_inventory.find_one({"match_id": b["match_id"], "category_id": b["category_id"]})["available"]


def paid_booking(db, qty=2, **kw):
    b = hold(db, qty=qty, **kw)
    r = ticketing.process_payment(db, b["user_id"], b["_id"], "upi")
    assert r["outcome"] == "success"
    return db.bookings.find_one({"_id": b["_id"]}), list(db.tickets.find({"booking_id": b["_id"]}).sort("seq", 1))


# ---------------------------------------------------------------- payment + tickets
def test_successful_payment_confirms_booking_and_issues_one_unique_ticket_per_seat(db):
    b = hold(db, qty=3)
    r = ticketing.process_payment(db, b["user_id"], b["_id"], "credit_card")
    assert r["outcome"] == "success"
    b2 = db.bookings.find_one({"_id": b["_id"]})
    assert (b2["status"], b2["payment_status"]) == ("confirmed", "successful")
    assert b2["payment_ref"].startswith("TXN") and b2["paid_at"] and b2["expires_at"] is None
    pay = db.payments.find_one({"booking_id": b["_id"]})
    assert pay["status"] == "successful" and pay["transaction_id"] == b2["payment_ref"] and pay["method"] == "credit_card"
    assert dec(pay["amount"]) == dec(b["total"])
    ts = list(db.tickets.find({"booking_id": b["_id"]}).sort("seq", 1))
    assert len(ts) == 3 and [t["seq"] for t in ts] == [1, 2, 3]
    assert len({t["ticket_id"] for t in ts}) == 3 and len({t["verification_token_hash"] for t in ts}) == 3
    payloads = [ticketing.qr_payload(t) for t in ts]
    assert len(set(payloads)) == 3 and all(p.startswith("BMMT:") for p in payloads)
    for t, p in zip(ts, payloads):  # hash in DB matches the token the QR carries
        assert t["verification_token_hash"] == ticketing.token_hash(ticketing.normalize_token(p))
        assert (t["ticket_status"], t["payment_status"], t["used_at"], t["verified_by"]) == ("valid", "successful", None, None)
        assert t["user_id"] == b["user_id"] and t["match_id"] == b["match_id"] and t["booking_reference"] == b["reference"]


def test_qr_token_is_never_stored_or_exposed_in_clear(db):
    _, ts = paid_booking(db, qty=2)
    tokens = {ticketing.normalize_token(ticketing.qr_payload(t)) for t in ts}
    for coll in db.list_collection_names():
        for doc in db[coll].find({}):
            blob = repr(doc)
            assert not any(tok in blob for tok in tokens), f"plaintext token found in {coll}"
    from app.qr import qr_svg
    for t in ts:  # nothing but the token is encoded; the markup itself carries no personal data
        svg = qr_svg(ticketing.qr_payload(t))
        assert "@" not in svg and t["ticket_id"] not in svg and user(db)["name"] not in svg


def test_failed_payment_creates_no_tickets_keeps_hold_and_can_be_retried(db):
    b = hold(db, qty=2)
    before = available(db, b)
    with patched(settings, DEMO_PAYMENT_MODE="selectable"):
        r = ticketing.process_payment(db, b["user_id"], b["_id"], "debit_card", simulate="failure")
    assert r["outcome"] == "failed" and r["payment"]["status"] == "failed" and r["payment"]["failure_reason"]
    b2 = db.bookings.find_one({"_id": b["_id"]})
    assert (b2["status"], b2["payment_status"]) == ("pending_payment", "failed")
    assert db.tickets.count_documents({}) == 0 and available(db, b) == before  # still held, nothing lost or confirmed
    r = ticketing.process_payment(db, b["user_id"], b["_id"], "debit_card")  # retry succeeds
    assert r["outcome"] == "success" and db.tickets.count_documents({"booking_id": b["_id"]}) == 2
    assert db.payments.count_documents({"booking_id": b["_id"]}) == 2  # one failed + one successful attempt on record


def test_server_modes_override_the_client_choice(db):
    b = hold(db, qty=1)
    with patched(settings, DEMO_PAYMENT_MODE="failure"):
        assert ticketing.process_payment(db, b["user_id"], b["_id"], "upi", simulate="success")["outcome"] == "failed"
    with patched(settings, DEMO_PAYMENT_MODE="success"):
        assert ticketing.process_payment(db, b["user_id"], b["_id"], "upi", simulate="failure")["outcome"] == "success"
    outcomes = set()
    with patched(settings, DEMO_PAYMENT_MODE="random", DEMO_PAYMENT_SUCCESS_RATE=0.5):
        outcomes = {ticketing.decide_outcome(None) for _ in range(200)}
    assert outcomes == {True, False}


def test_expired_reservation_releases_inventory_exactly_once_and_cannot_be_paid(db):
    b = hold(db, qty=4, minutes=-1)  # already past its hold time
    held = available(db, b)
    try:
        ticketing.process_payment(db, b["user_id"], b["_id"], "upi")
        assert False, "payment of an expired reservation must be refused"
    except TicketError as e:
        assert e.status == 410
    assert available(db, b) == held + 4
    assert db.bookings.find_one({"_id": b["_id"]})["status"] == "expired"
    assert ticketing.expire_stale(db) == 0 and available(db, b) == held + 4  # second sweep releases nothing more
    assert ticketing.expire_booking(db, b["_id"]) is False and available(db, b) == held + 4  # nor does a direct second call
    assert db.tickets.count_documents({}) == 0 and db.payments.count_documents({}) == 0


def test_sweeper_releases_only_expired_holds(db):
    old, fresh = hold(db, qty=2, minutes=-5), hold(db, qty=2, minutes=5, match_index=1)
    assert ticketing.expire_stale(db) == 1
    assert db.bookings.find_one({"_id": old["_id"]})["status"] == "expired"
    assert db.bookings.find_one({"_id": fresh["_id"]})["status"] == "pending_payment"


def test_paying_twice_never_double_charges_or_double_issues(db):
    b = hold(db, qty=2)
    r1 = ticketing.process_payment(db, b["user_id"], b["_id"], "upi")
    r2 = ticketing.process_payment(db, b["user_id"], b["_id"], "net_banking")
    assert (r1["outcome"], r2["outcome"]) == ("success", "already_paid")
    assert r2["payment"]["transaction_id"] == r1["payment"]["transaction_id"]
    assert db.payments.count_documents({"booking_id": b["_id"], "status": "successful"}) == 1
    assert db.tickets.count_documents({"booking_id": b["_id"]}) == 2


def test_simultaneous_payments_of_one_booking_create_one_payment_and_the_right_tickets(db):
    b = hold(db, qty=3)
    outcomes, errors = [], []
    def go():
        try: outcomes.append(ticketing.process_payment(db, b["user_id"], b["_id"], "upi")["outcome"])
        except Exception as e: errors.append(repr(e))
    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert not errors, errors
    assert outcomes.count("success") == 1 and outcomes.count("already_paid") == 7
    assert db.payments.count_documents({"booking_id": b["_id"], "status": "successful"}) == 1
    assert db.tickets.count_documents({"booking_id": b["_id"]}) == 3


def test_only_the_owner_can_pay_and_invalid_inputs_are_refused(db):
    b = hold(db, qty=1)
    other = user(db, "u2@test.com")["_id"]
    for args, status in [((other, b["_id"], "upi"), 404), ((b["user_id"], 99999, "upi"), 404), ((b["user_id"], b["_id"], "bitcoin"), 400)]:
        try: ticketing.process_payment(db, *args); assert False
        except TicketError as e: assert e.status == status
    with patched(settings, PAYMENTS_DEMO_ENABLED=False):
        try: ticketing.process_payment(db, b["user_id"], b["_id"], "upi"); assert False
        except TicketError as e: assert e.status == 503
    assert db.tickets.count_documents({}) == 0 and db.payments.count_documents({}) == 0


def test_tickets_are_healed_if_the_server_died_after_confirming_payment(db):
    b, ts = paid_booking(db, qty=3)
    db.tickets.delete_one({"booking_id": b["_id"], "seq": 2}); db.tickets.delete_many({"booking_id": b["_id"], "seq": 3})
    db.payments.delete_many({"booking_id": b["_id"]})
    mine = ticketing.user_tickets(db, b["user_id"])
    assert len(mine) == 3 and db.tickets.count_documents({"booking_id": b["_id"]}) == 3
    assert db.payments.count_documents({"booking_id": b["_id"], "status": "successful"}) == 1
    ticketing.user_tickets(db, b["user_id"])
    assert db.tickets.count_documents({"booking_id": b["_id"]}) == 3  # healing again adds nothing


# ---------------------------------------------------------------- gate verification
def scan(db, t, expected=None, who=None):
    return ticketing.verify_ticket(db, who or staff(db), ticketing.qr_payload(t), expected)


def test_valid_ticket_is_accepted_once_then_reported_as_already_used(db):
    b, (t1, t2) = paid_booking(db, qty=2)
    r = scan(db, t1)
    assert r["result"] == "success" and r["ok"] and r["message"] == "ENTRY ALLOWED"
    tk = r["ticket"]
    assert tk["ticket_id"] == t1["ticket_id"] and tk["booking_reference"] == b["reference"] and tk["customer_name"] == "User One"
    assert tk["match"] and tk["stadium"]
    doc = db.tickets.find_one({"_id": t1["_id"]})
    assert doc["ticket_status"] == "used" and doc["used_at"] and doc["verified_by"] == staff(db)["_id"] and doc["verified_by_name"] == "Gate Staff"
    again = scan(db, t1)
    assert again["result"] == "already_used" and not again["ok"] and again["message"] == "WARNING: TICKET ALREADY USED"
    assert again["previous_entry"]["verified_by"] == "Gate Staff" and again["previous_entry"]["used_at"]
    assert db.tickets.find_one({"_id": t2["_id"]})["ticket_status"] == "valid"  # sibling ticket untouched
    assert scan(db, t2)["result"] == "success"


def test_every_scan_attempt_is_recorded_without_the_token(db):
    _, (t1, _) = paid_booking(db, qty=2)
    scan(db, t1); scan(db, t1)
    ticketing.verify_ticket(db, staff(db), "BMMT:" + "A" * 32)
    logs = list(db.entry_logs.find({}).sort("_id", 1))
    assert [l["result"] for l in logs] == ["success", "already_used", "invalid"]
    assert all(l["scanned_by_name"] == "Gate Staff" and l["scanned_at"] for l in logs)
    assert logs[0]["ticket_id"] == t1["ticket_id"] and logs[2]["ticket_id"] is None
    assert "BMMT" not in repr(logs) and ("A" * 32) not in repr(logs)


def test_invalid_tokens_are_rejected(db):
    paid_booking(db, qty=1)
    for raw in ["", "   ", "garbage", "BMMT:", "BMMT:short", "A" * 31, "A" * 33, "BMMT:" + "A" * 32, "B" * 32, None, 12345, {"$gt": ""}, "BMMT:" + "!" * 32]:
        r = ticketing.verify_ticket(db, staff(db), raw)
        assert r["result"] == "invalid" and r["message"] == "ERROR: INVALID TICKET" and r["ticket"] is None, raw
    assert db.tickets.count_documents({"ticket_status": "used"}) == 0


def test_ticket_id_alone_is_not_accepted_as_a_token(db):
    _, (t1, _) = paid_booking(db, qty=2)
    assert ticketing.verify_ticket(db, staff(db), t1["ticket_id"])["result"] == "invalid"
    assert db.tickets.find_one({"_id": t1["_id"]})["ticket_status"] == "valid"


def test_cancelled_tickets_are_rejected(db):
    b, (t1, t2) = paid_booking(db, qty=2)
    ticketing.cancel_tickets_for_booking(db, b["_id"])
    r = scan(db, t1)
    assert r["result"] == "cancelled" and r["message"] == "ERROR: TICKET CANCELLED"
    assert db.tickets.find_one({"_id": t1["_id"]})["ticket_status"] == "cancelled"


def test_tickets_of_a_cancelled_match_are_rejected(db):
    b, (t1, _) = paid_booking(db, qty=2)
    db.matches.update_one({"_id": b["match_id"]}, {"$set": {"status": "cancelled"}})
    assert scan(db, t1)["result"] == "cancelled"
    assert db.tickets.find_one({"_id": t1["_id"]})["ticket_status"] == "valid"  # not consumed


def test_unpaid_tickets_are_rejected(db):
    _, (t1, _) = paid_booking(db, qty=2)
    db.tickets.update_one({"_id": t1["_id"]}, {"$set": {"payment_status": "pending"}})
    r = scan(db, t1)
    assert r["result"] == "unpaid" and r["message"] == "ERROR: TICKET NOT PAID"
    assert db.tickets.find_one({"_id": t1["_id"]})["ticket_status"] == "valid"


def test_wrong_match_is_rejected_and_the_ticket_is_not_consumed(db):
    b, (t1, _) = paid_booking(db, qty=2)
    other = db.matches.find_one({"_id": {"$ne": b["match_id"]}})["_id"]
    r = scan(db, t1, expected=other)
    assert r["result"] == "wrong_match" and "WRONG MATCH" in r["message"]
    assert db.tickets.find_one({"_id": t1["_id"]})["ticket_status"] == "valid"
    assert scan(db, t1, expected=b["match_id"])["result"] == "success"


def test_entry_date_rule_is_optional(db):
    b, (t1, _) = paid_booking(db, qty=2)  # first seeded match is ~2 weeks away, so not "today"
    with patched(settings, ENFORCE_ENTRY_DATE=True):
        assert scan(db, t1)["result"] == "not_match_day"
    assert db.tickets.find_one({"_id": t1["_id"]})["ticket_status"] == "valid"
    assert scan(db, t1)["result"] == "success"  # default config: no date rule


def test_simultaneous_scans_of_one_ticket_admit_exactly_one_person(db):
    b, (t1, _) = paid_booking(db, qty=2)
    staff_users = [staff(db), db.users.find_one({"email": "admin@test.com"})]
    results = []
    def go(i): results.append(ticketing.verify_ticket(db, staff_users[i % 2], ticketing.qr_payload(t1))["result"])
    ts = [threading.Thread(target=go, args=(i,)) for i in range(12)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert results.count("success") == 1 and results.count("already_used") == 11, results
    assert db.tickets.count_documents({"ticket_status": "used"}) == 1
    assert db.entry_logs.count_documents({"result": "success"}) == 1 and db.entry_logs.count_documents({}) == 12


# ---------------------------------------------------------------- views for users / admins
def test_my_tickets_only_returns_the_owners_tickets_and_phases(db):
    paid_booking(db, qty=2)
    b2 = hold(db, user_id=user(db, "u2@test.com")["_id"], qty=1, match_index=1)
    ticketing.process_payment(db, b2["user_id"], b2["_id"], "upi")
    mine = ticketing.user_tickets(db, user(db)["_id"])
    assert len(mine) == 2 and all(t["customer_name"] == "User One" for t in mine)
    assert all(t["phase"] == "upcoming" for t in mine) and ticketing.user_tickets(db, user(db)["_id"], "completed") == []
    assert len(ticketing.user_tickets(db, user(db, "u2@test.com")["_id"])) == 1
    assert "qr_image" not in mine[0] and "backup_code" not in mine[0]  # QR only on the single-ticket/booking views
    full = ticketing.tickets_out(db, [db.tickets.find_one({"user_id": user(db)["_id"]})], with_qr=True)[0]
    assert full["qr_image"].startswith("data:image/svg+xml;base64,") and full["qr_payload"].startswith("BMMT:")
    db.matches.update_one({"_id": mine[0]["match_id"]}, {"$set": {"match_date": "2020-01-01"}})
    done = ticketing.user_tickets(db, user(db)["_id"], "completed")
    assert done and done[0]["validity"] == "expired"


def test_admin_search_lists_and_statistics(db):
    b, (t1, t2, t3) = paid_booking(db, qty=3)
    scan(db, t1)
    ticketing.cancel_tickets_for_booking(db, b["_id"])  # cancels only the two unused ones
    assert [t["ticket_status"] for t in db.tickets.find({"booking_id": b["_id"]}).sort("seq", 1)] == ["used", "cancelled", "cancelled"]
    assert len(ticketing.search_tickets(db, q=t2["ticket_id"][4:8])) >= 1
    assert [t["ticket_id"] for t in ticketing.search_tickets(db, q=t2["ticket_id"])] == [t2["ticket_id"]]
    assert len(ticketing.search_tickets(db, q=b["reference"].lower())) == 3
    assert len(ticketing.search_tickets(db, match_id=b["match_id"], status="cancelled")) == 2
    assert len(ticketing.search_tickets(db, status="used")) == 1 and ticketing.search_tickets(db, q="NOPE-NOPE") == []
    b2, _ = paid_booking(db, qty=2, match_index=1)
    hold(db, qty=1, match_index=1)  # an unpaid hold on match 2
    st = ticketing.ticket_stats(db)
    row = {r["match_id"]: r for r in st["matches"]}[b["match_id"]]
    assert (row["issued"], row["sold"], row["valid"], row["used"], row["cancelled"]) == (3, 1, 0, 1, 2) and row["attendance_percent"] == 100.0
    row2 = {r["match_id"]: r for r in st["matches"]}[b2["match_id"]]
    assert (row2["sold"], row2["valid"], row2["used"], row2["held_unpaid"]) == (2, 2, 0, 1)
    t = st["totals"]
    assert (t["issued"], t["sold"], t["valid"], t["used"], t["cancelled"], t["held_unpaid"]) == (5, 3, 2, 1, 2, 1)
    inv = db.ticket_inventory.find_one({"match_id": b2["match_id"], "category_id": b2["category_id"]})
    only = ticketing.ticket_stats(db, b2["match_id"])
    assert len(only["matches"]) == 1 and only["matches"][0]["remaining_for_sale"] >= inv["available"]
    logs = ticketing.list_entry_logs(db, match_id=b["match_id"])
    assert len(logs) == 1 and logs[0]["result"] == "success" and logs[0]["scanned_by"] == "Gate Staff"
    assert ticketing.list_entry_logs(db, result="invalid") == [] and ticketing.list_entry_logs(db, ticket_id=t1["ticket_id"])


def test_pdf_has_one_page_per_ticket_and_qr_codes_decode(db):
    import io
    from app.ticket_pdf import build_tickets_pdf
    b, ts = paid_booking(db, qty=3)
    rows = ticketing.tickets_out(db, ts, with_qr=True)
    pdf = build_tickets_pdf(rows)
    assert pdf.startswith(b"%PDF") and pdf.count(b"/Type /Page\n") + pdf.count(b"/Type /Page ") >= 3


# ---------------------------------------------------------------- deterministic races
# Threads rarely interleave inside a tiny critical section, so these tests inject the competing request at the exact
# moment between "read" and "atomic write" - the only place a lost-update bug can live.
def test_scan_that_loses_a_race_after_reading_the_ticket_is_rejected(db):
    _, (t1, _) = paid_booking(db, qty=2)
    real, calls = ticketing._diagnose, {"n": 0}
    def racing(ticket, match, expected):
        calls["n"] += 1
        if calls["n"] == 1:  # another gate scans the same ticket while this request is between its read and its write
            other = db.users.find_one({"email": "admin@test.com"})
            assert ticketing.verify_ticket(db, other, ticketing.qr_payload(t1))["result"] == "success"
        return real(ticket, match, expected)
    with patched(ticketing, _diagnose=racing):
        r = scan(db, t1)
    assert r["result"] == "already_used" and r["previous_entry"]["verified_by"] == "Admin"
    assert db.entry_logs.count_documents({"result": "success"}) == 1
    assert db.tickets.find_one({"_id": t1["_id"]})["verified_by_name"] == "Admin"  # the first scanner keeps the entry


def test_payment_that_loses_a_race_after_reading_is_not_charged_twice(db):
    b = hold(db, qty=3)
    real, st = ticketing.payable_amount, {"done": False}
    def racing(db_, booking):
        if not st["done"]:
            st["done"] = True  # the same booking is paid from another tab meanwhile
            assert ticketing.process_payment(db, booking["user_id"], booking["_id"], "upi")["outcome"] == "success"
        return real(db_, booking)
    with patched(ticketing, payable_amount=racing):
        r = ticketing.process_payment(db, b["user_id"], b["_id"], "credit_card")
    assert r["outcome"] == "already_paid"
    assert db.payments.count_documents({"booking_id": b["_id"], "status": "successful"}) == 1
    assert db.tickets.count_documents({"booking_id": b["_id"]}) == 3
    assert db.bookings.find_one({"_id": b["_id"]})["payment_method"] == "upi"  # first payment is the one on record


def test_unique_indexes_block_duplicate_tickets_and_tokens(db):
    from pymongo.errors import DuplicateKeyError
    _, (t1, t2) = paid_booking(db, qty=2)
    def clone(**over):
        d = {k: v for k, v in t1.items() if k != "_id"}; d["_id"] = 10_000 + len(over); d.update(over); return d
    for over in ({"seq": t1["seq"], "ticket_id": "TKT-OTHER00001", "verification_token_hash": "h1"},   # same booking+seat slot
                 {"seq": 9, "ticket_id": t1["ticket_id"], "verification_token_hash": "h2"},          # same ticket id
                 {"seq": 9, "ticket_id": "TKT-OTHER00002", "verification_token_hash": t1["verification_token_hash"]}):  # same token
        try:
            db.tickets.insert_one(clone(**over)); assert False, over
        except DuplicateKeyError:
            pass
    assert db.tickets.count_documents({}) == 2


def test_backfill_upgrades_bookings_made_before_tickets_existed(db):
    from app.backfill_tickets import backfill
    # a legacy PAID booking (old flow: status 'confirmed', payment 'successful', ref 'DEMO-...', no tickets, no expiry fields)
    paid = hold(db, qty=2)
    db.bookings.update_one({"_id": paid["_id"]}, {"$set": {"status": "confirmed", "payment_status": "successful", "payment_ref": "DEMO-ABC12345", "expires_at": None}})
    # a legacy UNPAID booking (old flow: 'confirmed' but payment pending)
    unpaid = hold(db, qty=1, match_index=1)
    db.bookings.update_one({"_id": unpaid["_id"]}, {"$set": {"status": "confirmed", "expires_at": None}})
    assert backfill(db, dry_run=True) == {"paid_without_tickets": 1, "tickets_created": 2, "unpaid_made_pending": 1}
    assert db.tickets.count_documents({}) == 0 and db.bookings.find_one({"_id": unpaid["_id"]})["status"] == "confirmed"  # dry run changed nothing
    assert backfill(db) == {"paid_without_tickets": 1, "tickets_created": 2, "unpaid_made_pending": 1}
    ts = list(db.tickets.find({"booking_id": paid["_id"]}))
    assert len(ts) == 2 and all(t["ticket_status"] == "valid" for t in ts)
    assert db.payments.find_one({"transaction_id": "DEMO-ABC12345"})["status"] == "successful"
    u = db.bookings.find_one({"_id": unpaid["_id"]})
    assert u["status"] == "pending_payment" and u["expires_at"] > now()
    assert backfill(db) == {"paid_without_tickets": 0, "tickets_created": 0, "unpaid_made_pending": 0}  # re-run is a no-op
    assert db.tickets.count_documents({}) == 2
    assert scan(db, ts[0])["result"] == "success"  # upgraded legacy tickets work at the gate


def test_clock_is_venue_local_and_countdown_is_computed_by_the_server(db):
    from zoneinfo import ZoneInfo
    from app import clock
    from app.services import booking_out
    ist = datetime.now(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)
    assert abs((clock.now() - ist).total_seconds()) < 5  # default APP_TIMEZONE is Asia/Kolkata whatever the server's own zone is
    b = hold(db, qty=1, minutes=10)
    out = booking_out(db, db.bookings.find_one({"_id": b["_id"]}))
    assert 590 <= out["expires_in_seconds"] <= 600  # the browser counts down from this, never from its own clock
    db.bookings.update_one({"_id": b["_id"]}, {"$set": {"expires_at": now() - timedelta(minutes=1)}})
    assert booking_out(db, db.bookings.find_one({"_id": b["_id"]}))["expires_in_seconds"] == 0
    assert booking_out(db, {**db.bookings.find_one({"_id": b["_id"]}), "expires_at": None})["expires_in_seconds"] is None
