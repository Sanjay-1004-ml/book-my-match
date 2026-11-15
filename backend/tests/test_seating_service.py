"""Phase 3A: seating service tests (layouts, categories, stands, sections, rows, seats, match inventory, server-side pricing).
Run with pytest like the rest of the suite (uses the in-memory mongomock database from conftest.py)."""
from decimal import Decimal
import pytest
from pymongo.errors import DuplicateKeyError
from app import seating as S, services
from app.config import settings
from app.pricing import seat_quote, ticket_quote
from app.seating_schema import VALIDATORS, ensure_seating_validators


def spec(**over):
    s = {"categories": [{"name": "General", "base_price": "300"}, {"name": "Premium", "base_price": "1000.50", "color": "#f59e0b"},
                        {"name": "VIP", "base_price": "1500"}],
         "stands": [{"name": "North Stand", "code": "N", "sections": [
                        {"name": "Block A", "category": "General", "rows": [{"label": "A1", "seat_count": 5}, {"label": "A2", "seat_count": 5}]},
                        {"name": "Block B", "category": "General", "rows": [{"label": "B1", "seat_count": 4}]}]},
                    {"name": "VIP Section", "code": "V", "sections": [
                        {"name": "VIP Lounge", "category": "VIP", "rows": [{"label": "V1", "seat_count": 3}]}]}]}
    s.update(over)
    return s


def two_matches_same_stadium(db):
    seen = {}
    for m in db.matches.find({"status": "scheduled"}).sort("_id", 1):
        seen.setdefault(m["stadium_id"], []).append(m)
        if len(seen[m["stadium_id"]]) == 2:
            return seen[m["stadium_id"]]
    raise AssertionError("seed data has no stadium with two scheduled matches")


def layout(db, stadium_id=None, s=None):
    if stadium_id is None:
        stadium_id = two_matches_same_stadium(db)[0]["stadium_id"]
    return S.create_layout(db, stadium_id, "Main layout", s or spec())


def codes(fn):
    with pytest.raises(S.SeatingError) as e:
        fn()
    return e.value.status


# ---------------------------------------------------------------- layouts, stands, sections, rows, seats
def test_create_nested_layout_and_totals(db):
    lay = layout(db)
    t = S.layout_tree(db, lay["id"])
    assert t["totals"]["stands"] == 2 and t["totals"]["sections"] == 3 and t["totals"]["rows"] == 4 and t["totals"]["seats"] == 17
    assert t["totals"]["active_seats"] == 17 and t["totals"]["categories"] == 3
    north = t["stands"][0]
    assert north["name"] == "North Stand" and north["seat_count"] == 14 and [s["name"] for s in north["sections"]] == ["Block A", "Block B"]
    seats = list(db.seats.find({"layout_id": lay["id"]}))
    keys = {s["seat_key"] for s in seats}
    assert len(keys) == 17 and "NBLOCKA-A1-1" in keys
    s = next(x for x in seats if x["seat_key"] == "NBLOCKA-A1-5")
    for field in ("seat_number", "row_label", "section_id", "category_id", "stadium_id", "is_active"):
        assert field in s
    prem = next(c for c in t["categories"] if c["name"] == "Premium")
    assert prem["base_price"] == 1000.5


def test_one_layout_per_stadium_and_unknown_stadium(db):
    lay = layout(db)
    assert codes(lambda: S.create_layout(db, lay["stadium_id"], "Again", spec())) == 409
    assert codes(lambda: S.create_layout(db, 999999, "X layout", spec())) == 404
    assert db.seat_layouts.count_documents({"stadium_id": lay["stadium_id"]}) == 1


