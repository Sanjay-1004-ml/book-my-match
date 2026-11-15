"""HTTP-level tests of the whole journey: book -> demo pay -> QR tickets -> PDF -> staff scan -> rescan.
Run with pytest (needs fastapi/httpx/mongomock). Rules are tested in depth in test_ticketing_service.py."""
import threading
from datetime import timedelta
from app.clock import now
from .conftest import register, admin_headers, first_match


def staff_headers(client):
    r = client.post("/api/auth/login", json={"email": "staff@test.com", "password": "StaffPass123"})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def book(client, h, qty=2, **extra):
    m, cats = first_match(client)
    r = client.post("/api/bookings", json={"match_id": m["id"], "category_id": cats[0]["category_id"], "quantity": qty, **extra}, headers=h)
    assert r.status_code == 201, r.text
    return r.json(), m


def pay(client, h, booking_id, method="upi", simulate=None):
    body = {"booking_id": booking_id, "method": method}
    if simulate: body["simulate"] = simulate
    return client.post("/api/payments/pay", json=body, headers=h)


def test_full_journey(client):
    h, sh = register(client), staff_headers(client)
    b, m = book(client, h, qty=3)
    assert client.get(f"/api/bookings/{b['id']}/tickets", headers=h).json() == []  # nothing before payment
    p = pay(client, h, b["id"], "credit_card")
    assert p.status_code == 200 and p.json()["outcome"] == "success"
    assert p.json()["booking"]["status"] == "confirmed" and p.json()["booking"]["payment_status"] == "successful"
    tickets = client.get(f"/api/bookings/{b['id']}/tickets", headers=h).json()
    assert len(tickets) == 3 and len({t["qr_payload"] for t in tickets}) == 3
    pdf = client.get(f"/api/bookings/{b['id']}/tickets/pdf", headers=h)
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    one = client.get(f"/api/tickets/{tickets[0]['ticket_id']}/pdf", headers=h)
    assert one.status_code == 200 and one.content.startswith(b"%PDF")
    r = client.post("/api/staff/verify", json={"token": tickets[0]["qr_payload"], "expected_match_id": m["id"]}, headers=sh)
    assert r.status_code == 200 and r.json()["result"] == "success" and r.json()["ticket"]["customer_name"] == "User One"
    r = client.post("/api/staff/verify", json={"token": tickets[0]["qr_payload"]}, headers=sh)
    assert r.json()["result"] == "already_used" and r.json()["previous_entry"]["verified_by"] == "Gate Staff"
    assert client.get(f"/api/tickets/{tickets[0]['ticket_id']}", headers=h).json()["validity"] == "used"
    assert len(client.get("/api/tickets", headers=h).json()) == 3


def test_failure_then_retry_and_no_card_data_accepted(client):
    h = register(client)
    b, _ = book(client, h)
    f = pay(client, h, b["id"], "debit_card", simulate="failure").json()
    assert f["outcome"] == "failed" and f["booking"]["status"] == "pending_payment"
    assert client.get("/api/tickets", headers=h).json() == []
    r = client.post("/api/payments/pay", json={"booking_id": b["id"], "method": "credit_card", "card_number": "4111111111111111", "cvv": "123"}, headers=h)
    assert r.status_code == 422  # extra (sensitive) fields are refused outright
    assert pay(client, h, b["id"], "bitcoin").status_code == 422
    assert pay(client, h, b["id"], "upi").json()["outcome"] == "success"


def test_payment_and_ticket_access_control(client):
    h1, h2 = register(client), register(client, "u2@test.com")
    b, _ = book(client, h1)
    assert pay(client, h2, b["id"]).status_code == 404
    assert client.post("/api/payments/pay", json={"booking_id": b["id"], "method": "upi"}).status_code == 401
    pay(client, h1, b["id"])
    t = client.get(f"/api/bookings/{b['id']}/tickets", headers=h1).json()[0]
    for url in (f"/api/tickets/{t['ticket_id']}", f"/api/tickets/{t['ticket_id']}/pdf", f"/api/bookings/{b['id']}/tickets", f"/api/bookings/{b['id']}/tickets/pdf"):
        assert client.get(url, headers=h2).status_code == 404 and client.get(url).status_code == 401
    assert client.get("/api/tickets", headers=h2).json() == []


def test_only_staff_and_admin_can_verify_or_see_entry_data(client):
    h = register(client)
    b, _ = book(client, h); pay(client, h, b["id"])
    t = client.get(f"/api/bookings/{b['id']}/tickets", headers=h).json()[0]
    body = {"token": t["qr_payload"]}
    assert client.post("/api/staff/verify", json=body, headers=h).status_code == 403   # the ticket owner cannot self-admit
    assert client.post("/api/staff/verify", json=body).status_code == 401
    for url in ("/api/staff/matches", "/api/staff/recent-scans", "/api/admin/tickets", "/api/admin/entry-logs", "/api/admin/ticket-stats", "/api/admin/payments"):
        assert client.get(url, headers=h).status_code == 403, url
    sh, ah = staff_headers(client), admin_headers(client)
    assert client.get("/api/admin/ticket-stats", headers=sh).status_code == 403          # staff scan, but do not see admin data
    assert client.post("/api/staff/verify", json=body, headers=ah).json()["result"] == "success"  # admins may scan too
    assert client.get("/api/auth/me", headers=h).json()["role"] == "user"


