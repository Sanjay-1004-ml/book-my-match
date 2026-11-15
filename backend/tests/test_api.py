import threading
from decimal import Decimal
from .conftest import register, admin_headers, first_match


def test_register_login_validation(client):
    h = register(client)
    assert client.get("/api/auth/me", headers=h).json()["role"] == "user"
    assert client.post("/api/auth/register", json={"name": "X Y", "email": "u1@test.com", "password": "Passw0rd123", "confirm_password": "Passw0rd123"}).status_code == 409
    assert client.post("/api/auth/register", json={"name": "X Y", "email": "a@b.com", "password": "short", "confirm_password": "short"}).status_code == 422
    assert client.post("/api/auth/login", json={"email": "u1@test.com", "password": "wrong"}).status_code == 401


def test_catalog(client):
    assert len(client.get("/api/sports").json()) == 5
    assert len(client.get("/api/stadiums").json()) == 17
    assert len(client.get("/api/matches?limit=100").json()) == 85
    assert client.get("/api/matches/999999").status_code == 404
    assert client.get("/api/matches/search?q=cricket").json()


def test_book_price_and_availability(client):
    h = register(client)
    m, cats = first_match(client)
    c = cats[0]
    r = client.post("/api/bookings", json={"match_id": m["id"], "category_id": c["category_id"], "quantity": 2}, headers=h)
    assert r.status_code == 201, r.text
    b = r.json()
    sub = Decimal(str(c["price"])) * 2
    fee = (sub * Decimal("0.02")).quantize(Decimal("0.01"))
    tax = ((sub + fee) * Decimal("0.18")).quantize(Decimal("0.01"))
    assert Decimal(str(b["ticket_total"])) == sub + fee + tax
    after = [x for x in client.get(f"/api/matches/{m['id']}/ticket-categories").json() if x["category_id"] == c["category_id"]][0]
    assert after["available"] == c["available"] - 2
    assert b["payment_status"] == "pending" and b["demo_payment"] is True
    assert b["status"] == "pending_payment" and b["expires_at"] and b["ticket_count"] == 0  # held, not confirmed, no tickets yet


def test_invalid_requests(client):
    h = register(client)
    m, cats = first_match(client)
    cid = cats[0]["category_id"]

    post = lambda **kw: client.post(
        "/api/bookings",
        json=kw,
        headers=h
    )

    # Invalid match
    assert post(
        match_id=999999,
        category_id=cid,
        quantity=1
    ).status_code == 404

    # Invalid ticket category
    assert post(
        match_id=m["id"],
        category_id=999999,
        quantity=1
    ).status_code == 400

    # Quantity below minimum
    assert post(
        match_id=m["id"],
        category_id=cid,
        quantity=0
    ).status_code == 422

    # Quantity above the allowed maximum
    assert post(
        match_id=m["id"],
        category_id=cid,
        quantity=101
    ).status_code == 422

    # Set inventory to 3 tickets for testing overselling
    ah = admin_headers(client)

    inventory_response = client.put(
        "/api/admin/inventory",
        headers=ah,
        json={
            "match_id": m["id"],
            "category_id": cid,
            "total": 3
        }
    )

    assert inventory_response.status_code == 200, inventory_response.text

    # Request 4 tickets when only 3 are available
    assert post(
        match_id=m["id"],
        category_id=cid,
        quantity=4
    ).status_code in (400, 409)

    # Booking without authentication
    assert client.post(
        "/api/bookings",
        json={
            "match_id": m["id"],
            "category_id": cid,
            "quantity": 1
        }
    ).status_code == 401

def test_category_from_other_stadium_rejected(client):
    h = register(client)
    ms = client.get("/api/matches?limit=100").json()
    m1 = ms[0]
    other = next(x for x in ms if x["stadium"]["id"] != m1["stadium"]["id"])
    foreign = client.get(f"/api/matches/{other['id']}/ticket-categories").json()[0]["category_id"]
    assert client.post("/api/bookings", json={"match_id": m1["id"], "category_id": foreign, "quantity": 1}, headers=h).status_code == 400


def test_idempotency_key(client):
    h = register(client)
    m, cats = first_match(client)
    body = {"match_id": m["id"], "category_id": cats[0]["category_id"], "quantity": 1}
    r1 = client.post("/api/bookings", json=body, headers={**h, "Idempotency-Key": "abc123"})
    r2 = client.post("/api/bookings", json=body, headers={**h, "Idempotency-Key": "abc123"})
    assert r1.status_code == 201 and r2.status_code == 200 and r1.json()["id"] == r2.json()["id"]
    assert len(client.get("/api/bookings", headers=h).json()) == 1


def test_cancel_releases_inventory_and_ownership(client):
    h1, h2 = register(client), register(client, "u2@test.com")
    m, cats = first_match(client)
    c = cats[0]
    b = client.post("/api/bookings", json={"match_id": m["id"], "category_id": c["category_id"], "quantity": 3}, headers=h1).json()
    assert client.get(f"/api/bookings/{b['id']}", headers=h2).status_code == 404
    assert client.post(f"/api/bookings/{b['id']}/cancel", headers=h2).status_code == 404
    assert client.post("/api/payments/pay", json={"booking_id": b["id"], "method": "upi"}, headers=h1).json()["outcome"] == "success"
    r = client.post(f"/api/bookings/{b['id']}/cancel", headers=h1)
    assert r.status_code == 200 and r.json()["status"] == "cancelled" and r.json()["refund_status"] == "demo_refund_pending"
    assert client.post(f"/api/bookings/{b['id']}/cancel", headers=h1).status_code == 409
    after = [x for x in client.get(f"/api/matches/{m['id']}/ticket-categories").json() if x["category_id"] == c["category_id"]][0]
    assert after["available"] == c["available"]