def test_invalid_spec_is_rejected_before_anything_is_written(db):
    sid = two_matches_same_stadium(db)[0]["stadium_id"]
    bad = spec()
    bad["stands"][0]["sections"][0]["rows"].append({"label": "a1", "seat_count": 2})   # same row label (case-insensitive)
    assert codes(lambda: S.create_layout(db, sid, "Bad layout", bad)) == 422
    unknown = spec()
    unknown["stands"][0]["sections"][0]["category"] = "Nope"
    assert codes(lambda: S.create_layout(db, sid, "Bad layout", unknown)) == 422
    too_big = spec()
    too_big["stands"][0]["sections"][0]["rows"][0]["seat_count"] = settings.SEATING_MAX_SEATS_PER_ROW + 1
    assert codes(lambda: S.create_layout(db, sid, "Bad layout", too_big)) == 422
    clash = spec()
    clash["stands"].append({"name": "North  stand", "code": "N2", "sections": []})   # duplicate stand name (whitespace/case)
    assert codes(lambda: S.create_layout(db, sid, "Bad layout", clash)) == 422
    for coll in ("seat_layouts", "seat_categories", "seat_stands", "seat_sections", "seat_rows", "seats"):
        assert db[coll].count_documents({}) == 0, coll


def test_failure_midway_rolls_the_layout_back(db):
    sid = two_matches_same_stadium(db)[0]["stadium_id"]
    real = S._create_row
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("boom")
        return real(*a, **k)
    S._create_row = flaky
    try:
        with pytest.raises(RuntimeError):
            S.create_layout(db, sid, "Flaky layout", spec())
    finally:
        S._create_row = real
    for coll in ("seat_layouts", "seat_categories", "seat_stands", "seat_sections", "seat_rows", "seats"):
        assert db[coll].count_documents({}) == 0, coll


def test_stands_add_update_duplicate_and_deactivate(db):
    lay = layout(db)
    st = S.add_stand(db, lay["id"], {"name": "East Stand"})
    assert st["code"] == "EASTSTAND"
    assert codes(lambda: S.add_stand(db, lay["id"], {"name": "east stand", "code": "E2"})) == 409
    assert S.update_stand(db, st["id"], {"name": "East Upper"})["name"] == "East Upper"
    north = db.seat_stands.find_one({"code": "N"})
    S.update_stand(db, north["_id"], {"is_active": False})
    assert all(not s["is_active"] for s in db.seat_sections.find({"stand_id": north["_id"]}))
    S.update_stand(db, north["_id"], {"is_active": True})
    assert all(s["is_active"] for s in db.seat_sections.find({"stand_id": north["_id"]}))


def test_sections_rows_and_row_capacity(db):
    lay = layout(db)
    cat = db.seat_categories.find_one({"name": "Premium"})["_id"]
    st = db.seat_stands.find_one({"code": "N"})["_id"]
    sec = S.add_section(db, st, {"name": "Block C", "category_id": cat})
    assert sec["code"] == "NBLOCKC" and codes(lambda: S.add_section(db, st, {"name": "block c", "category_id": cat})) == 409
    assert codes(lambda: S.add_section(db, st, {"name": "Block D", "category_id": 999999})) == 422
    row = S.add_row(db, sec["id"], {"label": "c1", "seat_count": 20})
    assert row["label"] == "C1" and row["seat_count"] == 20
    assert codes(lambda: S.add_row(db, sec["id"], {"label": "C1", "seat_count": 2})) == 409
    assert codes(lambda: S.add_row(db, sec["id"], {"label": "C2", "seat_count": 0})) == 422
    assert S.update_row(db, row["id"], {"seat_count": 25})["seat_count"] == 25
    nums = sorted(s["seat_number"] for s in db.seats.find({"row_id": row["id"]}))
    assert nums == list(range(1, 26))
    assert S.update_row(db, row["id"], {"seat_count": 10})["seat_count"] == 10
    assert max(s["seat_number"] for s in db.seats.find({"row_id": row["id"]})) == 10
    S.update_row(db, row["id"], {"label": "C9"})
    assert all(s["seat_key"].startswith("NBLOCKC-C9-") for s in db.seats.find({"row_id": row["id"]}))
    assert S.layout_tree(db, lay["id"])["totals"]["seats"] == 17 + 10


