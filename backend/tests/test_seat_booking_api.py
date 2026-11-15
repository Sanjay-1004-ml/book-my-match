"""Phase 3B-3D end to end over HTTP: select seats -> hold -> pay -> tickets/QR -> My Bookings -> staff scan (needs fastapi/httpx/mongomock)."""
from .conftest import register, admin_headers
from .test_seating_api import make_layout, stadium_matches


def staff_headers(client):
    r = client.post("/api/auth/login", json={"email": "staff@test.com", "password": "StaffPass123"})
    return {"Authorization": "Bearer " + r.json()["token"]}


def ready(client, ah):
    m1, m2 = stadium_matches(client)[:2]
    lay = make_layout(client, ah, m1["stadium"]["id"])
    for m in (m1, m2):
        assert client.put(f"/api/admin/matches/{m['id']}/seating", headers=ah, json={"layout_id": lay["id"]}).status_code == 200
    ids = [i["seat_id"] for i in client.get(f"/api/admin/matches/{m1['id']}/seat-inventory?limit=20", headers=ah).json()["items"]]
    return m1, m2, ids


def test_booking_endpoints_require_login(client):
    ah = admin_headers(client)
    m1, m2, ids = ready(client, ah)
    assert client.post("/api/bookings/seats", json={"match_id": m1["id"], "seat_ids": ids[:1]}).status_code == 401
    assert client.post("/api/payments/pay", json={"booking_id": 1, "method": "upi"}).status_code == 401


def test_full_seat_journey_hold_pay_ticket_my_bookings_staff_scan(client):
    ah, uh, sh = admin_headers(client), register(client), staff_headers(client)
    m1, m2, ids = ready(client, ah)
    r = client.post("/api/bookings/seats", headers=uh, json={"match_id": m1["id"], "seat_ids": ids[:2]})
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["booking_type"] == "seat" and b["status"] == "pending_payment" and b["quantity"] == 2 and 0 < b["expires_in_seconds"] <= 600
    assert b["ticket_total"] == 722.16 and [s["seat_key"] for s in b["seats"]] == ["NBLOCKA-A1-1", "NBLOCKA-A1-2"] and b["ticket_count"] == 0
    # the map now shows the seats as held for everybody else and as 'mine' for the owner
    sec = client.get(f"/api/matches/{m1['id']}/seating").json()["stands"][0]["sections"][0]["id"]
    seat_row = lambda hd: client.get(f"/api/matches/{m1['id']}/seating/sections/{sec}", headers=hd).json()["rows"][0]["seats"]
    assert seat_row(uh)[0]["status"] == "mine" and seat_row({})[0]["status"] == "held" and not seat_row({})[0]["selectable"]
    other = register(client, "u2@test.com")
    assert client.post("/api/bookings/seats", headers=other, json={"match_id": m1["id"], "seat_ids": [ids[1], ids[2]]}).status_code == 409
    # pay with the existing payment endpoint
    p = client.post("/api/payments/pay", headers=uh, json={"booking_id": b["id"], "method": "upi"})
    assert p.status_code == 200, p.text
    assert client.post("/api/payments/pay", headers=uh, json={"booking_id": b["id"], "method": "upi"}).json()["outcome"] == "already_paid"
    mine = client.get(f"/api/bookings/{b['id']}", headers=uh).json()
    assert mine["status"] == "confirmed" and mine["payment_status"] == "successful" and mine["ticket_count"] == 2 and mine["seats"][0]["row"] == "A1"
    assert any(x["id"] == b["id"] for x in client.get("/api/bookings", headers=uh).json())
    tix = client.get(f"/api/bookings/{b['id']}/tickets", headers=uh).json()
    assert len(tix) == 2 and tix[0]["seat"]["seat_key"] == "NBLOCKA-A1-1" and tix[0]["qr_payload"].startswith("BMMT:") and tix[0]["qr_image"]
    pdf = client.get(f"/api/bookings/{b['id']}/tickets/pdf", headers=uh)
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    # booked seats are red for everyone, free for the other match
    assert seat_row({})[0]["status"] == "booked"
    assert client.post("/api/bookings/seats", headers=other, json={"match_id": m2["id"], "seat_ids": ids[:1]}).status_code == 201
    # staff verification
    ok = client.post("/api/staff/verify", headers=sh, json={"token": tix[0]["qr_payload"]}).json()
    assert ok["ok"] and ok["ticket"]["seat"]["seat_key"] == "NBLOCKA-A1-1" and ok["ticket"]["booking_reference"] == b["reference"]
    assert client.post("/api/staff/verify", headers=sh, json={"token": tix[0]["qr_payload"]}).json()["result"] == "already_used"
    assert client.post("/api/staff/verify", headers=uh, json={"token": tix[1]["qr_payload"]}).status_code == 403      # a normal user cannot scan
    inv = client.get(f"/api/admin/matches/{m1['id']}/seat-inventory?status=booked", headers=ah).json()["items"]
    assert {i["booking_reference"] for i in inv} == {b["reference"]}


