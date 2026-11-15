"""HTTP-level tests for the enhanced food section: menu, server-priced quote, food orders, food payment (alone and
together with tickets), confirmation/receipt, admin item fields. Run with pytest (needs fastapi/httpx/mongomock).
Rules are tested in depth in test_food_service.py."""
from .conftest import register, admin_headers, first_match


def item(client, name):
    return next(i for i in client.get("/api/food/items").json() if i["name"] == name)


def order(client, h, lines, booking_id=None, key=None):
    headers = {**h, **({"Idempotency-Key": key} if key else {})}
    body = {"items": [{"item_id": item(client, n)["id"], "quantity": q} for n, q in lines]}
    if booking_id: body["booking_id"] = booking_id
    return client.post("/api/food/orders", headers=headers, json=body)


def pay_food(client, h, order_id, simulate=None, method="upi"):
    body = {"food_order_id": order_id, "method": method}
    if simulate: body["simulate"] = simulate
    return client.post("/api/payments/pay", headers=h, json=body)


def book(client, h, food=None, qty=2):
    m, cats = first_match(client)
    body = {"match_id": m["id"], "category_id": cats[0]["category_id"], "quantity": qty}
    if food: body["food"] = [{"item_id": item(client, n)["id"], "quantity": q} for n, q in food]
    r = client.post("/api/bookings", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_menu_items_carry_description_image_and_category(client):
    items = client.get("/api/food/items").json()
    assert len(items) == 21 and all(i["description"] and i["category"] and "image_url" in i for i in items)


def test_quote_is_priced_by_the_server_and_flags_unavailable_items(client):
    brownie = item(client, "Brownie")
    q = client.post("/api/food/quote", json={"items": [{"item_id": brownie["id"], "quantity": 2, "price": 1}]}).json()  # a client price is ignored
    assert q["lines"][0]["unit_price"] == 80.0 and (q["subtotal"], q["fee"], q["tax"], q["total"]) == (160.0, 0.0, 8.0, 168.0)
    q = client.post("/api/food/quote", json={"items": [{"item_id": brownie["id"], "quantity": 1}, {"item_id": 99999, "quantity": 1}]}).json()
    assert q["total"] == 84.0 and q["unavailable"][0]["item_id"] == 99999
    assert client.post("/api/food/quote", json={"items": []}).json()["total"] == 0.0
    assert client.post("/api/food/quote", json={"items": [{"item_id": brownie["id"], "quantity": 21}]}).status_code == 422


def test_booking_quote_itemises_food_and_ticket_prices_are_unchanged(client):
    m, cats = first_match(client)
    brownie = item(client, "Brownie")
    base = client.post("/api/bookings/quote", json={"match_id": m["id"], "category_id": cats[0]["category_id"], "quantity": 2}).json()
    q = client.post("/api/bookings/quote", json={"match_id": m["id"], "category_id": cats[0]["category_id"], "quantity": 2,
                                                 "food": [{"item_id": brownie["id"], "quantity": 2}]}).json()
    assert q["tickets"] == base["tickets"] and q["food"]["total"] == 168.0 and q["food_lines"][0]["name"] == "Brownie"
    assert q["grand_total"] == round(base["grand_total"] + 168.0, 2) and "food_fee_percent" in q["rates"]


def test_standalone_food_checkout_payment_confirmation_and_receipt(client):
    h = register(client)
    r = order(client, h, [("Brownie", 2), ("Tea", 1)])
    assert r.status_code == 201
    o = r.json()
    assert o["payment_status"] == "pending" and o["can_pay"] and o["total"] == 183.75 and o["pickup_code"] is None
    assert client.get(f"/api/food/orders/{o['id']}/receipt", headers=h).status_code == 409   # nothing to receipt before paying
    f = pay_food(client, h, o["id"], simulate="failure").json()
    assert f["outcome"] == "failed" and f["food_order"]["payment_status"] == "failed" and f["food_order"]["can_pay"]
    p = pay_food(client, h, o["id"], simulate="success")
    assert p.status_code == 200 and p.json()["outcome"] == "success" and p.json()["payment"]["simulated"] is True
    done = client.get(f"/api/food/orders/{o['id']}", headers=h).json()
    assert done["payment_status"] == "successful" and done["pickup_code"] and done["qr_image"].startswith("data:image/svg+xml")
    assert pay_food(client, h, o["id"]).json()["outcome"] == "already_paid"          # a repeat never charges twice
    pays = [x for x in client.get("/api/payments", headers=h).json() if x["food_order_id"] == o["id"] and x["status"] == "successful"]
    assert len(pays) == 1
    pdf = client.get(f"/api/food/orders/{o['id']}/receipt", headers=h)
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert client.post(f"/api/food/orders/{o['id']}/cancel", headers=h).status_code == 400   # paid orders are not cancelled here


def test_pay_input_accepts_exactly_one_target_and_no_card_data(client):
    h = register(client)
    o = order(client, h, [("Tea", 1)]).json()
    assert client.post("/api/payments/pay", headers=h, json={"method": "upi"}).status_code == 422
    assert client.post("/api/payments/pay", headers=h, json={"booking_id": 1, "food_order_id": o["id"], "method": "upi"}).status_code == 422
    assert client.post("/api/payments/pay", headers=h, json={"food_order_id": o["id"], "method": "credit_card", "card_number": "4111111111111111"}).status_code == 422


def test_food_orders_are_private_and_idempotent(client):
    h1, h2 = register(client), register(client, "u2@test.com")
    a = order(client, h1, [("Tea", 1)], key="k1")
    b = order(client, h1, [("Tea", 1)], key="k1")
    assert a.status_code == 201 and b.status_code == 200 and a.json()["id"] == b.json()["id"]
    assert len(client.get("/api/food/orders", headers=h1).json()) == 1
    oid = a.json()["id"]
    assert client.get(f"/api/food/orders/{oid}", headers=h2).status_code == 404
    assert pay_food(client, h2, oid).status_code == 404
    assert client.post(f"/api/food/orders/{oid}/cancel", headers=h2).status_code == 404
    assert client.get("/api/food/orders").status_code == 401


def test_unpaid_standalone_order_can_be_cancelled_and_then_not_paid(client):
    h = register(client)
    o = order(client, h, [("Tea", 1)]).json()
    assert client.post(f"/api/food/orders/{o['id']}/cancel", headers=h).json()["status"] == "cancelled"
    assert client.post(f"/api/food/orders/{o['id']}/cancel", headers=h).status_code == 409
    assert pay_food(client, h, o["id"]).status_code == 409


def test_food_during_ticket_booking_is_paid_together_with_the_tickets(client):
    h = register(client)
    b = book(client, h, food=[("Brownie", 2)])
    assert b["food_total"] == 168.0 and b["grand_total"] == round(b["ticket_total"] + 168.0, 2)
    extra = order(client, h, [("Tea", 1)], booking_id=b["id"]).json()           # more food while the booking is still unpaid
    assert extra["pay_with_booking"] and not extra["can_pay"]
    assert pay_food(client, h, extra["id"]).status_code == 409                    # must be paid with the tickets
    assert client.post(f"/api/food/orders/{extra['id']}/cancel", headers=h).status_code == 409
    p = client.post("/api/payments/pay", headers=h, json={"booking_id": b["id"], "method": "upi"}).json()
    assert p["outcome"] == "success" and p["payment"]["amount"] == round(b["ticket_total"] + 168.0 + extra["total"], 2)
    assert p["booking"]["food_due"] == 0.0 and p["booking"]["paid_amount"] == p["payment"]["amount"]
    assert len(client.get(f"/api/bookings/{b['id']}/tickets", headers=h).json()) == 2   # tickets still issued
    assert client.get(f"/api/food/orders/{extra['id']}", headers=h).json()["payment_status"] == "successful"
    assert len(client.get("/api/payments", headers=h).json()) == 1                       # a single payment covered everything


def test_food_after_the_booking_is_paid_has_its_own_payment_and_pickup_details(client):
    h = register(client)
    b = book(client, h)
    assert client.post("/api/payments/pay", headers=h, json={"booking_id": b["id"], "method": "upi"}).json()["outcome"] == "success"
    o = order(client, h, [("Coffee", 2)], booking_id=b["id"]).json()
    assert o["can_pay"] and not o["pay_with_booking"]
    assert client.get(f"/api/bookings/{b['id']}", headers=h).json()["food_due"] == o["total"]
    assert pay_food(client, h, o["id"]).json()["outcome"] == "success"
    done = client.get(f"/api/food/orders/{o['id']}", headers=h).json()
    assert done["pickup"]["booking_reference"] == b["reference"] and done["pickup"]["stadium"]
    assert len(client.get("/api/payments", headers=h).json()) == 2


def test_admin_can_describe_items_and_duplicates_and_bad_urls_are_rejected(client):
    ah, h = admin_headers(client), register(client)
    ok = client.post("/api/admin/food/items", headers=ah, json={"category_id": 1, "name": "Cake", "price": 70, "description": "Rich cake",
                                                                 "image_url": "https://example.com/cake.jpg"})
    assert ok.status_code == 201 and ok.json()["description"] == "Rich cake" and ok.json()["image_url"].endswith("cake.jpg")
    assert client.post("/api/admin/food/items", headers=ah, json={"category_id": 1, "name": "cake", "price": 70}).status_code == 409
    assert client.post("/api/admin/food/items", headers=ah, json={"category_id": 1, "name": "Bad", "price": 1, "image_url": "javascript:alert(1)"}).status_code == 422
    r = client.put(f"/api/admin/food/items/{ok.json()['id']}", headers=ah, json={"is_active": False})
    assert r.status_code == 200
    q = client.post("/api/food/quote", json={"items": [{"item_id": ok.json()["id"], "quantity": 1}]}).json()
    assert q["lines"] == [] and q["unavailable"][0]["name"] == "Cake" and q["total"] == 0.0   # disabled items are never charged