def test_individual_seats_and_duplicate_prevention(db):
    lay = layout(db)
    row = db.seat_rows.find_one({"label": "A1"})
    assert codes(lambda: S.add_seat(db, row["_id"], {"seat_number": 3})) == 409   # already exists from the row
    s = S.add_seat(db, row["_id"], {"seat_number": 6})
    assert s["seat_key"] == "NBLOCKA-A1-6" and s["base_price"] == 300.0
    assert codes(lambda: S.add_seat(db, row["_id"], {"seat_number": 6})) == 409
    assert codes(lambda: S.add_seat(db, row["_id"], {"seat_number": 0})) == 422
    # the unique indexes are the last line of defence even if the service check were bypassed
    dup = dict(db.seats.find_one({"_id": s["id"]}), _id=999999)
    with pytest.raises(DuplicateKeyError):
        db.seats.insert_one(dup)
    other = dict(db.seats.find_one({"_id": s["id"]}), _id=999998, seat_number=77)   # same seat_key, different number
    with pytest.raises(DuplicateKeyError):
        db.seats.insert_one(other)
    assert S.update_seat(db, s["id"], {"price_override": "450"})["base_price"] == 450.0
    assert S.update_seat(db, s["id"], {"price_override": None})["base_price"] == 300.0
    S.delete_seat(db, s["id"])
    assert db.seats.find_one({"_id": s["id"]}) is None


def test_seat_keys_are_unique_across_the_whole_stadium(db):
    lay = layout(db)
    keys = [s["seat_key"] for s in db.seats.find({"stadium_id": lay["stadium_id"]})]
    assert len(keys) == len(set(keys)) == 17


# ---------------------------------------------------------------- categories and prices
def test_category_price_validation_and_update(db):
    lay = layout(db)
    for bad in (-1, "abc", "1.234", 1000001, None, float("nan")):
        assert codes(lambda b=bad: S.create_category(db, lay["id"], {"name": "Bad", "base_price": b})) == 422, bad
    assert codes(lambda: S.create_category(db, lay["id"], {"name": "general", "base_price": 10})) == 409
    assert codes(lambda: S.create_category(db, lay["id"], {"name": "Odd", "base_price": 10, "color": "red"})) == 422
    c = S.create_category(db, lay["id"], {"name": "East Stand", "base_price": "500"})
    assert S.update_category(db, c["id"], {"base_price": "550"})["base_price"] == 550.0
    assert codes(lambda: S.update_category(db, c["id"], {"base_price": "-5"})) == 422
    assert codes(lambda: S.update_category(db, c["id"], {"name": "VIP"})) == 409
    prem = db.seat_categories.find_one({"name": "General"})["_id"]
    assert codes(lambda: S.delete_category(db, prem)) == 409     # in use
    S.delete_category(db, c["id"])


def test_changing_a_section_category_recategorises_seats_and_inventory(db):
    lay = layout(db)
    m = two_matches_same_stadium(db)[0]
    S.assign_layout(db, m["_id"], lay["id"])
    vip = db.seat_categories.find_one({"name": "VIP"})["_id"]
    sec = db.seat_sections.find_one({"name": "Block B"})
    S.update_section(db, sec["_id"], {"category_id": vip})
    assert {s["category_id"] for s in db.seats.find({"section_id": sec["_id"]})} == {vip}
    assert {r["category_id"] for r in db.match_seat_inventory.find({"section_id": sec["_id"]})} == {vip}
    assert codes(lambda: S.update_section(db, sec["_id"], {"category_id": 999999})) == 422


