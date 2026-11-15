"""Phase 3A: HTTP-level seating tests (needs fastapi/httpx/mongomock like the rest of the suite).
Business rules are tested in depth in test_seating_service.py; this file covers routing, validation, authorization and compatibility."""
from .conftest import register, admin_headers, first_match

SPEC = {"categories": [{"name": "General", "base_price": 300}, {"name": "VIP", "base_price": 1500}],
        "stands": [{"name": "North Stand", "code": "N", "sections": [
            {"name": "Block A", "category": "General", "rows": [{"label": "A1", "seat_count": 5}, {"label": "A2", "seat_count": 5}]}]},
            {"name": "VIP Section", "code": "V", "sections": [{"name": "Lounge", "category": "VIP", "rows": [{"label": "V1", "seat_count": 3}]}]}]}


def stadium_matches(client):
    by = {}
    for m in client.get("/api/matches?limit=100").json():
        by.setdefault(m["stadium"]["id"], []).append(m)
    return next(v for v in by.values() if len(v) >= 2)


def make_layout(client, ah, sid=None, spec=SPEC):
    sid = sid or stadium_matches(client)[0]["stadium"]["id"]
    r = client.post("/api/admin/seating/layouts", headers=ah, json={"stadium_id": sid, "name": "Main layout", **spec})
    assert r.status_code == 201, r.text
    return r.json()


ADMIN_CALLS = [
    ("get", "/api/admin/seating/layouts", None), ("post", "/api/admin/seating/layouts", {"stadium_id": 1, "name": "Layout"}),
    ("get", "/api/admin/seating/layouts/1", None), ("put", "/api/admin/seating/layouts/1", {"name": "Renamed"}), ("delete", "/api/admin/seating/layouts/1", None),
    ("post", "/api/admin/seating/layouts/1/categories", {"name": "X", "base_price": 1}), ("put", "/api/admin/seating/categories/1", {"base_price": 5}),
    ("delete", "/api/admin/seating/categories/1", None), ("post", "/api/admin/seating/layouts/1/stands", {"name": "Stand"}),
    ("put", "/api/admin/seating/stands/1", {"name": "Stand"}), ("delete", "/api/admin/seating/stands/1", None),
    ("post", "/api/admin/seating/stands/1/sections", {"name": "Sec", "category_id": 1}), ("put", "/api/admin/seating/sections/1", {"is_active": False}),
    ("delete", "/api/admin/seating/sections/1", None), ("post", "/api/admin/seating/sections/1/rows", {"label": "A", "seat_count": 2}),
    ("put", "/api/admin/seating/rows/1", {"seat_count": 3}), ("delete", "/api/admin/seating/rows/1", None), ("get", "/api/admin/seating/rows/1/seats", None),
    ("post", "/api/admin/seating/rows/1/seats", {"seat_number": 9}), ("put", "/api/admin/seating/seats/1", {"is_active": False}),
    ("delete", "/api/admin/seating/seats/1", None), ("put", "/api/admin/matches/1/seating", {"layout_id": 1}), ("delete", "/api/admin/matches/1/seating", None),
    ("get", "/api/admin/matches/1/seat-inventory/summary", None), ("get", "/api/admin/matches/1/seat-inventory", None),
    ("put", "/api/admin/matches/1/seat-inventory/status", {"seat_ids": [1], "status": "blocked"}),
]


def call(client, verb, url, body, headers=None):
    kw = {"headers": headers or {}}
    if body is not None:
        kw["json"] = body
    return getattr(client, verb)(url, **kw)


def test_every_admin_seating_endpoint_rejects_anonymous_and_normal_users(client):
    uh = register(client)
    for verb, url, body in ADMIN_CALLS:
        assert call(client, verb, url, body).status_code == 401, (verb, url)
        assert call(client, verb, url, body, uh).status_code == 403, (verb, url)
    # staff are not administrators either
    r = client.post("/api/auth/login", json={"email": "staff@test.com", "password": "StaffPass123"})
    sh = {"Authorization": "Bearer " + r.json()["token"]}
    assert call(client, "post", "/api/admin/seating/layouts", {"stadium_id": 1, "name": "Layout"}, sh).status_code == 403


