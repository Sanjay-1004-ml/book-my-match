"""Service-level tests for the food section: cart quote, bill, food payment (standalone and with a booking), duplicate
payment protection, pickup details / QR / receipt and the description backfill.
Like test_ticketing_service.py they only need a `db` (see conftest.py), no HTTP layer."""
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from app import ticketing, food_service, services
from app.clock import now
from app.config import settings
from app.database import d128, dec, next_id
from app.pricing import food_quote
from app.ticketing import TicketError
from .test_ticketing_service import hold, user, patched


def L(db, name, qty):
    """A cart line (what the API receives) for the seeded item called `name`."""
    return SimpleNamespace(item_id=db.food_items.find_one({"name": name})["_id"], quantity=qty)


def place_order(db, lines, booking_id=None, user_id=None):
    """What POST /api/food/orders does: server-priced, stored with a price snapshot."""
    uid = user_id or user(db)["_id"]
    resolved = services.resolve_food(db, lines)
    fq = food_service.bill_for(resolved)
    o = {"_id": next_id(db, "food_orders"), "user_id": uid, "booking_id": booking_id, "subtotal": d128(fq["subtotal"]),
         "fee": d128(fq["fee"]), "tax": d128(fq["tax"]), "total": d128(fq["total"]), "status": "placed",
         "payment_status": "pending", "created_at": now(), "items": services.food_order_doc_items(resolved)}
    db.food_orders.insert_one(o)
    return o


def confirmed_booking(db, qty=2, **kw):
    b = hold(db, qty=qty, **kw)
    r = ticketing.process_payment(db, b["user_id"], b["_id"], "upi")
    assert r["outcome"] == "success"
    return db.bookings.find_one({"_id": b["_id"]})


def payments_for(db, order_id):
    return list(db.payments.find({"food_order_id": order_id}))


# ---------------------------------------------------------------- bill / quote
def test_food_quote_without_fee_matches_the_original_calculation():
    from app.pricing import pct, money
    q = food_quote([(Decimal("80"), 2), (Decimal("45"), 1)], Decimal("5"))
    assert (q["subtotal"], q["fee"], q["tax"], q["total"]) == (Decimal("205.00"), Decimal("0.00"), Decimal("10.25"), Decimal("215.25"))
    assert q["total"] == money(Decimal("205") + pct(Decimal("205"), Decimal("5")))  # the pre-enhancement formula


def test_food_quote_fee_then_tax_on_subtotal_plus_fee_with_half_up_rounding():
    q = food_quote([(Decimal("80"), 2)], Decimal("5"), Decimal("10"))
    assert (q["subtotal"], q["fee"], q["tax"], q["total"]) == (Decimal("160.00"), Decimal("16.00"), Decimal("8.80"), Decimal("184.80"))
    q = food_quote([(Decimal("15"), 1)], Decimal("5"), Decimal("5"))  # 15 + 0.75 fee = 15.75; tax 0.7875 -> 0.79
    assert (q["fee"], q["tax"], q["total"]) == (Decimal("0.75"), Decimal("0.79"), Decimal("16.54"))


def test_quote_cart_is_priced_from_the_database_and_merges_duplicate_lines(db):
    brownie = db.food_items.find_one({"name": "Brownie"})
    cart = [L(db, "Brownie", 1), L(db, "Brownie", 1), L(db, "Tea", 3)]
    q = food_service.quote_cart(db, cart)
    assert [(i["name"], i["quantity"], i["unit_price"], i["line_total"]) for i in q["lines"]] == [("Brownie", 2, 80.0, 160.0), ("Tea", 3, 15.0, 45.0)]
    assert (q["subtotal"], q["fee"], q["tax"], q["total"], q["item_count"]) == (205.0, 0.0, 10.25, 215.25, 5)
    db.food_items.update_one({"_id": brownie["_id"]}, {"$set": {"price": d128("100")}})  # admin changes the price
    assert food_service.quote_cart(db, [L(db, "Brownie", 1)])["subtotal"] == 100.0       # quote follows the database


