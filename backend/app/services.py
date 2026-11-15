import hashlib
import hmac
import re
from collections import defaultdict
from datetime import datetime, time, date
from decimal import Decimal
from fastapi import HTTPException
from .clock import now as clock_now
from .config import settings
from .database import dec


def parse_start_time(timing: str):
    m = re.match(r"\s*(\d{1,2}):(\d{2})\s*(am|pm)", timing or "", re.I)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3).lower()
    if ap == "pm" and h != 12: h += 12
    if ap == "am" and h == 12: h = 0
    return time(h, mi) if h < 24 and mi < 60 else None


def format_timing(start: time) -> str:
    return start.strftime("%I:%M %p").lstrip("0").lower().replace(" ", "")


def time_str(t) -> str:
    return t.replace(microsecond=0).isoformat()


def match_start(m: dict):
    return time.fromisoformat(m["start_time"]) if m.get("start_time") else None


def match_datetime(m: dict) -> datetime:
    return datetime.combine(date.fromisoformat(m["match_date"]), match_start(m) or time(0, 0))


def d(x):
    return float(dec(x)) if x is not None else None


# ---------- batch loaders (avoid one query per row) ----------
def match_context(db, matches: list) -> dict:
    st_ids = list({m["stadium_id"] for m in matches})
    stadiums = {s["_id"]: s for s in db.stadiums.find({"_id": {"$in": st_ids}})}
    sports = {s["_id"]: s for s in db.sports.find({"_id": {"$in": list({s["sport_id"] for s in stadiums.values()})}})}
    inv = defaultdict(list)
    for i in db.ticket_inventory.find({"match_id": {"$in": [m["_id"] for m in matches]}}).sort("_id", 1):
        inv[i["match_id"]].append(i)
    cat_ids = list({i["category_id"] for rows in inv.values() for i in rows})
    cats = {c["_id"]: c for c in db.ticket_categories.find({"_id": {"$in": cat_ids}})}
    return {"stadiums": stadiums, "sports": sports, "inventory": inv, "categories": cats}


def match_out(m: dict, ctx: dict) -> dict:
    st = ctx["stadiums"][m["stadium_id"]]
    sp = ctx["sports"][st["sport_id"]]
    inv = ctx["inventory"].get(m["_id"], [])
    avail = sum(i["available"] for i in inv)
    prices = [dec(ctx["categories"][i["category_id"]]["price"]) for i in inv]
    start = match_start(m)
    return {
        "id": m["_id"], "team_a": m["team_a"], "team_b": m["team_b"], "title": f'{m["team_a"]} vs {m["team_b"]}',
        "date": m["match_date"], "start_time": start.strftime("%H:%M") if start else None,
        "timing": m["timing"], "status": m["status"],
        "stadium": {"id": st["_id"], "name": st["name"], "location": st["location"],
                    "capacity": st["capacity"], "image_url": st.get("image_url")},
        "sport": {"id": sp["_id"], "name": sp["name"]},
        "tickets_available": avail, "sold_out": avail == 0,
        "min_price": d(min(prices)) if prices else None,
        "max_price": d(max(prices)) if prices else None,
        "tournament": m.get("tournament") or None,   # optional field: only matches an admin has labelled have one
    }


def matches_out(db, matches: list) -> list:
    if not matches:
        return []
    ctx = match_context(db, matches)
    return [match_out(m, ctx) for m in matches]


def stadiums_out(db, stadiums: list, upcoming=None) -> list:
    sports = {s["_id"]: s for s in db.sports.find({"_id": {"$in": list({s["sport_id"] for s in stadiums})}})}
    cats = defaultdict(list)
    for c in db.ticket_categories.find({"stadium_id": {"$in": [s["_id"] for s in stadiums]}}).sort("_id", 1):
        cats[c["stadium_id"]].append(c)
    out = []
    for s in stadiums:
        sp = sports[s["sport_id"]]
        o = {"id": s["_id"], "name": s["name"], "location": s["location"], "capacity": s["capacity"],
             "image_url": s.get("image_url"), "sport": {"id": sp["_id"], "name": sp["name"]},
             "categories": [{"id": c["_id"], "name": c["name"], "price": d(c["price"])} for c in cats[s["_id"]]]}
        if upcoming is not None:
            o["upcoming_matches"] = upcoming
        out.append(o)
    return out