def test_admin_creates_reads_updates_a_layout(client):
    ah = admin_headers(client)
    lay = make_layout(client, ah)
    assert lay["totals"]["seats"] == 13 and lay["totals"]["stands"] == 2
    tree = client.get(f"/api/admin/seating/layouts/{lay['id']}", headers=ah).json()
    assert tree["stands"][0]["sections"][0]["rows"][0]["label"] == "A1" and tree["categories"][0]["base_price"] == 300.0
    assert [x["id"] for x in client.get(f"/api/admin/seating/layouts?stadium_id={lay['stadium_id']}", headers=ah).json()] == [lay["id"]]
    assert client.put(f"/api/admin/seating/layouts/{lay['id']}", headers=ah, json={"name": "Renamed layout"}).json()["name"] == "Renamed layout"
    cat = tree["categories"][0]["id"]
    r = client.put(f"/api/admin/seating/categories/{cat}", headers=ah, json={"base_price": 350})
    assert r.status_code == 200 and r.json()["base_price"] == 350.0
    assert client.put(f"/api/admin/seating/categories/{cat}", headers=ah, json={"base_price": -1}).status_code == 422
    assert client.post("/api/admin/seating/layouts", headers=ah, json={"stadium_id": lay["stadium_id"], "name": "Second"}).status_code == 409


def test_validation_errors_are_422_and_unknown_fields_are_rejected(client):
    ah = admin_headers(client)
    sid = stadium_matches(client)[0]["stadium"]["id"]
    assert client.post("/api/admin/seating/layouts", headers=ah, json={"stadium_id": sid, "name": "X"}).status_code == 422      # name too short
    assert client.post("/api/admin/seating/layouts", headers=ah, json={"stadium_id": sid, "name": "Layout", "surprise": 1}).status_code == 422
    bad = {**SPEC, "stands": [{"name": "N Stand", "sections": [{"name": "Blk", "category": "Nope", "rows": []}]}]}
    assert client.post("/api/admin/seating/layouts", headers=ah, json={"stadium_id": sid, "name": "Layout", **bad}).status_code == 422
    assert client.get("/api/admin/seating/layouts", headers=ah).json() == []      # nothing half-created


def test_stands_sections_rows_seats_over_http(client):
    ah = admin_headers(client)
    lay = make_layout(client, ah)
    tree = client.get(f"/api/admin/seating/layouts/{lay['id']}", headers=ah).json()
    gen = tree["categories"][0]["id"]
    st = client.post(f"/api/admin/seating/layouts/{lay['id']}/stands", headers=ah, json={"name": "East Stand"})
    assert st.status_code == 201
    assert client.post(f"/api/admin/seating/layouts/{lay['id']}/stands", headers=ah, json={"name": "East Stand"}).status_code == 409
    sec = client.post(f"/api/admin/seating/stands/{st.json()['id']}/sections", headers=ah, json={"name": "East 1", "category_id": gen})
    assert sec.status_code == 201
    row = client.post(f"/api/admin/seating/sections/{sec.json()['id']}/rows", headers=ah, json={"label": "E1", "seat_count": 4})
    assert row.status_code == 201 and row.json()["seat_count"] == 4
    seats = client.get(f"/api/admin/seating/rows/{row.json()['id']}/seats", headers=ah).json()
    assert [s["seat_number"] for s in seats] == [1, 2, 3, 4] and seats[0]["base_price"] == 300.0
    assert client.post(f"/api/admin/seating/rows/{row.json()['id']}/seats", headers=ah, json={"seat_number": 4}).status_code == 409
    assert client.post(f"/api/admin/seating/rows/{row.json()['id']}/seats", headers=ah, json={"seat_number": 5, "price_override": 450}).json()["base_price"] == 450.0
    assert client.put(f"/api/admin/seating/sections/{sec.json()['id']}", headers=ah, json={"is_active": False}).json()["is_active"] is False
    assert client.delete(f"/api/admin/seating/rows/{row.json()['id']}", headers=ah).status_code == 204
    assert client.delete(f"/api/admin/seating/stands/{st.json()['id']}", headers=ah).status_code == 204
    assert client.delete("/api/admin/seating/seats/999999", headers=ah).status_code == 404


