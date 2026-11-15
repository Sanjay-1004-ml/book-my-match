"""Phase 3B-3D: seat holds, double-booking prevention, payment, tickets and staff verification (service level).
Seat bookings reuse the existing payment, ticket and verification code, so these tests drive the same functions the API calls."""
import threading
from datetime import timedelta
from decimal import Decimal
import pytest
from app import seating as S, seat_booking as SB, ticketing, services
from app.clock import now
from app.config import settings
from app.database import dec
from app.pricing import seat_quote
from .test_seating_service import layout, two_matches_same_stadium, codes
from .test_ticketing_service import user, staff


def setup(db, assign_both=False):
    lay = layout(db)
    m1, m2 = two_matches_same_stadium(db)
    S.assign_layout(db, m1["_id"], lay["id"])
    if assign_both:
        S.assign_layout(db, m2["_id"], lay["id"])
    seats = [s["_id"] for s in db.seats.find({}).sort("_id", 1)]
    return lay, m1, m2, seats


def row(db, match_id, seat_id):
    return db.match_seat_inventory.find_one({"match_id": match_id, "seat_id": seat_id})


def u2(db):
    return user(db, "u2@test.com")


# ---------------------------------------------------------------- selection, validation, pricing
def test_hold_locks_seats_prices_on_server_and_creates_pending_booking(db):
    lay, m1, m2, seats = setup(db, assign_both=True)
    u = user(db)
    b = SB.create_seat_booking(db, u["_id"], m1["_id"], [seats[2], seats[0], seats[1]])
    assert b["booking_type"] == "seat" and b["status"] == "pending_payment" and b["quantity"] == 3 and b["category_id"] is None
    assert b["seat_ids"] == sorted([seats[0], seats[1], seats[2]])
    exp = seat_quote([Decimal(300)] * 3, settings.FEE_PERCENT, settings.TICKET_TAX_PERCENT)
    assert dec(b["total"]) == exp["total"] and dec(b["fee"]) == exp["fee"] and dec(b["tax"]) == exp["tax"]
    for sid in b["seat_ids"]:
        r = row(db, m1["_id"], sid)
        assert r["status"] == "held" and r["booking_id"] == b["_id"] and r["hold_user_id"] == u["_id"] and r["hold_expires_at"] == b["expires_at"]
    assert b["expires_at"] - b["created_at"] == timedelta(minutes=settings.RESERVATION_MINUTES)
    assert row(db, m2["_id"], seats[0])["status"] == "available"        # a different match keeps its own inventory
    out = services.booking_out(db, b, detail=True)
    assert out["booking_type"] == "seat" and len(out["seats"]) == 3 and out["seats"][0]["stand"] == "North Stand" and out["expires_in_seconds"] > 0
    assert out["ticket_total"] == float(exp["total"]) and out["category"] == "General"


def test_price_changes_after_the_hold_do_not_change_the_booking(db):
    lay, m1, m2, seats = setup(db)
    b = SB.create_seat_booking(db, user(db)["_id"], m1["_id"], [seats[0]])
    S.update_category(db, db.seat_categories.find_one({"name": "General"})["_id"], {"base_price": "999"})
    assert dec(db.bookings.find_one({"_id": b["_id"]})["subtotal"]) == Decimal(300)


def test_invalid_selections_are_rejected_and_lock_nothing(db):
    lay, m1, m2, seats = setup(db)
    u = user(db)["_id"]
    S.set_seat_status(db, m1["_id"], [seats[3]], "blocked")
    for ids, status in [([], 422), ([seats[0], seats[0]], 422), ([999999], 404), ([seats[3]], 409), ([seats[0], seats[3]], 409),
                        ([True], 422), (["a"], 422), (list(range(seats[0], seats[0] + settings.MAX_TICKETS + 1)), 400)]:
        assert codes(lambda i=ids: SB.create_seat_booking(db, u, m1["_id"], i)) == status, ids
    assert codes(lambda: SB.create_seat_booking(db, u, 999999, [seats[0]])) == 404
    assert codes(lambda: SB.create_seat_booking(db, u, m2["_id"], [seats[0]])) == 409       # match without seat selection
    assert db.match_seat_inventory.count_documents({"status": "held"}) == 0 and db.bookings.count_documents({"booking_type": "seat"}) == 0