# ---------------------------------------------------------------- match assignment, per-match inventory
def test_assign_layout_creates_separate_inventory_per_match(db):
    lay = layout(db)
    m1, m2 = two_matches_same_stadium(db)
    s1 = S.assign_layout(db, m1["_id"], lay["id"])
    assert s1["seating_enabled"] and s1["total"] == 17 and s1["available"] == 17 and s1["sellable_available"] == 17
    S.assign_layout(db, m2["_id"], lay["id"])
    seat = db.seats.find_one({})
    assert S.set_seat_status(db, m1["_id"], [seat["_id"]], "blocked")["changed"] == 1
    assert S.inventory_summary(db, m1["_id"])["blocked"] == 1 and S.inventory_summary(db, m1["_id"])["available"] == 16
    assert S.inventory_summary(db, m2["_id"])["blocked"] == 0 and S.inventory_summary(db, m2["_id"])["available"] == 17
    assert db.match_seat_inventory.count_documents({"seat_id": seat["_id"]}) == 2
    assert db.matches.find_one({"_id": m1["_id"]})["seat_inventory_status"] == "ready"


def test_assign_is_idempotent_and_resumes_a_partial_build(db):
    lay = layout(db)
    m = two_matches_same_stadium(db)[0]
    S.assign_layout(db, m["_id"], lay["id"])
    first = db.seats.find_one({})["_id"]
    S.set_seat_status(db, m["_id"], [first], "blocked")
    db.match_seat_inventory.delete_many({"match_id": m["_id"], "seat_id": {"$in": [s["_id"] for s in list(db.seats.find({}))[10:]]}})   # simulate a crash
    S.assign_layout(db, m["_id"], lay["id"])
    s = S.inventory_summary(db, m["_id"])
    assert s["total"] == 17 and s["blocked"] == 1            # nothing duplicated, the blocked seat kept its status


def test_assign_rejections(db):
    lay = layout(db)
    m = two_matches_same_stadium(db)[0]
    other = next(x for x in db.matches.find({"status": "scheduled"}) if x["stadium_id"] != m["stadium_id"])
    assert codes(lambda: S.assign_layout(db, other["_id"], lay["id"])) == 409          # wrong stadium
    assert codes(lambda: S.assign_layout(db, 999999, lay["id"])) == 404
    assert codes(lambda: S.assign_layout(db, m["_id"], 999999)) == 404
    db.matches.update_one({"_id": m["_id"]}, {"$set": {"status": "cancelled"}})
    assert codes(lambda: S.assign_layout(db, m["_id"], lay["id"])) == 409
    db.matches.update_one({"_id": m["_id"]}, {"$set": {"status": "scheduled"}})
    S.update_layout(db, lay["id"], {"is_active": False})
    assert codes(lambda: S.assign_layout(db, m["_id"], lay["id"])) == 409
    empty = S.create_layout(db, other["stadium_id"], "Empty layout")
    assert codes(lambda: S.assign_layout(db, other["_id"], empty["id"])) == 409


def test_seats_added_later_appear_in_assigned_matches(db):
    lay = layout(db)
    m1, m2 = two_matches_same_stadium(db)
    S.assign_layout(db, m1["_id"], lay["id"])          # m2 is not assigned
    row = db.seat_rows.find_one({"label": "A1"})
    S.add_seat(db, row["_id"], {"seat_number": 6})
    S.update_row(db, db.seat_rows.find_one({"label": "B1"})["_id"], {"seat_count": 6})
    assert S.inventory_summary(db, m1["_id"])["total"] == 17 + 1 + 2
    assert db.match_seat_inventory.count_documents({"match_id": m2["_id"]}) == 0