def test_quote_cart_reports_unavailable_items_and_never_charges_them(db):
    waffle = db.food_items.find_one({"name": "Waffle"})
    db.food_items.update_one({"_id": waffle["_id"]}, {"$set": {"is_active": False}})
    q = food_service.quote_cart(db, [L(db, "Brownie", 1), L(db, "Waffle", 2), SimpleNamespace(item_id=99999, quantity=1)])
    assert [i["name"] for i in q["lines"]] == ["Brownie"] and q["subtotal"] == 80.0
    assert {(u["item_id"], u["name"], u["reason"]) for u in q["unavailable"]} == {
        (waffle["_id"], "Waffle", "no_longer_available"), (99999, None, "not_found")}
    empty = food_service.quote_cart(db, [])
    assert empty["lines"] == [] and empty["total"] == 0.0 and empty["item_count"] == 0


def test_quote_cart_uses_the_configured_fee(db):
    with patched(settings, FOOD_FEE_PERCENT=Decimal("10")):
        q = food_service.quote_cart(db, [L(db, "Brownie", 2)])
        assert (q["subtotal"], q["fee"], q["tax"], q["total"]) == (160.0, 16.0, 8.8, 184.8)
        assert q["rates"] == {"food_tax_percent": 5.0, "food_fee_percent": 10.0}
        o = place_order(db, [L(db, "Brownie", 2)])
        assert dec(o["total"]) == Decimal("184.80") and services.food_order_out(o)["fee"] == 16.0


def test_legacy_order_without_fee_or_payment_fields_still_renders(db):
    o = place_order(db, [L(db, "Tea", 1)])
    for k in ("fee", "payment_ref", "paid_at", "payment_method"):
        o.pop(k, None)
    out = services.food_order_out(o)
    assert out["fee"] == 0.0 and out["transaction_id"] is None and out["pickup_code"] is None and out["order_number"].startswith("FO")


# ---------------------------------------------------------------- paying a standalone food order
def test_standalone_food_payment_success_creates_one_payment_and_a_pickup_code(db):
    o = place_order(db, [L(db, "Brownie", 2), L(db, "Tea", 1)])
    assert services.food_order_out(o)["pickup_code"] is None  # nothing to collect before it is paid
    r = ticketing.process_food_payment(db, o["user_id"], o["_id"], "upi")
    assert r["outcome"] == "success"
    p = r["payment"]
    assert p["kind"] == "food" and p["food_order_id"] == o["_id"] and p["booking_id"] is None and p["simulated"] is True
    assert dec(p["amount"]) == dec(o["total"]) == Decimal("183.75") and p["status"] == "successful"   # (160+15) * 1.05
    cur = db.food_orders.find_one({"_id": o["_id"]})
    assert cur["payment_status"] == "successful" and cur["payment_ref"] == p["transaction_id"] and cur["paid_at"] is not None
    out = services.food_order_out(cur)
    assert len(out["pickup_code"]) == 6 and out["transaction_id"] == p["transaction_id"]
    assert ticketing.payment_out(p)["food_order_id"] == o["_id"] and ticketing.payment_out(p)["kind"] == "food"


def test_paying_the_same_food_order_twice_never_charges_twice(db):
    o = place_order(db, [L(db, "Burger", 1)])
    r1 = ticketing.process_food_payment(db, o["user_id"], o["_id"], "credit_card")
    r2 = ticketing.process_food_payment(db, o["user_id"], o["_id"], "debit_card")
    assert (r1["outcome"], r2["outcome"]) == ("success", "already_paid")
    assert r2["payment"]["transaction_id"] == r1["payment"]["transaction_id"]
    assert len(payments_for(db, o["_id"])) == 1


def test_food_payment_that_loses_a_race_is_reported_as_already_paid(db):
    o = place_order(db, [L(db, "Burger", 1)])
    real, calls = ticketing.decide_outcome, {"n": 0}
    def racing(simulate):
        calls["n"] += 1
        if calls["n"] == 1:  # a second tab pays the same order between this request's read and its atomic flip
            assert ticketing.process_food_payment(db, o["user_id"], o["_id"], "upi")["outcome"] == "success"
        return real(simulate)
    with patched(ticketing, decide_outcome=racing):
        r = ticketing.process_food_payment(db, o["user_id"], o["_id"], "upi")
    assert r["outcome"] == "already_paid" and len(payments_for(db, o["_id"])) == 1