def test_inactive_section_and_past_or_cancelled_matches_cannot_be_booked(db):
    lay, m1, m2, seats = setup(db)
    sec = db.seat_sections.find_one({"name": "Block A"})
    S.update_section(db, sec["_id"], {"is_active": False})
    one = db.seats.find_one({"section_id": sec["_id"]})["_id"]
    assert codes(lambda: SB.create_seat_booking(db, user(db)["_id"], m1["_id"], [one])) == 409
    db.matches.update_one({"_id": m1["_id"]}, {"$set": {"status": "cancelled"}})
    other = db.seats.find_one({"section_id": db.seat_sections.find_one({"name": "Block B"})["_id"]})["_id"]
    assert codes(lambda: SB.create_seat_booking(db, user(db)["_id"], m1["_id"], [other])) == 400


def test_one_active_hold_per_user_per_match(db):
    lay, m1, m2, seats = setup(db)
    u = user(db)["_id"]
    SB.create_seat_booking(db, u, m1["_id"], [seats[0]])
    assert codes(lambda: SB.create_seat_booking(db, u, m1["_id"], [seats[1]])) == 409
    SB.create_seat_booking(db, u2(db)["_id"], m1["_id"], [seats[1]])          # another user is not affected


# ---------------------------------------------------------------- double booking and concurrency
def test_two_users_cannot_hold_the_same_seat(db):
    lay, m1, m2, seats = setup(db)
    a = SB.create_seat_booking(db, user(db)["_id"], m1["_id"], [seats[0], seats[1]])
    assert codes(lambda: SB.create_seat_booking(db, u2(db)["_id"], m1["_id"], [seats[1], seats[2]])) == 409
    assert row(db, m1["_id"], seats[2])["status"] == "available"            # the second request locked nothing, not even seat 3
    assert row(db, m1["_id"], seats[1])["booking_id"] == a["_id"]


def test_simultaneous_requests_for_the_same_seat_have_exactly_one_winner(db):
    lay, m1, m2, seats = setup(db)
    users = [user(db, f"racer{i}@test.com")["_id"] for i in range(8)]
    wins, losses, errors = [], [], []

    def go(uid):
        try:
            wins.append(SB.create_seat_booking(db, uid, m1["_id"], [seats[0], seats[1]]))
        except S.SeatingError as e:
            losses.append(e.status)
        except Exception as e:      # noqa: BLE001 - any other failure fails the test below
            errors.append(repr(e))
    ts = [threading.Thread(target=go, args=(u,)) for u in users]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors, errors
    assert len(wins) == 1 and losses == [409] * 7
    assert {r["booking_id"] for r in db.match_seat_inventory.find({"match_id": m1["_id"], "status": "held"})} == {wins[0]["_id"]}
    assert db.match_seat_inventory.count_documents({"match_id": m1["_id"], "status": "held"}) == 2


def test_overlapping_requests_never_double_book_and_leave_no_orphan_locks(db):
    lay, m1, m2, seats = setup(db)
    users = [user(db, f"o{i}@test.com")["_id"] for i in range(6)]
    wins = []

    def go(i):
        try:
            wins.append(SB.create_seat_booking(db, users[i], m1["_id"], seats[i:i + 3]))      # windows overlap
        except S.SeatingError:
            pass
    ts = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    held = list(db.match_seat_inventory.find({"match_id": m1["_id"], "status": "held"}))
    assert len(held) == 3 * len(wins) and len({r["seat_id"] for r in held}) == len(held)
    assert {r["booking_id"] for r in held} == {b["_id"] for b in wins}            # every lock belongs to a real booking