def test_client_cannot_choose_the_price_and_invalid_requests_are_rejected(client):
    ah, uh = admin_headers(client), register(client)
    m1, m2, ids = ready(client, ah)
    post = lambda **kw: client.post("/api/bookings/seats", headers=uh, json=kw)
    assert post(match_id=m1["id"], seat_ids=ids[:1], total=1).status_code == 422
    assert post(match_id=m1["id"], seat_ids=ids[:1], price=1).status_code == 422
    assert post(match_id=m1["id"], seat_ids=[]).status_code == 422
    assert post(match_id=m1["id"], seat_ids=[ids[0], ids[0]]).status_code == 422
    assert post(match_id=m1["id"], seat_ids=[999999]).status_code == 404
    assert post(match_id=999999, seat_ids=ids[:1]).status_code == 404
    assert client.post("/api/payments/pay", headers=uh, json={"booking_id": 1, "method": "upi", "amount": 1}).status_code == 422


def test_failed_payment_keeps_the_hold_cancel_releases_it_and_retry_works(client):
    ah, uh = admin_headers(client), register(client)
    m1, m2, ids = ready(client, ah)
    b = client.post("/api/bookings/seats", headers=uh, json={"match_id": m1["id"], "seat_ids": ids[:1]}).json()
    f = client.post("/api/payments/pay", headers=uh, json={"booking_id": b["id"], "method": "upi", "simulate": "failure"})
    assert f.status_code == 200 and f.json()["outcome"] == "failed"
    assert client.get(f"/api/bookings/{b['id']}", headers=uh).json()["ticket_count"] == 0
    assert client.get(f"/api/admin/matches/{m1['id']}/seat-inventory/summary", headers=ah).json()["held"] == 1
    c = client.post(f"/api/bookings/{b['id']}/cancel", headers=uh)
    assert c.status_code == 200 and c.json()["status"] == "cancelled"
    assert client.get(f"/api/admin/matches/{m1['id']}/seat-inventory/summary", headers=ah).json()["held"] == 0
    assert client.post(f"/api/bookings/{b['id']}/cancel", headers=uh).status_code == 409
    b2 = client.post("/api/bookings/seats", headers=uh, json={"match_id": m1["id"], "seat_ids": ids[:1]}).json()
    assert client.post("/api/payments/pay", headers=uh, json={"booking_id": b2["id"], "method": "upi", "simulate": "failure"}).json()["outcome"] == "failed"
    assert client.post("/api/payments/pay", headers=uh, json={"booking_id": b2["id"], "method": "credit_card"}).json()["outcome"] == "success"


def test_idempotency_key_returns_the_same_hold_and_other_users_cannot_see_or_pay_it(client):
    ah, uh = admin_headers(client), register(client)
    m1, m2, ids = ready(client, ah)
    h = {**uh, "Idempotency-Key": "k-123"}
    a = client.post("/api/bookings/seats", headers=h, json={"match_id": m1["id"], "seat_ids": ids[:1]})
    b = client.post("/api/bookings/seats", headers=h, json={"match_id": m1["id"], "seat_ids": ids[:1]})
    assert a.status_code == 201 and b.status_code == 200 and a.json()["id"] == b.json()["id"]
    other = register(client, "u2@test.com")
    assert client.get(f"/api/bookings/{a.json()['id']}", headers=other).status_code == 404
    assert client.post("/api/payments/pay", headers=other, json={"booking_id": a.json()["id"], "method": "upi"}).status_code == 404
    assert client.post(f"/api/bookings/{a.json()['id']}/cancel", headers=other).status_code == 404


def test_cancelling_a_paid_seat_booking_frees_the_seats_and_cancels_the_tickets(client):
    ah, uh = admin_headers(client), register(client)
    m1, m2, ids = ready(client, ah)
    b = client.post("/api/bookings/seats", headers=uh, json={"match_id": m1["id"], "seat_ids": ids[:2]}).json()
    client.post("/api/payments/pay", headers=uh, json={"booking_id": b["id"], "method": "upi"})
    assert client.post(f"/api/bookings/{b['id']}/cancel", headers=uh).status_code == 200
    s = client.get(f"/api/admin/matches/{m1['id']}/seat-inventory/summary", headers=ah).json()
    assert s["booked"] == 0 and s["held"] == 0 and s["available"] == s["total"]
    tix = client.get(f"/api/bookings/{b['id']}/tickets", headers=uh).json()
    assert all(t["ticket_status"] == "cancelled" for t in tix)


def test_food_orders_stay_separate_from_seat_bookings(client):
    ah, uh = admin_headers(client), register(client)
    m1, m2, ids = ready(client, ah)
    brownie = next(i for i in client.get("/api/food/items").json() if i["name"] == "Brownie")
    b = client.post("/api/bookings/seats", headers=uh, json={"match_id": m1["id"], "seat_ids": ids[:1]}).json()
    assert b["food_total"] == 0.0 and b["grand_total"] == b["ticket_total"]
    o = client.post("/api/food/orders", headers=uh, json={"items": [{"item_id": brownie["id"], "quantity": 2}]})
    assert o.status_code == 201
    pay = client.post("/api/payments/pay", headers=uh, json={"food_order_id": o.json()["id"], "method": "upi"})
    assert pay.status_code == 200 and pay.json()["payment"]["amount"] == 168.0
    assert client.get(f"/api/bookings/{b['id']}", headers=uh).json()["payment_status"] == "pending"      # ticket payment untouched