def test_block_unblock_never_touches_held_or_booked(db):
    lay = layout(db)
    m = two_matches_same_stadium(db)[0]
    S.assign_layout(db, m["_id"], lay["id"])
    ids = [s["_id"] for s in db.seats.find({}).sort("_id", 1)][:4]
    db.match_seat_inventory.update_one({"match_id": m["_id"], "seat_id": ids[0]}, {"$set": {"status": "held"}})
    db.match_seat_inventory.update_one({"match_id": m["_id"], "seat_id": ids[1]}, {"$set": {"status": "booked"}})
    r = S.set_seat_status(db, m["_id"], ids, "blocked")
    assert r == {"requested": 4, "changed": 2, "skipped": 2}
    st = {x["seat_id"]: x["status"] for x in db.match_seat_inventory.find({"match_id": m["_id"], "seat_id": {"$in": ids}})}
    assert st[ids[0]] == "held" and st[ids[1]] == "booked" and st[ids[2]] == "blocked"
    assert S.set_seat_status(db, m["_id"], ids, "available")["changed"] == 2
    assert codes(lambda: S.set_seat_status(db, m["_id"], ids, "booked")) == 422
    assert codes(lambda: S.set_seat_status(db, m["_id"], [], "blocked")) == 422


def test_destructive_operations_are_refused_for_held_or_booked_seats(db):
    lay = layout(db)
    m = two_matches_same_stadium(db)[0]
    S.assign_layout(db, m["_id"], lay["id"])
    row = db.seat_rows.find_one({"label": "A1"})
    sec = db.seat_sections.find_one({"_id": row["section_id"]})
    seat = db.seats.find_one({"row_id": row["_id"], "seat_number": 2})
    db.match_seat_inventory.update_one({"match_id": m["_id"], "seat_id": seat["_id"]}, {"$set": {"status": "booked"}})
    for fn in (lambda: S.delete_seat(db, seat["_id"]), lambda: S.delete_row(db, row["_id"]), lambda: S.delete_section(db, sec["_id"]),
               lambda: S.delete_stand(db, sec["stand_id"]), lambda: S.unassign_layout(db, m["_id"]),
               lambda: S.update_row(db, row["_id"], {"label": "ZZ"}), lambda: S.update_row(db, row["_id"], {"seat_count": 1})):
        assert codes(fn) == 409
    assert db.seats.count_documents({"row_id": row["_id"]}) == 5 and db.match_seat_inventory.count_documents({"match_id": m["_id"]}) == 17
    assert codes(lambda: S.delete_layout(db, lay["id"])) == 409
    # a different layout cannot be swapped in while seats are booked
    db.match_seat_inventory.update_one({"match_id": m["_id"], "seat_id": seat["_id"]}, {"$set": {"status": "available"}})
    S.delete_row(db, row["_id"])                 # now allowed, and its inventory rows go with it
    assert db.match_seat_inventory.count_documents({"match_id": m["_id"]}) == 12


def test_unassign_and_delete_layout(db):
    lay = layout(db)
    m = two_matches_same_stadium(db)[0]
    S.assign_layout(db, m["_id"], lay["id"])
    assert codes(lambda: S.delete_layout(db, lay["id"])) == 409
    assert codes(lambda: S.update_layout(db, lay["id"], {"is_active": False})) == 409
    S.unassign_layout(db, m["_id"])
    doc = db.matches.find_one({"_id": m["_id"]})
    assert "seat_layout_id" not in doc and "seat_inventory_status" not in doc and db.match_seat_inventory.count_documents({}) == 0
    S.delete_layout(db, lay["id"])
    assert db.seats.count_documents({}) == 0 and db.seat_layouts.count_documents({}) == 0


def test_section_deactivation_stops_sales_but_keeps_seat_state(db):
    lay = layout(db)
    m = two_matches_same_stadium(db)[0]
    S.assign_layout(db, m["_id"], lay["id"])
    sec = db.seat_sections.find_one({"name": "Block A"})
    one = db.seats.find_one({"section_id": sec["_id"], "seat_number": 1})
    S.update_seat(db, one["_id"], {"is_active": False})
    S.update_section(db, sec["_id"], {"is_active": False})
    summ = S.inventory_summary(db, m["_id"])
    assert summ["sellable_available"] == 17 - 10 and summ["inactive"] == 10
    assert codes(lambda: S.match_section_seats(db, m["_id"], sec["_id"])) == 404
    pub = S.match_seating(db, m["_id"])
    assert "Block A" not in [s["name"] for st in pub["stands"] for s in st["sections"]]
    S.update_section(db, sec["_id"], {"is_active": True})
    summ = S.inventory_summary(db, m["_id"])
    assert summ["inactive"] == 1 and summ["sellable_available"] == 16     # the individually disabled seat stays off
    rows = S.match_section_seats(db, m["_id"], sec["_id"])["rows"]
    first = next(s for r in rows for s in r["seats"] if s["seat_id"] == one["_id"])
    assert first["status"] == "unavailable" and first["selectable"] is False