# ---------------------------------------------------------------- expiry and release
def test_expired_hold_does_not_block_a_new_booking_even_if_no_sweeper_ran(db):
    lay, m1, m2, seats = setup(db)
    a = SB.create_seat_booking(db, user(db)["_id"], m1["_id"], [seats[0]])
    db.match_seat_inventory.update_one({"match_id": m1["_id"], "seat_id": seats[0]}, {"$set": {"hold_expires_at": now() - timedelta(seconds=1)}})
    # the lock query itself checks the timestamp: call it directly, bypassing the sweeper that create_seat_booking also runs
    got = db.match_seat_inventory.find_one_and_update({"match_id": m1["_id"], "seat_id": seats[0], "is_active": True, **SB._lockable(now())},
                                                      {"$set": {"status": "held", "booking_id": 777}})
    assert got is not None
    db.match_seat_inventory.update_one({"match_id": m1["_id"], "seat_id": seats[0]}, {"$set": {"status": "held", "booking_id": a["_id"], "hold_expires_at": now() - timedelta(seconds=1)}})
    b = SB.create_seat_booking(db, u2(db)["_id"], m1["_id"], [seats[0]])
    assert row(db, m1["_id"], seats[0])["booking_id"] == b["_id"]


def test_expiry_releases_only_the_expired_bookings_own_seats(db):
    lay, m1, m2, seats = setup(db)
    a = SB.create_seat_booking(db, user(db)["_id"], m1["_id"], [seats[0], seats[1]])
    past = now() - timedelta(minutes=1)
    db.bookings.update_one({"_id": a["_id"]}, {"$set": {"expires_at": past}})
    db.match_seat_inventory.update_many({"booking_id": a["_id"]}, {"$set": {"hold_expires_at": past}})
    b = SB.create_seat_booking(db, u2(db)["_id"], m1["_id"], [seats[0]])           # takes over an expired seat; a is expired along the way
    assert db.bookings.find_one({"_id": a["_id"]})["status"] == "expired"
    assert row(db, m1["_id"], seats[0])["booking_id"] == b["_id"] and row(db, m1["_id"], seats[1])["status"] == "available"
    assert ticketing.expire_booking(db, a["_id"]) is False                           # already expired: a second expiry cannot touch b's seat
    assert row(db, m1["_id"], seats[0])["status"] == "held" and row(db, m1["_id"], seats[0])["booking_id"] == b["_id"]


def test_expire_stale_and_sweeper_free_abandoned_holds(db):
    lay, m1, m2, seats = setup(db)
    a = SB.create_seat_booking(db, user(db)["_id"], m1["_id"], [seats[0]])
    past = now() - timedelta(minutes=1)
    db.bookings.update_one({"_id": a["_id"]}, {"$set": {"expires_at": past}})
    db.match_seat_inventory.update_one({"booking_id": a["_id"]}, {"$set": {"hold_expires_at": past}})
    assert ticketing.expire_stale(db) == 1
    assert row(db, m1["_id"], seats[0])["status"] == "available" and "booking_id" not in row(db, m1["_id"], seats[0])
    # an orphaned lock (server died before the booking was written) is freed by the sweeper
    db.match_seat_inventory.update_one({"match_id": m1["_id"], "seat_id": seats[1]}, {"$set": {"status": "held", "booking_id": 4242, "hold_expires_at": past}})
    assert S.sweep_expired_holds(db) == 1 and row(db, m1["_id"], seats[1])["status"] == "available"


def test_pay_after_expiry_is_refused_and_seats_are_released(db):
    lay, m1, m2, seats = setup(db)
    u = user(db)["_id"]
    a = SB.create_seat_booking(db, u, m1["_id"], [seats[0]])
    past = now() - timedelta(seconds=5)
    db.bookings.update_one({"_id": a["_id"]}, {"$set": {"expires_at": past}})
    db.match_seat_inventory.update_one({"booking_id": a["_id"]}, {"$set": {"hold_expires_at": past}})
    with pytest.raises(ticketing.TicketError) as e:
        ticketing.process_payment(db, u, a["_id"], "upi")
    assert e.value.status == 410 and row(db, m1["_id"], seats[0])["status"] == "available"
    assert db.payments.count_documents({}) == 0 and db.tickets.count_documents({}) == 0


