import re
from datetime import datetime
from decimal import Decimal
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pymongo.errors import DuplicateKeyError
from ..clock import now as clock_now
from ..database import get_db, next_id, d128, dec
from ..deps import require_admin
from ..schemas import (SportIn, StadiumIn, StadiumUpdate, MatchIn, MatchUpdate, CategoryIn, CategoryUpdate,
                       InventoryIn, FoodCategoryIn, FoodItemIn, FoodItemUpdate, UserUpdate)
from ..services import bookings_out, matches_out, stadium_out, format_timing, time_str, d
from .food import items_out

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _insert(db, coll: str, doc: dict, dup_msg="Duplicate or invalid reference"):
    doc["_id"] = next_id(db, coll)
    try:
        db[coll].insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, dup_msg)
    return doc


def _update(db, coll: str, id_, fields: dict, dup_msg="Duplicate or invalid reference"):
    if not fields:
        return
    try:
        db[coll].update_one({"_id": id_}, {"$set": fields})
    except DuplicateKeyError:
        raise HTTPException(409, dup_msg)


def _get(db, coll: str, id_, name):
    obj = db[coll].find_one({"_id": id_})
    if not obj:
        raise HTTPException(404, f"{name} not found")
    return obj


def _not_null(data: dict, nullable=()):
    """The SQL schema had NOT NULL columns; an explicit JSON null must not erase them."""
    return {k: v for k, v in data.items() if v is not None or k in nullable}


def _sum_money(db, coll: str, flt: dict) -> Decimal:
    return sum((dec(x["total"]) for x in db[coll].find(flt, {"total": 1})), Decimal(0))


@router.get("/dashboard")
def dashboard(db=Depends(get_db)):
    tickets = list(db.bookings.aggregate([{"$match": {"status": "confirmed"}}, {"$group": {"_id": None, "n": {"$sum": "$quantity"}}}]))
    revenue = (_sum_money(db, "bookings", {"status": "confirmed", "payment_status": "successful"})
               + _sum_money(db, "food_orders", {"status": "placed", "payment_status": "successful"}))
    return {"users": db.users.count_documents({}), "matches": db.matches.count_documents({}),
            "stadiums": db.stadiums.count_documents({}), "bookings": db.bookings.count_documents({}),
            "confirmed_bookings": db.bookings.count_documents({"status": "confirmed"}),
            "cancelled_bookings": db.bookings.count_documents({"status": "cancelled"}),
            "tickets_booked": tickets[0]["n"] if tickets else 0,
            "food_orders": db.food_orders.count_documents({"status": "placed"}),
            "tickets_issued": db.tickets.count_documents({}), "tickets_used": db.tickets.count_documents({"ticket_status": "used"}),
            "revenue_demo": d(revenue),
            "revenue_note": "Demo payments only; no real money was collected."}


# ---- sports / stadiums ----
@router.post("/sports", status_code=201)
def add_sport(body: SportIn, db=Depends(get_db)):
    s = _insert(db, "sports", {"name": body.name.strip(), "description": body.description}, "Sport already exists")
    return {"id": s["_id"], "name": s["name"]}


@router.put("/sports/{sport_id}")
def edit_sport(sport_id: int, body: SportIn, db=Depends(get_db)):
    _get(db, "sports", sport_id, "Sport")
    _update(db, "sports", sport_id, {"name": body.name.strip(), "description": body.description}, "Sport already exists")
    return {"id": sport_id, "name": body.name.strip()}


@router.post("/stadiums", status_code=201)
def add_stadium(body: StadiumIn, db=Depends(get_db)):
    _get(db, "sports", body.sport_id, "Sport")
    s = _insert(db, "stadiums", body.model_dump(), "Stadium name already exists")
    return stadium_out(db, s)


@router.put("/stadiums/{stadium_id}")
def edit_stadium(stadium_id: int, body: StadiumUpdate, db=Depends(get_db)):
    _get(db, "stadiums", stadium_id, "Stadium")
    data = _not_null(body.model_dump(exclude_unset=True), nullable=("image_url",))
    if "sport_id" in data:
        _get(db, "sports", data["sport_id"], "Sport")
    _update(db, "stadiums", stadium_id, data, "Stadium name already exists")
    return stadium_out(db, db.stadiums.find_one({"_id": stadium_id}))


# ---- matches ----
def _create_inventory(db, match: dict, stadium: dict, per_category: Optional[int] = None):
    cats = list(db.ticket_categories.find({"stadium_id": match["stadium_id"]}).sort("_id", 1))
    if not cats:
        return
    each = per_category if per_category is not None else stadium["capacity"] // len(cats)
    for c in cats:
        _insert(db, "ticket_inventory", {"match_id": match["_id"], "category_id": c["_id"], "total": each, "available": each})