# ---------------------------------------------------------------- reads and pricing
def test_public_views_and_matches_without_layout_keep_working(db):
    lay = layout(db)
    m1, m2 = two_matches_same_stadium(db)
    assert S.match_seating(db, m1["_id"]) == {"match_id": m1["_id"], "seating_enabled": False}     # no layout yet: not an error
    assert S.inventory_summary(db, m1["_id"]) == {"match_id": m1["_id"], "seating_enabled": False}
    assert codes(lambda: S.match_seating(db, 999999)) == 404
    S.assign_layout(db, m1["_id"], lay["id"])
    pub = S.match_seating(db, m1["_id"])
    assert pub["seating_enabled"] and [st["name"] for st in pub["stands"]] == ["North Stand", "VIP Section"]
    assert pub["stands"][0]["available"] == 14 and pub["stands"][0]["sections"][0]["available"] == 10
    sec = pub["stands"][0]["sections"][0]
    detail = S.match_section_seats(db, m1["_id"], sec["id"])
    assert [r["label"] for r in detail["rows"]] == ["A1", "A2"] and len(detail["rows"][0]["seats"]) == 5
    assert detail["rows"][0]["seats"][0]["selectable"] and detail["rows"][0]["seats"][0]["price"] == 300.0
    st = S.stadium_seating(db, lay["stadium_id"])
    assert st["seating_enabled"] and st["total_seats"] == 17
    other = next(x for x in db.stadiums.find({}) if x["_id"] != lay["stadium_id"])
    assert S.stadium_seating(db, other["_id"]) == {"stadium_id": other["_id"], "seating_enabled": False}


def test_quote_is_priced_by_the_server_and_matches_existing_fee_and_tax_rules(db):
    lay = layout(db)
    m = two_matches_same_stadium(db)[0]
    S.assign_layout(db, m["_id"], lay["id"])
    a1 = [s["_id"] for s in db.seats.find({"row_label": "A1"}).sort("seat_number", 1)]
    vip = db.seats.find_one({"row_label": "V1"})["_id"]
    q = S.quote_seats(db, m["_id"], [a1[0], a1[1], vip])
    assert q["subtotal"] == 2100.0 and q["quantity"] == 3
    exp = seat_quote([Decimal(300), Decimal(300), Decimal(1500)], settings.FEE_PERCENT, settings.TICKET_TAX_PERCENT)
    assert Decimal(str(q["total"])) == exp["total"] and Decimal(str(q["fee"])) == exp["fee"] and Decimal(str(q["tax"])) == exp["tax"]
    same = seat_quote([Decimal(300)] * 4, settings.FEE_PERCENT, settings.TICKET_TAX_PERCENT)
    old = ticket_quote(300, 4, settings.FEE_PERCENT, settings.TICKET_TAX_PERCENT)
    assert same["total"] == old["total"] and same["fee"] == old["fee"] and same["tax"] == old["tax"]
    S.update_seat(db, a1[0], {"price_override": "999.99"})
    S.update_category(db, db.seat_categories.find_one({"name": "VIP"})["_id"], {"base_price": "2000"})
    q2 = S.quote_seats(db, m["_id"], [a1[0], vip])
    assert q2["subtotal"] == 2999.99