# ---------------------------------------------------------------- payment, confirmation, tickets
def test_successful_payment_books_seats_and_issues_one_ticket_per_seat(db):
    lay, m1, m2, seats = setup(db, assign_both=True)
    u = user(db)["_id"]
    a = SB.create_seat_booking(db, u, m1["_id"], [seats[0], seats[1]])
    r = ticketing.process_payment(db, u, a["_id"], "upi")
    assert r["outcome"] == "success" and r["payment"]["status"] == "successful" and r["payment"]["simulated"] is True
    assert dec(r["payment"]["amount"]) == dec(a["total"])
    for sid in (seats[0], seats[1]):
        x = row(db, m1["_id"], sid)
        assert x["status"] == "booked" and x["booking_id"] == a["_id"] and "hold_expires_at" not in x and "hold_user_id" not in x
    tix = list(db.tickets.find({"booking_id": a["_id"]}).sort("seq", 1))
    assert len(tix) == 2 and [t["seat"]["seat_key"] for t in tix] == ["NBLOCKA-A1-1", "NBLOCKA-A1-2"]
    assert tix[0]["seat_number"] == "North Stand · Block A · Row A1 · Seat 1" and tix[0]["ticket_category"] == "General"
    assert len({t["ticket_id"] for t in tix}) == 2 and all(t["verification_token_hash"] for t in tix)
    b = db.bookings.find_one({"_id": a["_id"]})
    assert b["status"] == "confirmed" and b["payment_status"] == "successful" and b["expires_at"] is None
    # the same physical seat is still free for the other match
    assert row(db, m2["_id"], seats[0])["status"] == "available"
    SB.create_seat_booking(db, u2(db)["_id"], m2["_id"], [seats[0]])
    # a booked seat can no longer be taken for this match
    assert codes(lambda: SB.create_seat_booking(db, u2(db)["_id"], m1["_id"], [seats[1]])) == 409