def test_assign_layout_inventory_and_public_reads(client):
    ah = admin_headers(client)
    m1, m2 = stadium_matches(client)[:2]
    lay = make_layout(client, ah, m1["stadium"]["id"])
    assert client.get(f"/api/matches/{m1['id']}/seating").json() == {"match_id": m1["id"], "seating_enabled": False}      # existing matches keep working
    r = client.put(f"/api/admin/matches/{m1['id']}/seating", headers=ah, json={"layout_id": lay["id"]})
    assert r.status_code == 200 and r.json()["total"] == 13 and r.json()["available"] == 13
    summ = client.get(f"/api/admin/matches/{m1['id']}/seat-inventory/summary", headers=ah).json()
    assert summ["total"] == 13 and client.get(f"/api/admin/matches/{m2['id']}/seat-inventory/summary", headers=ah).json()["seating_enabled"] is False
    items = client.get(f"/api/admin/matches/{m1['id']}/seat-inventory?limit=5", headers=ah).json()
    assert items["total"] == 13 and len(items["items"]) == 5
    sid = items["items"][0]["seat_id"]
    assert client.put(f"/api/admin/matches/{m1['id']}/seat-inventory/status", headers=ah, json={"seat_ids": [sid], "status": "blocked"}).json()["changed"] == 1
    assert client.put(f"/api/admin/matches/{m1['id']}/seat-inventory/status", headers=ah, json={"seat_ids": [sid], "status": "booked"}).status_code == 422
    # public (no login) reads
    pub = client.get(f"/api/matches/{m1['id']}/seating").json()
    assert pub["seating_enabled"] and pub["stands"][0]["sections"][0]["available"] <= 10
    sec = pub["stands"][0]["sections"][0]["id"]
    detail = client.get(f"/api/matches/{m1['id']}/seating/sections/{sec}").json()
    assert detail["rows"][0]["seats"][0]["status"] == "unavailable" and detail["rows"][0]["seats"][1]["selectable"] is True
    assert client.get(f"/api/stadiums/{m1['stadium']['id']}/seating").json()["seating_enabled"] is True
    assert client.get("/api/matches/999999/seating").status_code == 404
    # another stadium's layout cannot be assigned
    other = next(m for m in client.get("/api/matches?limit=100").json() if m["stadium"]["id"] != m1["stadium"]["id"])
    assert client.put(f"/api/admin/matches/{other['id']}/seating", headers=ah, json={"layout_id": lay["id"]}).status_code == 409
    assert client.delete(f"/api/admin/matches/{m1['id']}/seating", headers=ah).status_code == 204
    assert client.delete(f"/api/admin/seating/layouts/{lay['id']}", headers=ah).status_code == 204


def test_seat_quote_ignores_client_prices_and_uses_existing_fee_and_tax(client):
    ah = admin_headers(client)
    m1 = stadium_matches(client)[0]
    lay = make_layout(client, ah, m1["stadium"]["id"])
    client.put(f"/api/admin/matches/{m1['id']}/seating", headers=ah, json={"layout_id": lay["id"]})
    ids = [i["seat_id"] for i in client.get(f"/api/admin/matches/{m1['id']}/seat-inventory?limit=3", headers=ah).json()["items"]]
    r = client.post(f"/api/matches/{m1['id']}/seat-quote", json={"seat_ids": ids[:2]})
    assert r.status_code == 200 and r.json()["subtotal"] == 600.0 and r.json()["fee"] == 12.0 and r.json()["tax"] == 110.16 and r.json()["total"] == 722.16
    assert client.post(f"/api/matches/{m1['id']}/seat-quote", json={"seat_ids": ids[:2], "price": 1}).status_code == 422       # no price field exists
    assert client.post(f"/api/matches/{m1['id']}/seat-quote", json={"seat_ids": ids[:2], "total": 1}).status_code == 422
    assert client.post(f"/api/matches/{m1['id']}/seat-quote", json={"seat_ids": []}).status_code == 422
    assert client.post(f"/api/matches/{m1['id']}/seat-quote", json={"seat_ids": [ids[0], ids[0]]}).status_code == 422


def test_existing_booking_and_food_flows_still_work_and_seat_matches_refuse_category_tickets(client):
    ah, uh = admin_headers(client), register(client)
    ms = stadium_matches(client)
    seat_match, plain = ms[0], ms[1]
    lay = make_layout(client, ah, seat_match["stadium"]["id"])
    client.put(f"/api/admin/matches/{seat_match['id']}/seating", headers=ah, json={"layout_id": lay["id"]})
    cats = client.get(f"/api/matches/{seat_match['id']}/ticket-categories").json()
    r = client.post("/api/bookings", headers=uh, json={"match_id": seat_match["id"], "category_id": cats[0]["category_id"], "quantity": 1})
    assert r.status_code == 409 and "seat" in r.json()["detail"].lower()          # a seat-enabled match is sold by seat only
    # a match WITHOUT a layout keeps the existing category booking exactly as before
    pcats = client.get(f"/api/matches/{plain['id']}/ticket-categories").json()
    before = pcats[0]["available"]
    r = client.post("/api/bookings", headers=uh, json={"match_id": plain["id"], "category_id": pcats[0]["category_id"], "quantity": 2})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "pending_payment" and r.json()["booking_type"] == "category" and r.json()["seats"] == []
    assert client.get(f"/api/matches/{plain['id']}/ticket-categories").json()[0]["available"] == before - 2
    assert client.get("/api/bookings", headers=uh).status_code == 200
    brownie = next(i for i in client.get("/api/food/items").json() if i["name"] == "Brownie")
    q = client.post("/api/food/quote", json={"items": [{"item_id": brownie["id"], "quantity": 2}]}).json()
    assert (q["subtotal"], q["fee"], q["tax"], q["total"]) == (160.0, 0.0, 8.0, 168.0)          # food tax is still 5%, billed separately
    assert client.get("/api/admin/dashboard", headers=ah).status_code == 200