def stadium_out(db, s: dict, upcoming=None) -> dict:
    return stadiums_out(db, [s], upcoming)[0]


def _merge_food_lines(lines) -> dict:
    merged = {}
    for ln in lines:
        merged[ln.item_id] = merged.get(ln.item_id, 0) + ln.quantity
    return merged


def resolve_food(db, lines):
    """Merge duplicate lines, validate items are active. Returns list of (food_item_doc, qty). Strict: raises 400."""
    out = []
    for item_id, qty in _merge_food_lines(lines).items():
        item = db.food_items.find_one({"_id": item_id})
        if not item or not item.get("is_active"):
            who = f'"{item["name"]}"' if item else f"item {item_id}"
            raise HTTPException(400, f"Food {who} is not available")
        if qty > 50:
            raise HTTPException(400, "Too many of one food item")
        out.append((item, qty))
    return out


def split_food(db, lines):
    """Lenient version used for cart quotes: returns (available [(item_doc, qty)], unavailable [dict]).
    Unavailable items are reported (so the UI can show them) but are never priced."""
    merged = _merge_food_lines(lines)
    docs = {i["_id"]: i for i in db.food_items.find({"_id": {"$in": list(merged)}})} if merged else {}
    available, unavailable = [], []
    for item_id, qty in merged.items():
        if qty > 50:
            raise HTTPException(400, "Too many of one food item")
        item = docs.get(item_id)
        if item and item.get("is_active"):
            available.append((item, qty))
        else:
            unavailable.append({"item_id": item_id, "name": item["name"] if item else None, "quantity": qty,
                                "reason": "no_longer_available" if item else "not_found"})
    return available, unavailable


def food_order_doc_items(lines):
    from .database import d128
    return [{"food_item_id": i["_id"], "name": i["name"], "unit_price": d128(dec(i["price"])), "quantity": q} for i, q in lines]


# ---------- food order identity (derived, never stored, so old orders get them too) ----------
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I


def order_number(order_id: int) -> str:
    return f"FO{int(order_id):06d}"


def pickup_code(order_id: int) -> str:
    """6-character code shown at the food counter. HMAC of the order id with the server secret: stable, not guessable."""
    mac = hmac.new(settings.TICKET_SIGNING_KEY.encode(), f"food-pickup:{int(order_id)}".encode(), hashlib.sha256).digest()
    return "".join(_CODE_ALPHABET[b % 32] for b in mac[:6])


def iso(v):
    return v.isoformat() if v else None


def food_order_out(o: dict) -> dict:
    """Plain order view (no database access). `food_orders_out` in food_service adds pickup details, QR and action flags."""
    paid = o["status"] == "placed" and o["payment_status"] == "successful"
    return {"id": o["_id"], "order_number": order_number(o["_id"]), "booking_id": o.get("booking_id"), "status": o["status"],
            "payment_status": o["payment_status"], "subtotal": d(o["subtotal"]), "fee": d(o.get("fee") or 0), "tax": d(o["tax"]),
            "total": d(o["total"]), "created_at": iso(o.get("created_at")), "paid_at": iso(o.get("paid_at")),
            "transaction_id": o.get("payment_ref"), "payment_method": o.get("payment_method"),
            "pickup_code": pickup_code(o["_id"]) if paid else None,  # only a paid order can be collected
            "items": [{"name": i["name"], "unit_price": d(i["unit_price"]), "quantity": i["quantity"],
                       "line_total": d(dec(i["unit_price"]) * i["quantity"])} for i in o.get("items", [])]}