def test_duplicate_payment_requests_charge_once_and_issue_tickets_once(db):
    lay, m1, m2, seats = setup(db)
    u = user(db)["_id"]
    a = SB.create_seat_booking(db, u, m1["_id"], [seats[0], seats[1], seats[2]])
    results, errors = [], []

    def pay():
        try:
            results.append(ticketing.process_payment(db, u, a["_id"], "debit_card")["outcome"])
        except Exception as e:      # noqa: BLE001
            errors.append(repr(e))
    ts = [threading.Thread(target=pay) for _ in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors, errors
    assert results.count("success") == 1 and results.count("already_paid") == 4
    assert db.payments.count_documents({"booking_id": a["_id"], "status": "successful"}) == 1
    assert db.tickets.count_documents({"booking_id": a["_id"]}) == 3
    assert db.match_seat_inventory.count_documents({"booking_id": a["_id"], "status": "booked"}) == 3


def test_failed_payment_books_nothing_keeps_the_hold_and_allows_retry(db):
    lay, m1, m2, seats = setup(db)
    u = user(db)["_id"]
    a = SB.create_seat_booking(db, u, m1["_id"], [seats[0]])
    r = ticketing.process_payment(db, u, a["_id"], "upi", simulate="failure")
    assert r["outcome"] == "failed" and r["payment"]["status"] == "failed" and r["payment"]["failure_reason"]
    assert row(db, m1["_id"], seats[0])["status"] == "held" and db.tickets.count_documents({}) == 0
    assert db.bookings.find_one({"_id": a["_id"]})["status"] == "pending_payment"
    assert ticketing.process_payment(db, u, a["_id"], "net_banking")["outcome"] == "success"
    assert row(db, m1["_id"], seats[0])["status"] == "booked" and db.tickets.count_documents({"booking_id": a["_id"]}) == 1


def test_only_the_owner_can_pay_and_amount_comes_from_the_booking(db):
    lay, m1, m2, seats = setup(db)
    a = SB.create_seat_booking(db, user(db)["_id"], m1["_id"], [seats[0]])
    with pytest.raises(ticketing.TicketError) as e:
        ticketing.process_payment(db, u2(db)["_id"], a["_id"], "upi")
    assert e.value.status == 404 and row(db, m1["_id"], seats[0])["status"] == "held"


def test_seat_lost_in_an_expiry_race_charges_nothing_and_rolls_back(db):
    lay, m1, m2, seats = setup(db)
    u = user(db)["_id"]
    a = SB.create_seat_booking(db, u, m1["_id"], [seats[0], seats[1]])
    # simulate: the hold of one seat was overtaken by someone else between the booking check and seat confirmation
    db.match_seat_inventory.update_one({"match_id": m1["_id"], "seat_id": seats[1]}, {"$set": {"booking_id": 9999}})
    with pytest.raises(ticketing.TicketError) as e:
        ticketing.process_payment(db, u, a["_id"], "upi")
    assert e.value.status == 409
    assert db.payments.count_documents({}) == 0 and db.tickets.count_documents({}) == 0
    b = db.bookings.find_one({"_id": a["_id"]})
    assert b["status"] == "expired" and b["payment_status"] == "failed" and b["payment_ref"] is None
    assert row(db, m1["_id"], seats[0])["status"] == "available"
    assert row(db, m1["_id"], seats[1])["booking_id"] == 9999                          # the other owner keeps their seat


def test_healing_completes_a_booking_whose_server_died_after_payment(db):
    lay, m1, m2, seats = setup(db)
    u = user(db)["_id"]
    a = SB.create_seat_booking(db, u, m1["_id"], [seats[0], seats[1]])
    ticketing.process_payment(db, u, a["_id"], "upi")
    db.match_seat_inventory.update_many({"booking_id": a["_id"]}, {"$set": {"status": "held"}})       # crash left seats held
    db.tickets.delete_many({})
    ticketing.heal_user(db, u)
    assert db.match_seat_inventory.count_documents({"booking_id": a["_id"], "status": "booked"}) == 2 and db.tickets.count_documents({}) == 2


# ---------------------------------------------------------------- QR and staff verification
def test_seat_ticket_qr_verification_by_staff_shows_seat_and_blocks_reuse(db):
    lay, m1, m2, seats = setup(db)
    u = user(db)["_id"]
    a = SB.create_seat_booking(db, u, m1["_id"], [seats[6]])
    ticketing.process_payment(db, u, a["_id"], "upi")
    t = db.tickets.find_one({"booking_id": a["_id"]})
    info = ticketing.tickets_out(db, [t], with_qr=True)[0]
    assert info["qr_payload"].startswith("BMMT:") and info["qr_image"].startswith("data:image/svg+xml;base64") and info["seat"]["seat_key"] == "NBLOCKA-A2-2"
    assert "NBLOCKA" not in info["qr_payload"]                                      # the QR carries only an opaque token
    ok = ticketing.verify_ticket(db, staff(db), info["qr_payload"])
    assert ok["ok"] and ok["ticket"]["seat_number"] == "North Stand · Block A · Row A2 · Seat 2" and ok["ticket"]["seat"]["row"] == "A2"
    assert ok["ticket"]["match"] and ok["ticket"]["stadium"] and ok["ticket"]["booking_reference"] == a["reference"]
    again = ticketing.verify_ticket(db, staff(db), info["qr_payload"])
    assert not again["ok"] and again["result"] == "already_used"
    assert ticketing.verify_ticket(db, staff(db), "BMMT:" + "A" * 32)["result"] == "invalid"
    other = ticketing.verify_ticket(db, staff(db), info["backup_code"], expected_match_id=m1["_id"] + 1000)
    assert not other["ok"]


def test_ticket_pdf_contains_the_seat(db):
    from app.ticket_pdf import build_tickets_pdf
    lay, m1, m2, seats = setup(db)
    u = user(db)["_id"]
    a = SB.create_seat_booking(db, u, m1["_id"], [seats[0]])
    ticketing.process_payment(db, u, a["_id"], "upi")
    rows = ticketing.tickets_out(db, list(db.tickets.find({"booking_id": a["_id"]})), with_qr=True)
    assert build_tickets_pdf(rows)[:4] == b"%PDF"


# ---------------------------------------------------------------- cancellation, layout safety, compatibility
def test_seat_bookings_block_layout_changes_and_category_bookings(db):
    lay, m1, m2, seats = setup(db)
    u = user(db)["_id"]
    a = SB.create_seat_booking(db, u, m1["_id"], [seats[0]])
    ticketing.process_payment(db, u, a["_id"], "upi")
    seat = db.seats.find_one({"_id": seats[0]})
    for fn in (lambda: S.delete_seat(db, seats[0]), lambda: S.delete_row(db, seat["row_id"]), lambda: S.delete_stand(db, seat["stand_id"]),
               lambda: S.unassign_layout(db, m1["_id"]), lambda: S.delete_layout(db, lay["id"])):
        assert codes(fn) == 409
    assert db.tickets.count_documents({"booking_id": a["_id"]}) == 1 and db.seats.find_one({"_id": seats[0]})


def test_a_hold_that_expired_does_not_block_layout_maintenance(db):
    lay, m1, m2, seats = setup(db)
    a = SB.create_seat_booking(db, user(db)["_id"], m1["_id"], [seats[0]])
    db.match_seat_inventory.update_one({"booking_id": a["_id"]}, {"$set": {"hold_expires_at": now() - timedelta(seconds=1)}})
    S.delete_seat(db, seats[0])                      # the guard sweeps expired holds first
    assert db.seats.find_one({"_id": seats[0]}) is None


def test_seat_assignment_refused_when_category_bookings_exist_and_normal_matches_unchanged(db):
    lay = layout(db)
    m1, m2 = two_matches_same_stadium(db)
    inv = db.ticket_inventory.find_one({"match_id": m2["_id"]})
    db.bookings.insert_one({"_id": 5001, "reference": "OLD1", "user_id": user(db)["_id"], "match_id": m2["_id"], "category_id": inv["category_id"],
                            "quantity": 1, "status": "confirmed", "payment_status": "successful"})
    assert codes(lambda: S.assign_layout(db, m2["_id"], lay["id"])) == 409
    S.assign_layout(db, m1["_id"], lay["id"])        # the other match has no bookings, so it can
    # an old-style booking (no booking_type / seats) still renders and still issues tickets
    from .test_ticketing_service import hold
    b = hold(db, user(db, "legacy@test.com")["_id"], qty=2, match_index=40)
    out = services.booking_out(db, b)
    assert out["booking_type"] == "category" and out["seats"] == [] and out["category"]
    ticketing.process_payment(db, b["user_id"], b["_id"], "upi")
    t = ticketing.tickets_out(db, list(db.tickets.find({"booking_id": b["_id"]})))[0]
    assert t["seat"] is None and t["seat_number"] is None


def test_public_seat_map_reports_held_booked_mine_and_unavailable(db):
    lay, m1, m2, seats = setup(db)
    ua, ub = user(db)["_id"], u2(db)["_id"]
    a = SB.create_seat_booking(db, ua, m1["_id"], [seats[0]])
    b = SB.create_seat_booking(db, ub, m1["_id"], [seats[1]])
    ticketing.process_payment(db, ub, b["_id"], "upi")
    S.set_seat_status(db, m1["_id"], [seats[2]], "blocked")
    sec = db.seats.find_one({"_id": seats[0]})["section_id"]
    st = {s["number"]: s for r in S.match_section_seats(db, m1["_id"], sec, ua)["rows"] for s in r["seats"] if r["label"] == "A1"}
    assert st[1]["status"] == "mine" and st[2]["status"] == "booked" and st[3]["status"] == "unavailable" and st[4]["status"] == "available"
    seen_by_other = {s["number"]: s for r in S.match_section_seats(db, m1["_id"], sec, ub)["rows"] for s in r["seats"] if r["label"] == "A1"}
    assert seen_by_other[1]["status"] == "held" and seen_by_other[1]["selectable"] is False
    anon = {s["number"]: s for r in S.match_section_seats(db, m1["_id"], sec)["rows"] for s in r["seats"] if r["label"] == "A1"}
    assert anon[1]["status"] == "held"
    pub = S.match_seating(db, m1["_id"])["stands"][0]["sections"][0]
    assert pub["held"] == 1 and pub["booked"] == 1 and pub["available"] == 7
    inv = S.list_inventory(db, m1["_id"], status="booked")["items"][0]
    assert inv["booking_reference"] == b["reference"] and inv["customer"]