@router.post("/matches", status_code=201)
def add_match(body: MatchIn, db=Depends(get_db)):
    st = _get(db, "stadiums", body.stadium_id, "Stadium")
    doc = {"stadium_id": body.stadium_id, "team_a": body.team_a, "team_b": body.team_b,
           "match_date": body.match_date.isoformat(), "start_time": time_str(body.start_time),
           "timing": body.timing or format_timing(body.start_time), "status": body.status}
    if body.tournament:   # optional field: only stored when an admin provides it
        doc["tournament"] = body.tournament
    m = _insert(db, "matches", doc)
    try:
        _create_inventory(db, m, st)
    except Exception:  # keep match + inventory all-or-nothing
        db.ticket_inventory.delete_many({"match_id": m["_id"]})
        db.matches.delete_one({"_id": m["_id"]})
        raise
    return matches_out(db, [m])[0]


@router.put("/matches/{match_id}")
def edit_match(match_id: int, body: MatchUpdate, db=Depends(get_db)):
    m = _get(db, "matches", match_id, "Match")
    data = _not_null(body.model_dump(exclude_unset=True), nullable=("tournament",))
    if "stadium_id" in data and data["stadium_id"] != m["stadium_id"]:
        raise HTTPException(400, "Moving a match to another stadium is not supported; create a new match instead")
    data.pop("stadium_id", None)
    if "start_time" in data:
        if "timing" not in data:
            data["timing"] = format_timing(data["start_time"])
        data["start_time"] = time_str(data["start_time"])
    if "match_date" in data:
        data["match_date"] = data["match_date"].isoformat()
    went_cancelled = data.get("status") == "cancelled" and m["status"] != "cancelled"
    _update(db, "matches", match_id, data)
    if went_cancelled:
        # Cancel every live booking for this match; refunds stay demo/pending.
        for b in db.bookings.find({"match_id": match_id, "status": {"$in": ["confirmed", "pending_payment"]}}):
            paid = b["payment_status"] == "successful"
            res = db.bookings.update_one({"_id": b["_id"], "status": {"$in": ["confirmed", "pending_payment"]}},
                                         {"$set": {"status": "cancelled", "cancelled_at": clock_now(),
                                                   "refund_status": "demo_refund_pending" if paid else "not_applicable",
                                                   "payment_status": "successful" if paid else "failed"}})
            if res.modified_count == 1:
                db.food_orders.update_many({"booking_id": b["_id"]}, {"$set": {"status": "cancelled"}})
        db.tickets.update_many({"match_id": match_id, "ticket_status": "valid"},
                               {"$set": {"ticket_status": "cancelled", "cancelled_at": clock_now()}})
    return matches_out(db, [db.matches.find_one({"_id": match_id})])[0]


@router.delete("/matches/{match_id}", status_code=204)
def delete_match(match_id: int, db=Depends(get_db)):
    _get(db, "matches", match_id, "Match")
    if db.bookings.count_documents({"match_id": match_id}, limit=1):
        raise HTTPException(409, "This match has bookings and cannot be deleted. Set its status to 'cancelled' instead.")
    db.ticket_inventory.delete_many({"match_id": match_id})
    db.match_seat_inventory.delete_many({"match_id": match_id})   # Phase 3A: seat inventory of this match (it has no bookings, see check above)
    db.matches.delete_one({"_id": match_id})


# ---- ticket categories / inventory ----
@router.post("/ticket-categories", status_code=201)
def add_category(body: CategoryIn, db=Depends(get_db)):
    _get(db, "stadiums", body.stadium_id, "Stadium")
    c = _insert(db, "ticket_categories", {"stadium_id": body.stadium_id, "name": body.name.strip(), "price": d128(body.price)},
                "Category already exists for this stadium")
    for m in db.matches.find({"stadium_id": body.stadium_id, "status": "scheduled"}, {"_id": 1}):
        _insert(db, "ticket_inventory", {"match_id": m["_id"], "category_id": c["_id"],
                                         "total": body.tickets_per_match, "available": body.tickets_per_match})
    return {"id": c["_id"], "name": c["name"], "price": d(c["price"])}


@router.put("/ticket-categories/{cat_id}")
def edit_category(cat_id: int, body: CategoryUpdate, db=Depends(get_db)):
    _get(db, "ticket_categories", cat_id, "Ticket category")
    data = _not_null(body.model_dump(exclude_unset=True))
    if "price" in data:
        data["price"] = d128(data["price"])
    if "name" in data:
        data["name"] = data["name"].strip()
    _update(db, "ticket_categories", cat_id, data, "Category already exists for this stadium")
    c = db.ticket_categories.find_one({"_id": cat_id})
    return {"id": c["_id"], "name": c["name"], "price": d(c["price"])}


