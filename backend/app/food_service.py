"""Food billing and food-order presentation (pickup details, QR, which actions are allowed).

No FastAPI imports in the logic below besides what `services` already needs. Prices ALWAYS come from MongoDB here;
the browser only ever sends item ids and quantities.

Order lifecycle (reuses the existing `food_orders` documents, nothing is migrated):
  status          placed | cancelled
  payment_status  pending | failed | successful
A food order is paid in exactly one of two ways:
  * together with its booking's tickets (order attached to a booking that is still `pending_payment`)
  * on its own via ticketing.process_food_payment (standalone order, or order added to an already-paid booking)
"""
from decimal import Decimal
from .config import settings
from .database import dec
from .pricing import food_quote
from .qr import qr_data_uri
from .services import split_food, food_order_out, pickup_code, d

FOOD_QR_PREFIX = "BMMF:"


def food_rates() -> dict:
    return {"food_tax_percent": float(settings.FOOD_TAX_PERCENT), "food_fee_percent": float(settings.FOOD_FEE_PERCENT)}


def bill_for(available) -> dict:
    """available: [(food_item_doc, qty)] -> subtotal / fee / tax / total as Decimals (the single place food is priced)."""
    return food_quote([(dec(i["price"]), q) for i, q in available], settings.FOOD_TAX_PERCENT, settings.FOOD_FEE_PERCENT)


def lines_out(db, available) -> list:
    cats = {c["_id"]: c["name"] for c in db.food_categories.find({"_id": {"$in": list({i["category_id"] for i, _ in available})}})} if available else {}
    return [{"item_id": i["_id"], "name": i["name"], "category": cats.get(i["category_id"]), "description": i.get("description"),
             "image_url": i.get("image_url"), "unit_price": d(i["price"]), "quantity": q, "line_total": d(dec(i["price"]) * q),
             "available": True} for i, q in available]


def quote_cart(db, lines) -> dict:
    """Itemised, backend-authoritative bill for a cart. Unavailable items are listed separately and NOT charged."""
    available, unavailable = split_food(db, lines)
    fq = bill_for(available)
    return {"lines": lines_out(db, available), "unavailable": unavailable, "item_count": sum(q for _, q in available),
            **{k: d(v) for k, v in fq.items()}, "rates": food_rates(), "currency": "INR"}


# ---------------------------------------------------------------- order presentation
def _pickup(booking: dict | None, match: dict | None, stadium: dict | None) -> dict:
    if booking and match and stadium:
        return {"type": "stadium_counter", "stadium": stadium["name"], "location": stadium["location"],
                "match": f'{match["team_a"]} vs {match["team_b"]}', "match_date": match["match_date"], "timing": match["timing"],
                "booking_reference": booking["reference"],
                "instructions": f'Collect at the food counter at {stadium["name"]} on match day. Show your pickup code or QR.'}
    return {"type": "stadium_counter", "stadium": None, "location": None, "match": None, "match_date": None, "timing": None,
            "booking_reference": None,
            "instructions": "Collect at the stadium food counter. Show your pickup code or QR."}


def food_orders_out(db, orders: list, with_qr: bool = False) -> list:
    """Detailed order views: plain order + pickup details + payment flags (+ QR for paid orders when with_qr)."""
    if not orders:
        return []
    bookings = {b["_id"]: b for b in db.bookings.find({"_id": {"$in": list({o["booking_id"] for o in orders if o.get("booking_id")})}})}
    matches = {m["_id"]: m for m in db.matches.find({"_id": {"$in": list({b["match_id"] for b in bookings.values()})}})}
    stadiums = {s["_id"]: s for s in db.stadiums.find({"_id": {"$in": list({m["stadium_id"] for m in matches.values()})}})}
    out = []
    for o in orders:
        o_out = food_order_out(o)
        b = bookings.get(o.get("booking_id"))
        m = matches.get(b["match_id"]) if b else None
        st = stadiums.get(m["stadium_id"]) if m else None
        live = o["status"] == "placed"
        paid = live and o["payment_status"] == "successful"
        unpaid = live and not paid
        pay_with_booking = unpaid and b is not None and b["status"] == "pending_payment"
        # payable on its own: standalone, or the booking it belongs to is already confirmed
        can_pay = unpaid and (o.get("booking_id") is None or (b is not None and b["status"] == "confirmed"))
        o_out.update({
            "booking_reference": b["reference"] if b else None,
            "pickup": _pickup(b, m, st) if live else None,
            "pay_with_booking": pay_with_booking, "can_pay": can_pay, "can_cancel": can_pay,
            "receipt_available": paid, "rates": food_rates(), "demo_payment": True,
        })
        if with_qr and paid:
            payload = f"{FOOD_QR_PREFIX}{o['_id']}:{pickup_code(o['_id'])}"
            o_out["qr_payload"], o_out["qr_image"] = payload, qr_data_uri(payload)
        out.append(o_out)
    return out


def food_order_detail(db, order: dict, with_qr: bool = False) -> dict:
    return food_orders_out(db, [order], with_qr)[0]


def order_amount(order: dict) -> Decimal:
    return dec(order["total"])