def bookings_out(db, bookings: list, detail=False) -> list:
    if not bookings:
        return []
    matches = {m["_id"]: m for m in db.matches.find({"_id": {"$in": list({b["match_id"] for b in bookings})}})}
    ctx = match_context(db, list(matches.values()))
    cats = {c["_id"]: c for c in db.ticket_categories.find({"_id": {"$in": list({b["category_id"] for b in bookings})}})}
    orders = defaultdict(list)
    for o in db.food_orders.find({"booking_id": {"$in": [b["_id"] for b in bookings]}}).sort("_id", 1):
        orders[o["booking_id"]].append(o)
    tix = defaultdict(list)
    for t in db.tickets.find({"booking_id": {"$in": [b["_id"] for b in bookings]}}, {"booking_id": 1, "ticket_id": 1, "seq": 1}).sort("seq", 1):
        tix[t["booking_id"]].append(t["ticket_id"])
    users = {}
    if detail:
        users = {u["_id"]: u for u in db.users.find({"_id": {"$in": list({b["user_id"] for b in bookings})}})}
    result = []
    for b in bookings:
        m = matches[b["match_id"]]
        st = ctx["stadiums"][m["stadium_id"]]
        sp = ctx["sports"][st["sport_id"]]
        live = [o for o in orders[b["_id"]] if o["status"] != "cancelled"]
        food_total = sum((dec(o["total"]) for o in live), Decimal(0))
        food_paid = sum((dec(o["total"]) for o in live if o["payment_status"] == "successful"), Decimal(0))
        out = {
            "id": b["_id"], "reference": b["reference"], "status": b["status"], "payment_status": b["payment_status"],
            "refund_status": b.get("refund_status"), "demo_payment": True,
            "sport": sp["name"], "match": f'{m["team_a"]} vs {m["team_b"]}', "match_id": m["_id"],
            "stadium": st["name"], "location": st["location"],
            "match_date": m["match_date"], "timing": m["timing"],
            "category": (", ".join(dict.fromkeys(x["category"] for x in b["seats"])) if b.get("booking_type") == "seat" else cats[b["category_id"]]["name"]),
            "booking_type": b.get("booking_type", "category"),
            "seats": [{"seat_id": x["seat_id"], "seat_key": x["seat_key"], "stand": x["stand"], "section": x["section"], "row": x["row"], "number": x["number"],
                       "category": x["category"], "price": d(x["unit_price"])} for x in b.get("seats", [])],
            "quantity": b["quantity"], "unit_price": d(b["unit_price"]),
            "subtotal": d(b["subtotal"]), "fee": d(b["fee"]), "tax": d(b["tax"]), "ticket_total": d(b["total"]),
            "food_total": d(food_total), "grand_total": d(dec(b["total"]) + food_total),
            "paid_amount": d(b["paid_amount"]) if b.get("paid_amount") is not None else None,  # amount of the booking payment itself
            "food_paid_total": d(food_paid), "food_due": d(food_total - food_paid) if b["status"] == "confirmed" else 0.0,
            "created_at": iso(b.get("created_at")), "cancelled_at": iso(b.get("cancelled_at")),
            "expires_at": iso(b.get("expires_at")),
            "expires_in_seconds": max(0, int((b["expires_at"] - clock_now()).total_seconds())) if b.get("expires_at") else None,
            "paid_at": iso(b.get("paid_at")),
            "transaction_id": b.get("payment_ref"), "payment_method": b.get("payment_method"),
            "ticket_count": len(tix[b["_id"]]), "ticket_ids": tix[b["_id"]],
        }
        if detail:
            u = users.get(b["user_id"], {})
            out["food_orders"] = [food_order_out(o) for o in orders[b["_id"]]]
            out["user"] = {"id": b["user_id"], "name": u.get("name"), "email": u.get("email")}
        result.append(out)
    return result


def booking_out(db, b: dict, detail=False) -> dict:
    return bookings_out(db, [b], detail)[0]