def test_quote_rejects_unavailable_duplicate_foreign_and_too_many_seats(db):
    lay = layout(db)
    m1, m2 = two_matches_same_stadium(db)
    S.assign_layout(db, m1["_id"], lay["id"])
    ids = [s["_id"] for s in db.seats.find({}).sort("_id", 1)]
    S.set_seat_status(db, m1["_id"], [ids[0]], "blocked")
    assert codes(lambda: S.quote_seats(db, m1["_id"], [ids[0], ids[1]])) == 409
    assert codes(lambda: S.quote_seats(db, m1["_id"], [ids[1], ids[1]])) == 422
    assert codes(lambda: S.quote_seats(db, m1["_id"], [])) == 422
    assert codes(lambda: S.quote_seats(db, m1["_id"], [999999])) == 404
    assert codes(lambda: S.quote_seats(db, m2["_id"], [ids[1]])) == 409               # match without seating
    assert codes(lambda: S.quote_seats(db, m1["_id"], ids[1:1 + settings.MAX_TICKETS + 1])) == 422


def test_inventory_listing_and_summary_breakdown(db):
    lay = layout(db)
    m = two_matches_same_stadium(db)[0]
    S.assign_layout(db, m["_id"], lay["id"])
    S.set_seat_status(db, m["_id"], [db.seats.find_one({})["_id"]], "blocked")
    sm = S.inventory_summary(db, m["_id"])
    assert sum(c["total"] for c in sm["by_category"]) == 17 and {c["category"] for c in sm["by_category"]} == {"General", "VIP"}
    assert sum(x["total"] for x in sm["by_section"]) == 17
    page = S.list_inventory(db, m["_id"], status="blocked")
    assert page["total"] == 1 and page["items"][0]["seat_key"]
    page = S.list_inventory(db, m["_id"], limit=5, offset=15)
    assert page["total"] == 17 and len(page["items"]) == 2
    assert codes(lambda: S.list_inventory(db, m["_id"], status="nope")) == 422


# ---------------------------------------------------------------- compatibility with Phase 1/2
def test_existing_ticketing_data_and_match_output_are_untouched(db):
    m = two_matches_same_stadium(db)[0]
    before_inv = list(db.ticket_inventory.find({}).sort("_id", 1))
    before_match = services.matches_out(db, [db.matches.find_one({"_id": m["_id"]})])[0]
    before_bookings = db.bookings.count_documents({})
    lay = layout(db)
    S.assign_layout(db, m["_id"], lay["id"])
    assert services.matches_out(db, [db.matches.find_one({"_id": m["_id"]})])[0] == before_match
    S.unassign_layout(db, m["_id"])
    S.delete_layout(db, lay["id"])
    assert list(db.ticket_inventory.find({}).sort("_id", 1)) == before_inv and db.bookings.count_documents({}) == before_bookings
    assert services.matches_out(db, [db.matches.find_one({"_id": m["_id"]})])[0] == before_match


def test_mongodb_validators_are_defined_and_best_effort(db):
    assert set(VALIDATORS) == {"seat_layouts", "seat_categories", "seat_stands", "seat_sections", "seat_rows", "seats", "match_seat_inventory"}
    for v in VALIDATORS.values():
        assert v["$jsonSchema"]["bsonType"] == "object" and v["$jsonSchema"]["required"]
    ensure_seating_validators(db)     # must never raise, even where collMod is unavailable (mongomock)


def test_indexes_for_future_seat_locking_exist(db):
    from app.database import INDEXES
    names = {(c, tuple(k for k, _ in keys)) for c, keys, _ in INDEXES}
    for want in [("match_seat_inventory", ("match_id", "seat_id")), ("match_seat_inventory", ("status", "hold_expires_at")),
                 ("seat_holds", ("hold_id",)), ("seat_holds", ("status", "expires_at")), ("seats", ("stadium_id", "seat_key"))]:
        assert want in names, want