@router.put("/inventory")
def set_inventory(body: InventoryIn, db=Depends(get_db)):
    """Compare-and-set: only applies if nobody booked/cancelled between our read and write, otherwise re-reads."""
    for _ in range(8):
        inv = db.ticket_inventory.find_one({"match_id": body.match_id, "category_id": body.category_id})
        if not inv:
            raise HTTPException(404, "Inventory row not found")
        sold = inv["total"] - inv["available"]
        if body.total < sold:
            raise HTTPException(400, f"{sold} tickets are already booked; total cannot be lower")
        res = db.ticket_inventory.update_one(
            {"_id": inv["_id"], "total": inv["total"], "available": inv["available"]},
            {"$set": {"total": body.total, "available": body.total - sold}})
        if res.matched_count == 1:
            return {"match_id": body.match_id, "category_id": body.category_id, "total": body.total, "available": body.total - sold}
    raise HTTPException(409, "Inventory is changing right now, please retry")


# ---- food ----
@router.post("/food/categories", status_code=201)
def add_food_category(body: FoodCategoryIn, db=Depends(get_db)):
    c = _insert(db, "food_categories", {"name": body.name.strip()}, "Category already exists")
    return {"id": c["_id"], "name": c["name"]}


@router.get("/food/items")
def all_food(db=Depends(get_db)):
    return items_out(db, list(db.food_items.find().sort([("category_id", 1), ("_id", 1)])))


def _food_duplicate(db, category_id: int, name: str, exclude_id=None) -> bool:
    """Same item name (case-insensitive) in the same category = a duplicate record."""
    want = name.strip().lower()
    return any(i["name"].strip().lower() == want and i["_id"] != exclude_id for i in db.food_items.find({"category_id": category_id}, {"name": 1}))


@router.post("/food/items", status_code=201)
def add_food(body: FoodItemIn, db=Depends(get_db)):
    _get(db, "food_categories", body.category_id, "Food category")
    if _food_duplicate(db, body.category_id, body.name):
        raise HTTPException(409, "This food item already exists in that category")
    data = body.model_dump()
    data["name"] = data["name"].strip()
    data["price"] = d128(data["price"])
    i = _insert(db, "food_items", data)
    return items_out(db, [i])[0]


@router.put("/food/items/{item_id}")
def edit_food(item_id: int, body: FoodItemUpdate, db=Depends(get_db)):
    _get(db, "food_items", item_id, "Food item")
    data = _not_null(body.model_dump(exclude_unset=True))
    if "category_id" in data:
        _get(db, "food_categories", data["category_id"], "Food category")
    if "price" in data:
        data["price"] = d128(data["price"])
    if "name" in data:
        data["name"] = data["name"].strip()
    if "name" in data or "category_id" in data:
        cur = db.food_items.find_one({"_id": item_id})
        if _food_duplicate(db, data.get("category_id", cur["category_id"]), data.get("name", cur["name"]), exclude_id=item_id):
            raise HTTPException(409, "This food item already exists in that category")
    _update(db, "food_items", item_id, data)
    return items_out(db, [db.food_items.find_one({"_id": item_id})])[0]


# ---- bookings / users ----
def _rx(text: str) -> dict:
    return {"$regex": re.escape(text), "$options": "i"}


@router.get("/bookings")
def all_bookings(q: Optional[str] = None, status: Optional[str] = None, limit: int = 100, offset: int = 0, db=Depends(get_db)):
    conds = []
    if q and q.strip():
        t = q.strip()
        uids = [u["_id"] for u in db.users.find({"$or": [{"email": _rx(t)}, {"name": _rx(t)}]}, {"_id": 1})]
        conds.append({"$or": [{"reference": {"$regex": re.escape(t.upper())}}, {"user_id": {"$in": uids}}]})
    if status in ("pending_payment", "confirmed", "cancelled", "expired"):
        conds.append({"status": status})
    flt = {"$and": conds} if conds else {}
    rows = list(db.bookings.find(flt).sort("_id", -1).skip(max(offset, 0)).limit(min(max(limit, 1), 200)))
    return bookings_out(db, rows, detail=True)


@router.get("/bookings/{booking_id}")
def one_booking(booking_id: int, db=Depends(get_db)):
    return bookings_out(db, [_get(db, "bookings", booking_id, "Booking")], detail=True)[0]


@router.get("/users")
def users(db=Depends(get_db)):
    return [{"id": u["_id"], "name": u["name"], "email": u["email"], "phone": u.get("phone"), "role": u["role"],
             "is_active": u["is_active"]} for u in db.users.find().sort("_id", 1)]


@router.put("/users/{user_id}")
def edit_user(user_id: int, body: UserUpdate, admin: dict = Depends(require_admin), db=Depends(get_db)):
    u = _get(db, "users", user_id, "User")
    if u["_id"] == admin["_id"]:
        raise HTTPException(400, "You cannot change your own role or active status")
    _update(db, "users", user_id, _not_null(body.model_dump(exclude_unset=True)))
    u = db.users.find_one({"_id": user_id})
    return {"id": u["_id"], "role": u["role"], "is_active": u["is_active"]}