def test_staff_role_can_be_granted_only_by_an_admin(client):
    h = register(client); uid = client.get("/api/auth/me", headers=h).json()["id"]
    assert client.put(f"/api/admin/users/{uid}", json={"role": "staff"}, headers=h).status_code == 403
    assert client.put(f"/api/admin/users/{uid}", json={"role": "staff"}, headers=admin_headers(client)).json()["role"] == "staff"
    assert client.get("/api/staff/matches", headers=h).status_code == 200


def test_verify_rejects_garbage_and_extra_fields(client):
    sh = staff_headers(client)
    assert client.post("/api/staff/verify", json={"token": "nonsense"}, headers=sh).json()["result"] == "invalid"
    assert client.post("/api/staff/verify", json={"token": "x", "admin": True}, headers=sh).status_code == 422
    assert client.post("/api/staff/verify", json={}, headers=sh).status_code == 422


def test_cancel_paid_booking_cancels_qr_codes(client):
    h, sh = register(client), staff_headers(client)
    b, _ = book(client, h); pay(client, h, b["id"])
    t = client.get(f"/api/bookings/{b['id']}/tickets", headers=h).json()
    c = client.post(f"/api/bookings/{b['id']}/cancel", headers=h)
    assert c.status_code == 200 and c.json()["refund_status"] == "demo_refund_pending"
    assert client.post("/api/staff/verify", json={"token": t[0]["qr_payload"]}, headers=sh).json()["result"] == "cancelled"
    assert client.get(f"/api/tickets/{t[0]['ticket_id']}", headers=h).json()["validity"] == "cancelled"


def test_expired_reservation_cannot_be_paid_and_releases_tickets(client, db):
    h = register(client)
    b, m = book(client, h, qty=2)
    before = client.get(f"/api/matches/{m['id']}/ticket-categories").json()[0]["available"]
    db.bookings.update_one({"_id": b["id"]}, {"$set": {"expires_at": now() - timedelta(minutes=1)}})
    r = pay(client, h, b["id"])
    assert r.status_code in (409, 410)
    assert client.get(f"/api/matches/{m['id']}/ticket-categories").json()[0]["available"] == before + 2
    assert client.get("/api/tickets", headers=h).json() == [] and db.payments.count_documents({}) == 0


def test_simultaneous_http_payments_and_scans(client):
    h, sh = register(client), staff_headers(client)
    b, _ = book(client, h, qty=2)
    res = []
    ts = [threading.Thread(target=lambda: res.append(pay(client, h, b["id"]).json()["outcome"])) for _ in range(6)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert res.count("success") == 1 and res.count("already_paid") == 5
    tk = client.get(f"/api/bookings/{b['id']}/tickets", headers=h).json()
    assert len(tk) == 2
    out = []
    ts = [threading.Thread(target=lambda: out.append(client.post("/api/staff/verify", json={"token": tk[0]["qr_payload"]}, headers=sh).json()["result"])) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert out.count("success") == 1 and out.count("already_used") == 7


def test_admin_ticket_views_and_stats(client):
    h, sh, ah = register(client), staff_headers(client), admin_headers(client)
    b, m = book(client, h, qty=2); pay(client, h, b["id"])
    t = client.get(f"/api/bookings/{b['id']}/tickets", headers=h).json()
    client.post("/api/staff/verify", json={"token": t[0]["qr_payload"]}, headers=sh)
    assert [x["ticket_id"] for x in client.get(f"/api/admin/tickets?q={t[1]['ticket_id']}", headers=ah).json()] == [t[1]["ticket_id"]]
    assert len(client.get(f"/api/admin/tickets?q={b['reference']}", headers=ah).json()) == 2
    assert len(client.get(f"/api/admin/tickets?match_id={m['id']}&status=used", headers=ah).json()) == 1
    assert len(client.get("/api/admin/tickets?status=valid", headers=ah).json()) == 1
    assert client.get("/api/admin/tickets?status=bogus", headers=ah).status_code == 422
    d = client.get(f"/api/admin/tickets/{t[0]['ticket_id']}", headers=ah).json()
    assert d["entry_history"][0]["result"] == "success" and "qr_payload" not in d
    st = client.get(f"/api/admin/ticket-stats?match_id={m['id']}", headers=ah).json()
    assert (st["totals"]["sold"], st["totals"]["used"], st["totals"]["valid"]) == (2, 1, 1)
    assert client.get("/api/admin/entry-logs?result=success", headers=ah).json()[0]["scanned_by"] == "Gate Staff"