def test_failed_food_payment_can_be_retried_and_only_the_success_counts(db):
    o = place_order(db, [L(db, "Pizza", 1)])
    r = ticketing.process_food_payment(db, o["user_id"], o["_id"], "upi", simulate="failure")
    assert r["outcome"] == "failed" and r["payment"]["status"] == "failed" and r["payment"]["failure_reason"]
    cur = db.food_orders.find_one({"_id": o["_id"]})
    assert cur["payment_status"] == "failed" and cur["status"] == "placed" and cur.get("payment_ref") is None
    assert services.food_order_out(cur)["pickup_code"] is None
    r = ticketing.process_food_payment(db, o["user_id"], o["_id"], "upi", simulate="success")
    assert r["outcome"] == "success"
    assert sorted(p["status"] for p in payments_for(db, o["_id"])) == ["failed", "successful"]


def test_food_payment_guards(db):
    o = place_order(db, [L(db, "Tea", 1)])
    other = user(db, "u2@test.com")["_id"]
    for args, status in [((other, o["_id"], "upi"), 404), ((o["user_id"], 99999, "upi"), 404), ((o["user_id"], o["_id"], "bitcoin"), 400)]:
        try:
            ticketing.process_food_payment(db, *args); assert False, "should have been refused"
        except TicketError as e:
            assert e.status == status
    with patched(settings, PAYMENTS_DEMO_ENABLED=False):
        try:
            ticketing.process_food_payment(db, o["user_id"], o["_id"], "upi"); assert False
        except TicketError as e:
            assert e.status == 503
    db.food_orders.update_one({"_id": o["_id"]}, {"$set": {"status": "cancelled"}})
    try:
        ticketing.process_food_payment(db, o["user_id"], o["_id"], "upi"); assert False
    except TicketError as e:
        assert e.status == 409 and "cancelled" in e.message
    assert db.payments.count_documents({"food_order_id": o["_id"]}) == 0


def test_food_payment_is_refused_if_an_item_became_unavailable(db):
    o = place_order(db, [L(db, "Brownie", 1), L(db, "Waffle", 1)])
    db.food_items.update_one({"name": "Waffle"}, {"$set": {"is_active": False}})
    try:
        ticketing.process_food_payment(db, o["user_id"], o["_id"], "upi"); assert False
    except TicketError as e:
        assert e.status == 409 and "Waffle" in e.message
    assert db.payments.count_documents({"food_order_id": o["_id"]}) == 0
    assert db.food_orders.find_one({"_id": o["_id"]})["payment_status"] == "pending"  # nothing was charged


# ---------------------------------------------------------------- together with tickets / after tickets
def test_food_on_a_pending_booking_is_paid_with_the_tickets_and_not_separately(db):
    b = hold(db, qty=2)
    o = place_order(db, [L(db, "Brownie", 2)], booking_id=b["_id"])
    try:
        ticketing.process_food_payment(db, b["user_id"], o["_id"], "upi"); assert False
    except TicketError as e:
        assert e.status == 409 and "together with the tickets" in e.message
    det = food_service.food_order_detail(db, o)
    assert det["pay_with_booking"] is True and det["can_pay"] is False and det["can_cancel"] is False
    ticket_total, food_total = dec(b["total"]), dec(o["total"])
    assert ticketing.payable_amount(db, b) == ticket_total + food_total
    r = ticketing.process_payment(db, b["user_id"], b["_id"], "upi")
    assert r["outcome"] == "success" and dec(r["payment"]["amount"]) == ticket_total + food_total
    cur = db.food_orders.find_one({"_id": o["_id"]})
    assert cur["payment_status"] == "successful" and cur["payment_ref"] == r["payment"]["transaction_id"] and cur["paid_at"] is not None
    assert db.payments.count_documents({}) == 1                              # ONE payment covers tickets + food
    assert db.tickets.count_documents({"booking_id": b["_id"]}) == 2         # ticket issuing is unchanged
    again = ticketing.process_food_payment(db, b["user_id"], o["_id"], "upi")  # already paid through the booking
    assert again["outcome"] == "already_paid" and again["payment"]["transaction_id"] == r["payment"]["transaction_id"]
    assert db.payments.count_documents({}) == 1