def test_food_and_booking_with_food(client):
    h = register(client)
    items = client.get("/api/food/items").json()
    assert len(items) == 21
    brownie = next(i for i in items if i["name"] == "Brownie")
    m, cats = first_match(client)
    b = client.post("/api/bookings", headers=h, json={"match_id": m["id"], "category_id": cats[0]["category_id"], "quantity": 1,
                                                      "food": [{"item_id": brownie["id"], "quantity": 2}]}).json()
    assert b["food_total"] == 168.0  # 160 + 5% tax
    assert client.get(f"/api/bookings/{b['id']}", headers=h).json()["food_orders"][0]["items"][0]["name"] == "Brownie"
    o = client.post("/api/food/orders", headers=h, json={"items": [{"item_id": brownie["id"], "quantity": 1}], "booking_id": b["id"]})
    assert o.status_code == 201 and o.json()["total"] == 84.0
    assert client.post("/api/food/orders", headers=h, json={"items": [{"item_id": 99999, "quantity": 1}]}).status_code == 400


def test_admin_authorization(client):
    h = register(client)
    assert client.get("/api/admin/dashboard", headers=h).status_code == 403
    assert client.get("/api/admin/dashboard").status_code == 401
    ah = admin_headers(client)
    assert client.get("/api/admin/dashboard", headers=ah).json()["stadiums"] == 17
    r = client.post("/api/admin/food/items", headers=ah, json={"category_id": 1, "name": "Cake", "price": 70})
    assert r.status_code == 201
    assert client.post("/api/admin/food/items", headers=h, json={"category_id": 1, "name": "Cake", "price": 70}).status_code == 403


def test_admin_match_crud_and_price_change(client):
    ah = admin_headers(client)
    st = client.get("/api/stadiums").json()[0]
    r = client.post("/api/admin/matches", headers=ah, json={"stadium_id": st["id"], "team_a": "AAA", "team_b": "BBB", "match_date": "2030-01-01", "start_time": "18:00:00"})
    assert r.status_code == 201, r.text
    mid = r.json()["id"]
    assert client.get(f"/api/matches/{mid}/ticket-categories").json()
    cid = st["categories"][0]["id"]
    assert client.put(f"/api/admin/ticket-categories/{cid}", headers=ah, json={"price": 7000}).json()["price"] == 7000
    assert client.delete(f"/api/admin/matches/{mid}", headers=ah).status_code == 204


def test_concurrent_booking_never_oversells(client, db):
    h = register(client)
    m, cats = first_match(client)
    cid = cats[0]["category_id"]
    ah = admin_headers(client)
    client.put("/api/admin/inventory", headers=ah, json={"match_id": m["id"], "category_id": cid, "total": 3})
    results = []

    def go():
        r = client.post("/api/bookings", json={"match_id": m["id"], "category_id": cid, "quantity": 1}, headers=h)
        results.append(r.status_code)

    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert results.count(201) == 3, results
    inv = db.ticket_inventory.find_one({"match_id": m["id"], "category_id": cid})
    assert inv["available"] == 0


def test_idempotent_replay_does_not_double_reserve(client, db):
    h = register(client)
    m, cats = first_match(client)
    cid = cats[0]["category_id"]
    body = {"match_id": m["id"], "category_id": cid, "quantity": 2}
    before = db.ticket_inventory.find_one({"match_id": m["id"], "category_id": cid})["available"]
    for _ in range(3):
        client.post("/api/bookings", json=body, headers={**h, "Idempotency-Key": "same-key-1"})
    assert db.bookings.count_documents({}) == 1
    assert db.ticket_inventory.find_one({"match_id": m["id"], "category_id": cid})["available"] == before - 2


def test_cancel_twice_releases_inventory_once(client, db):
    h = register(client)
    m, cats = first_match(client)
    cid = cats[0]["category_id"]
    before = db.ticket_inventory.find_one({"match_id": m["id"], "category_id": cid})["available"]
    b = client.post("/api/bookings", json={"match_id": m["id"], "category_id": cid, "quantity": 2}, headers=h).json()
    assert client.post(f"/api/bookings/{b['id']}/cancel", headers=h).status_code == 200
    assert client.post(f"/api/bookings/{b['id']}/cancel", headers=h).status_code == 409
    assert db.ticket_inventory.find_one({"match_id": m["id"], "category_id": cid})["available"] == before


def test_ids_are_integers_and_new_ids_continue(client, db):
    h = register(client)
    assert isinstance(client.get("/api/auth/me", headers=h).json()["id"], int)
    ah = admin_headers(client)
    sid = client.post("/api/admin/sports", headers=ah, json={"name": "Kabaddi"}).json()["id"]
    sid2 = client.post("/api/admin/sports", headers=ah, json={"name": "Chess"}).json()["id"]
    assert sid == 6 and sid2 == 7  # seeded sports are 1..5; counters were synced
    assert client.post("/api/admin/sports", headers=ah, json={"name": "Kabaddi"}).status_code == 409


def test_search_input_is_not_interpreted_as_regex(client):
    assert client.get("/api/matches/search?q=.*").json() == []
    assert client.get("/api/matches?q=(").status_code == 200