def test_booking_with_food_reports_what_was_actually_paid(db):
    b = hold(db, qty=1)
    o = place_order(db, [L(db, "Tea", 2)], booking_id=b["_id"])
    ticketing.process_payment(db, b["user_id"], b["_id"], "upi")
    out = services.booking_out(db, db.bookings.find_one({"_id": b["_id"]}), detail=True)
    expected = float(dec(b["total"]) + dec(o["total"]))
    assert out["grand_total"] == expected and out["paid_amount"] == expected and out["food_due"] == 0.0
    assert out["food_paid_total"] == float(dec(o["total"]))


def test_food_added_after_the_booking_is_paid_is_payable_on_its_own(db):
    b = confirmed_booking(db)
    paid_at_booking = dec(b["paid_amount"])
    o = place_order(db, [L(db, "Coffee", 2)], booking_id=b["_id"])
    det = food_service.food_order_detail(db, o)
    assert det["can_pay"] is True and det["pay_with_booking"] is False and det["can_cancel"] is True
    out = services.booking_out(db, b, detail=True)
    assert out["food_due"] == float(dec(o["total"])) and out["paid_amount"] == float(paid_at_booking)  # booking payment untouched
    r = ticketing.process_food_payment(db, b["user_id"], o["_id"], "upi")
    assert r["outcome"] == "success" and r["payment"]["booking_id"] == b["_id"] and r["payment"]["booking_reference"] == b["reference"]
    assert db.payments.count_documents({}) == 2
    assert db.bookings.find_one({"_id": b["_id"]})["payment_ref"] != r["payment"]["transaction_id"]  # booking payment is not overwritten
    out = services.booking_out(db, db.bookings.find_one({"_id": b["_id"]}), detail=True)
    assert out["food_due"] == 0.0 and out["paid_amount"] == float(paid_at_booking)


def test_food_for_a_cancelled_or_expired_booking_cannot_be_paid(db):
    b = hold(db, qty=1)
    o = place_order(db, [L(db, "Tea", 1)], booking_id=b["_id"])
    db.bookings.update_one({"_id": b["_id"]}, {"$set": {"expires_at": now() - timedelta(minutes=1)}})
    assert ticketing.expire_booking(db, b["_id"]) is True   # releases tickets and cancels the attached food order
    assert db.food_orders.find_one({"_id": o["_id"]})["status"] == "cancelled"
    try:
        ticketing.process_food_payment(db, b["user_id"], o["_id"], "upi"); assert False
    except TicketError as e:
        assert e.status == 409


# ---------------------------------------------------------------- confirmation: pickup, QR, receipt
def test_order_detail_has_pickup_details_and_a_qr_only_when_paid(db):
    b = confirmed_booking(db)
    o = place_order(db, [L(db, "Samosa", 2)], booking_id=b["_id"])
    unpaid = food_service.food_order_detail(db, o, with_qr=True)
    assert "qr_image" not in unpaid and unpaid["receipt_available"] is False and unpaid["pickup_code"] is None
    ticketing.process_food_payment(db, b["user_id"], o["_id"], "upi")
    paid = food_service.food_order_detail(db, db.food_orders.find_one({"_id": o["_id"]}), with_qr=True)
    st = db.stadiums.find_one({"_id": db.matches.find_one({"_id": b["match_id"]})["stadium_id"]})
    assert paid["pickup"]["stadium"] == st["name"] and paid["pickup"]["booking_reference"] == b["reference"]
    assert paid["pickup"]["match"] and paid["pickup"]["timing"] and paid["booking_reference"] == b["reference"]
    assert paid["qr_payload"] == f'BMMF:{o["_id"]}:{paid["pickup_code"]}' and paid["qr_image"].startswith("data:image/svg+xml;base64,")
    assert paid["receipt_available"] is True and paid["order_number"] == f'FO{o["_id"]:06d}'
    for secret in (settings.SECRET_KEY, settings.TICKET_SIGNING_KEY):
        assert secret not in paid["qr_payload"]


def test_standalone_order_pickup_has_no_stadium_but_still_instructions(db):
    o = place_order(db, [L(db, "Tea", 1)])
    ticketing.process_food_payment(db, o["user_id"], o["_id"], "upi")
    d = food_service.food_order_detail(db, db.food_orders.find_one({"_id": o["_id"]}), with_qr=True)
    assert d["pickup"]["stadium"] is None and "food counter" in d["pickup"]["instructions"] and d["pickup_code"]


def test_pickup_codes_are_stable_unique_and_unambiguous(db):
    codes = {services.pickup_code(i) for i in range(1, 300)}
    assert len(codes) == 299 and all(len(c) == 6 and not set(c) & set("01OI") for c in codes)
    before = services.pickup_code(7)
    assert before == services.pickup_code(7)
    with patched(settings, TICKET_SIGNING_KEY="another-key-entirely"):
        assert services.pickup_code(7) != before   # depends on the server secret, so it cannot be computed by a customer


def test_receipt_pdf_is_generated_for_a_paid_order(db):
    from app.food_receipt import build_receipt_pdf
    o = place_order(db, [L(db, "Brownie", 2), L(db, "Pizza", 1), L(db, "Tea", 4)])
    ticketing.process_food_payment(db, o["user_id"], o["_id"], "net_banking")
    det = food_service.food_order_detail(db, db.food_orders.find_one({"_id": o["_id"]}), with_qr=True)
    pdf = build_receipt_pdf(det)
    assert pdf.startswith(b"%PDF") and len(pdf) > 1500


def test_cancelled_order_has_no_pickup_or_actions(db):
    o = place_order(db, [L(db, "Tea", 1)])
    db.food_orders.update_one({"_id": o["_id"]}, {"$set": {"status": "cancelled"}})
    d = food_service.food_order_detail(db, db.food_orders.find_one({"_id": o["_id"]}))
    assert d["pickup"] is None and not (d["can_pay"] or d["can_cancel"] or d["pay_with_booking"] or d["receipt_available"])


# ---------------------------------------------------------------- data
def test_seeded_menu_has_descriptions_and_no_duplicate_items(db):
    items = list(db.food_items.find({}))
    assert len(items) == 21 and all((i.get("description") or "").strip() for i in items)
    keys = [(i["category_id"], i["name"].lower()) for i in items]
    assert len(keys) == len(set(keys))


def test_backfill_fills_only_missing_descriptions_and_is_idempotent(db):
    from app import backfill_food
    brownie, tea = db.food_items.find_one({"name": "Brownie"}), db.food_items.find_one({"name": "Tea"})
    db.food_items.update_one({"_id": brownie["_id"]}, {"$set": {"description": None}})
    db.food_items.update_one({"_id": tea["_id"]}, {"$set": {"description": "Admin wrote this"}})
    db.food_items.update_one({"name": "Coffee"}, {"$unset": {"description": ""}})            # older documents have no field at all
    price_before = db.food_items.find_one({"name": "Brownie"})["price"]
    assert backfill_food.backfill(db, dry_run=True)["updated"] == 2
    assert not db.food_items.find_one({"name": "Brownie"})["description"]                    # dry run changes nothing
    assert backfill_food.backfill(db)["updated"] == 2
    assert db.food_items.find_one({"name": "Brownie"})["description"].startswith("Warm")
    assert db.food_items.find_one({"name": "Tea"})["description"] == "Admin wrote this"      # never overwritten
    assert db.food_items.find_one({"name": "Brownie"})["price"] == price_before
    assert backfill_food.backfill(db)["updated"] == 0                                         # second run: nothing to do
    assert db.food_items.count_documents({}) == 21
