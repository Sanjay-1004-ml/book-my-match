"""Phase 3A: stadium seating service (layouts, categories, stands, sections, rows, seats, per-match inventory, pricing).

Deliberately free of FastAPI/pydantic: it takes a MongoDB database object and plain dicts and raises SeatingError, which
main.py turns into an HTTP response. Routes are thin wrappers, so this logic is unit-testable on its own.

NOT in this module (Phase 3B/3C, see docs/PHASE_3A_SEATING.md): holding/locking seats, hold expiry, confirming seats after payment.
Inventory rows only ever move between "available" and "blocked" here; "held" and "booked" are reserved values nothing sets yet,
but every destructive operation already refuses to touch a seat in those states.
"""
import re
from decimal import Decimal, InvalidOperation
from pymongo.errors import BulkWriteError, DuplicateKeyError
from .clock import now as clock_now
from .config import settings
from .database import d128, dec, next_id, next_ids
from .pricing import seat_quote

STATUSES = ("available", "held", "booked", "blocked")
LOCKED = ["held", "booked"]            # seats in these states must never be deleted, moved or re-priced away
MAX_PRICE = Decimal("1000000")
CHUNK = 1000
_CODE = re.compile(r"^[A-Z0-9]{1,12}$")
_LABEL = re.compile(r"^[A-Z0-9]{1,8}$")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


class SeatingError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _bad(msg, status=422):
    raise SeatingError(status, msg)


# ------------------------------------------------------------------ validation helpers
def clean_name(v, label="Name", lo=2, hi=60):
    v = " ".join(str(v or "").split())
    if not (lo <= len(v) <= hi):
        _bad(f"{label} must be {lo}-{hi} characters")
    return v


def slug_code(text, maxlen=12):
    return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())[:maxlen]


def clean_code(v, label="Code"):
    v = slug_code(v) if v is not None else ""
    if not _CODE.match(v):
        _bad(f"{label} must be 1-12 letters or digits")
    return v


def clean_label(v):
    v = re.sub(r"\s+", "", str(v or "")).upper()
    if not _LABEL.match(v):
        _bad("Row label must be 1-8 letters or digits (for example A1)")
    return v


def clean_price(v, label="Price"):
    try:
        p = Decimal(str(v))
    except (InvalidOperation, ValueError):
        _bad(f"{label} must be a number")
    if not p.is_finite() or p < 0 or p > MAX_PRICE:
        _bad(f"{label} must be between 0 and {MAX_PRICE}")
    if p != p.quantize(Decimal("0.01")):
        _bad(f"{label} can have at most 2 decimal places")
    return p


def clean_int(v, label, lo, hi):
    if isinstance(v, bool) or not isinstance(v, int) or not (lo <= v <= hi):
        _bad(f"{label} must be a whole number between {lo} and {hi}")
    return v


def clean_color(v):
    if v in (None, ""):
        return None
    if not _COLOR.match(str(v)):
        _bad("Colour must look like #22c55e")
    return str(v).lower()


def f(x):
    return float(dec(x)) if x is not None else None


def _get(db, coll, id_, label):
    doc = db[coll].find_one({"_id": id_})
    if not doc:
        raise SeatingError(404, f"{label} not found")
    return doc


def _dup(fn, msg):
    try:
        return fn()
    except DuplicateKeyError:
        raise SeatingError(409, msg)


def _insert(db, coll, doc, dup_msg):
    doc["_id"] = next_id(db, coll)
    _dup(lambda: db[coll].insert_one(doc), dup_msg)
    return doc


# ------------------------------------------------------------------ scopes and guards
_SCOPE = {"layout": "layout_id", "stand": "stand_id", "section": "section_id", "row": "row_id"}


def _scope(kind, id_):
    """(filter on seats, filter on match_seat_inventory) selecting everything below a layout/stand/section/row/seat."""
    if kind == "seat":
        return {"_id": id_}, {"seat_id": id_}
    k = _SCOPE[kind]
    return {k: id_}, {k: id_}


def sweep_expired_holds(db, match_id=None, at=None):
    """Return seats whose hold ran out to 'available' (housekeeping only: the lock query in seat_booking ALSO checks hold_expires_at,
    so correctness never depends on this having run). Only rows still marked held with an expired timestamp are touched."""
    flt = {"status": "held", "hold_expires_at": {"$lte": at or clock_now()}}
    if match_id is not None:
        flt["match_id"] = match_id
    return db.match_seat_inventory.update_many(flt, {"$set": {"status": "available"}, "$unset": {"booking_id": "", "hold_user_id": "", "hold_expires_at": ""}}).modified_count


def _guard_removable(db, inv_filter, what):
    sweep_expired_holds(db)
    n = db.match_seat_inventory.count_documents({**inv_filter, "status": {"$in": LOCKED}})
    if n:
        raise SeatingError(409, f"{what} has {n} seat(s) that are held or booked for a match and cannot be removed or changed")


def _delete_seats(db, kind, id_):
    seat_f, inv_f = _scope(kind, id_)
    _guard_removable(db, inv_f, "This " + kind)
    db.match_seat_inventory.delete_many(inv_f)   # inventory first: a crash in between leaves seats without inventory (re-synced), never inventory without seats
    db.seats.delete_many(seat_f)


def _purge_layout(db, layout_id):
    for coll in ("match_seat_inventory", "seats", "seat_rows", "seat_sections", "seat_stands", "seat_categories"):
        db[coll].delete_many({"layout_id": layout_id})
    db.seat_layouts.delete_one({"_id": layout_id})


# ------------------------------------------------------------------ layouts
def _seat_total(db, layout_id):
    return db.seats.count_documents({"layout_id": layout_id})


def layout_totals(db, layout_id):
    q = {"layout_id": layout_id}
    seats = db.seats.count_documents(q)
    active = db.seats.count_documents({**q, "is_active": True})
    return {"stands": db.seat_stands.count_documents(q), "sections": db.seat_sections.count_documents(q),
            "active_sections": db.seat_sections.count_documents({**q, "is_active": True}),
            "rows": db.seat_rows.count_documents(q), "seats": seats, "active_seats": active,
            "categories": db.seat_categories.count_documents(q),
            "matches_assigned": db.matches.count_documents({"seat_layout_id": layout_id})}


def layout_out(db, lay, totals=True):
    st = db.stadiums.find_one({"_id": lay["stadium_id"]}) or {}
    o = {"id": lay["_id"], "stadium_id": lay["stadium_id"], "stadium_name": st.get("name"), "stadium_capacity": st.get("capacity"),
         "name": lay["name"], "is_active": lay["is_active"], "created_at": lay.get("created_at"), "updated_at": lay.get("updated_at")}
    if totals:
        o["totals"] = layout_totals(db, lay["_id"])
        o["capacity_matches_stadium"] = st.get("capacity") == o["totals"]["seats"]   # informational only, never enforced
    return o


def list_layouts(db, stadium_id=None):
    flt = {"stadium_id": stadium_id} if stadium_id else {}
    return [layout_out(db, lay) for lay in db.seat_layouts.find(flt).sort("_id", 1)]


def validate_layout_spec(spec):
    """Validate a nested layout spec completely BEFORE anything is written; returns a normalised copy."""
    cats, seen_cat = [], set()
    for c in spec.get("categories") or []:
        name = clean_name(c.get("name"), "Category name", 1, 50)
        if name.lower() in seen_cat:
            _bad(f"Duplicate category '{name}'")
        seen_cat.add(name.lower())
        cats.append({"name": name, "base_price": clean_price(c.get("base_price")), "color": clean_color(c.get("color")),
                     "description": (c.get("description") or None)})
    stands, codes, scodes, snames, total = [], set(), set(), set(), 0
    for st in spec.get("stands") or []:
        sname = clean_name(st.get("name"), "Stand name")
        if sname.lower() in snames:
            _bad(f"Duplicate stand '{sname}'")
        snames.add(sname.lower())
        scode = clean_code(st.get("code") or sname, "Stand code")
        if scode in scodes:
            _bad(f"Duplicate stand code '{scode}'")
        scodes.add(scode)
        secs, secnames = [], set()
        for sec in st.get("sections") or []:
            nm = clean_name(sec.get("name"), "Section name")
            if nm.lower() in secnames:
                _bad(f"Duplicate section '{nm}' in stand '{sname}'")
            secnames.add(nm.lower())
            code = clean_code(sec.get("code") or (scode + slug_code(nm)), "Section code")
            if code in codes:
                _bad(f"Duplicate section code '{code}' (give one of the sections an explicit code)")
            codes.add(code)
            cat = sec.get("category")
            if cat is None or str(cat).strip().lower() not in seen_cat:
                _bad(f"Section '{nm}' needs a category that is defined in 'categories'")
            rows, labels = [], set()
            for r in sec.get("rows") or []:
                label = clean_label(r.get("label"))
                if label in labels:
                    _bad(f"Duplicate row '{label}' in section '{nm}'")
                labels.add(label)
                cnt = clean_int(r.get("seat_count"), "Seat count", 1, settings.SEATING_MAX_SEATS_PER_ROW)
                start = clean_int(r.get("start_number", 1), "Start number", 1, 9999)
                if start + cnt - 1 > 9999:
                    _bad("Seat numbers cannot exceed 9999")
                rcat = r.get("category")
                if rcat is not None and str(rcat).strip().lower() not in seen_cat:
                    _bad(f"Row '{label}' uses an unknown category")
                total += cnt
                rows.append({"label": label, "seat_count": cnt, "start_number": start, "category": rcat})
            secs.append({"name": nm, "code": code, "category": cat, "rows": rows})
        stands.append({"name": sname, "code": scode, "sections": secs})
    if total > settings.SEATING_MAX_SEATS_PER_LAYOUT:
        _bad(f"A layout can have at most {settings.SEATING_MAX_SEATS_PER_LAYOUT} seats (this one has {total})")
    return {"categories": cats, "stands": stands, "total_seats": total}


def create_layout(db, stadium_id, name, spec=None, user_id=None):
    _get(db, "stadiums", stadium_id, "Stadium")
    name = clean_name(name, "Layout name", 2, 80)
    norm = validate_layout_spec(spec or {})
    if db.seat_layouts.find_one({"stadium_id": stadium_id}):
        raise SeatingError(409, "This stadium already has a seating layout; edit it instead")
    lay = _insert(db, "seat_layouts", {"stadium_id": stadium_id, "name": name, "is_active": True, "created_by": user_id,
                                       "created_at": clock_now(), "updated_at": clock_now()}, "This stadium already has a seating layout")
    try:
        cat_ids = {c["name"].lower(): create_category(db, lay["_id"], c)["id"] for c in norm["categories"]}
        for st in norm["stands"]:
            stand = _create_stand(db, lay, st["name"], st["code"])
            for sec in st["sections"]:
                section = _create_section(db, lay, stand, sec["name"], sec["code"], cat_ids[str(sec["category"]).strip().lower()])
                for r in sec["rows"]:
                    rc = cat_ids[str(r["category"]).strip().lower()] if r.get("category") else None
                    _create_row(db, lay, section, r["label"], r["seat_count"], r["start_number"], rc, sync=False)
    except Exception:
        _purge_layout(db, lay["_id"])   # all-or-nothing without needing multi-document transactions
        raise
    return layout_out(db, lay)


def update_layout(db, layout_id, data):
    lay = _get(db, "seat_layouts", layout_id, "Layout")
    upd = {}
    if data.get("name") is not None:
        upd["name"] = clean_name(data["name"], "Layout name", 2, 80)
    if data.get("is_active") is not None:
        if data["is_active"] is False and db.matches.count_documents({"seat_layout_id": layout_id, "status": "scheduled"}, limit=1):
            raise SeatingError(409, "Unassign this layout from its scheduled matches before deactivating it")
        upd["is_active"] = bool(data["is_active"])
    if upd:
        upd["updated_at"] = clock_now()
        db.seat_layouts.update_one({"_id": layout_id}, {"$set": upd})
    return layout_out(db, db.seat_layouts.find_one({"_id": layout_id}))


def delete_layout(db, layout_id):
    _get(db, "seat_layouts", layout_id, "Layout")
    if db.matches.count_documents({"seat_layout_id": layout_id}, limit=1):
        raise SeatingError(409, "This layout is assigned to matches. Unassign it from every match first.")
    _purge_layout(db, layout_id)


def layout_tree(db, layout_id):
    lay = _get(db, "seat_layouts", layout_id, "Layout")
    q = {"layout_id": layout_id}
    per_row = {r["_id"]: r["n"] for r in db.seats.aggregate([{"$match": q}, {"$group": {"_id": "$row_id", "n": {"$sum": 1}}}])}
    per_cat = {r["_id"]: r["n"] for r in db.seats.aggregate([{"$match": q}, {"$group": {"_id": "$category_id", "n": {"$sum": 1}}}])}
    cats = {c["_id"]: c for c in db.seat_categories.find(q).sort("_id", 1)}
    rows_by_sec = {}
    for r in db.seat_rows.find(q).sort([("sort_order", 1), ("_id", 1)]):
        rows_by_sec.setdefault(r["section_id"], []).append(
            {"id": r["_id"], "label": r["label"], "category_id": r.get("category_id"), "seat_count": per_row.get(r["_id"], 0),
             "start_number": r.get("start_number", 1)})
    secs_by_stand = {}
    for s in db.seat_sections.find(q).sort([("sort_order", 1), ("_id", 1)]):
        rows = rows_by_sec.get(s["_id"], [])
        secs_by_stand.setdefault(s["stand_id"], []).append(
            {"id": s["_id"], "name": s["name"], "code": s["code"], "category_id": s["category_id"], "is_active": s["is_active"],
             "rows": rows, "seat_count": sum(r["seat_count"] for r in rows)})
    stands = []
    for st in db.seat_stands.find(q).sort([("sort_order", 1), ("_id", 1)]):
        secs = secs_by_stand.get(st["_id"], [])
        stands.append({"id": st["_id"], "name": st["name"], "code": st["code"], "is_active": st["is_active"], "sections": secs,
                       "seat_count": sum(s["seat_count"] for s in secs)})
    out = layout_out(db, lay)
    out["categories"] = [category_out(c, per_cat.get(c["_id"], 0)) for c in cats.values()]
    out["stands"] = stands
    return out


# ------------------------------------------------------------------ categories (pricing)
def category_out(c, seats=None):
    o = {"id": c["_id"], "layout_id": c["layout_id"], "name": c["name"], "base_price": f(c["base_price"]), "color": c.get("color"),
         "description": c.get("description")}
    if seats is not None:
        o["seats"] = seats
    return o


def create_category(db, layout_id, data):
    _get(db, "seat_layouts", layout_id, "Layout")
    name = clean_name(data.get("name"), "Category name", 1, 50)
    c = _insert(db, "seat_categories", {"layout_id": layout_id, "name": name, "name_key": name.lower(),
                                        "base_price": d128(clean_price(data.get("base_price"), "Base price")),
                                        "color": clean_color(data.get("color")), "description": (data.get("description") or None),
                                        "created_at": clock_now()}, "A category with this name already exists in the layout")
    return category_out(c, 0)


def update_category(db, category_id, data):
    c = _get(db, "seat_categories", category_id, "Seat category")
    upd = {}
    if data.get("name") is not None:
        upd["name"] = clean_name(data["name"], "Category name", 1, 50)
        upd["name_key"] = upd["name"].lower()
    if data.get("base_price") is not None:
        upd["base_price"] = d128(clean_price(data["base_price"], "Base price"))
    if "color" in data:
        upd["color"] = clean_color(data["color"])
    if "description" in data:
        upd["description"] = data["description"] or None
    if upd:
        _dup(lambda: db.seat_categories.update_one({"_id": category_id}, {"$set": upd}), "A category with this name already exists in the layout")
    # Price changes apply to future quotes only: seats resolve their price live, and bookings (Phase 3D) must snapshot the price they paid.
    return category_out(db.seat_categories.find_one({"_id": category_id}), db.seats.count_documents({"category_id": category_id}))


def delete_category(db, category_id):
    _get(db, "seat_categories", category_id, "Seat category")
    if db.seats.count_documents({"category_id": category_id}, limit=1) or db.seat_sections.count_documents({"category_id": category_id}, limit=1):
        raise SeatingError(409, "This category is in use. Assign its sections and seats to another category first.")
    db.seat_categories.delete_one({"_id": category_id})


def _category_in_layout(db, category_id, layout_id):
    c = db.seat_categories.find_one({"_id": category_id, "layout_id": layout_id}) if isinstance(category_id, int) else None
    if not c:
        _bad("Unknown seat category for this layout")
    return c


def _recategorise(db, kind, id_, category_id):
    seat_f, inv_f = _scope(kind, id_)
    db.seats.update_many(seat_f, {"$set": {"category_id": category_id}})
    db.match_seat_inventory.update_many(inv_f, {"$set": {"category_id": category_id}})


# ------------------------------------------------------------------ stands
def _next_order(db, coll, flt):
    top = db[coll].find_one(flt, {"sort_order": 1}, sort=[("sort_order", -1)])
    return (top.get("sort_order", 0) if top else 0) + 1


def _create_stand(db, lay, name, code):
    return _insert(db, "seat_stands", {"layout_id": lay["_id"], "stadium_id": lay["stadium_id"], "name": name, "name_key": name.lower(),
                                       "code": code, "is_active": True, "sort_order": _next_order(db, "seat_stands", {"layout_id": lay["_id"]}),
                                       "created_at": clock_now()}, "A stand with this name or code already exists in the layout")


def stand_out(s):
    return {"id": s["_id"], "layout_id": s["layout_id"], "name": s["name"], "code": s["code"], "is_active": s["is_active"]}


def add_stand(db, layout_id, data):
    lay = _get(db, "seat_layouts", layout_id, "Layout")
    name = clean_name(data.get("name"), "Stand name")
    return stand_out(_create_stand(db, lay, name, clean_code(data.get("code") or name, "Stand code")))


def update_stand(db, stand_id, data):
    _get(db, "seat_stands", stand_id, "Stand")
    upd = {}
    if data.get("name") is not None:
        upd["name"] = clean_name(data["name"], "Stand name")
        upd["name_key"] = upd["name"].lower()
    if data.get("is_active") is not None:
        upd["is_active"] = bool(data["is_active"])
    if upd:
        _dup(lambda: db.seat_stands.update_one({"_id": stand_id}, {"$set": upd}), "A stand with this name already exists in the layout")
        if "is_active" in upd:   # a stand switch cascades to its sections (sales stop/start for the whole stand)
            for sec in db.seat_sections.find({"stand_id": stand_id}, {"_id": 1}):
                set_section_active(db, sec["_id"], upd["is_active"])
    return stand_out(db.seat_stands.find_one({"_id": stand_id}))


def delete_stand(db, stand_id):
    _get(db, "seat_stands", stand_id, "Stand")
    _delete_seats(db, "stand", stand_id)
    db.seat_rows.delete_many({"stand_id": stand_id})
    db.seat_sections.delete_many({"stand_id": stand_id})
    db.seat_stands.delete_one({"_id": stand_id})


# ------------------------------------------------------------------ sections
def _create_section(db, lay, stand, name, code, category_id):
    return _insert(db, "seat_sections", {"layout_id": lay["_id"], "stadium_id": lay["stadium_id"], "stand_id": stand["_id"], "name": name,
                                         "name_key": name.lower(), "code": code, "category_id": category_id, "is_active": True,
                                         "sort_order": _next_order(db, "seat_sections", {"stand_id": stand["_id"]}), "created_at": clock_now()},
                   "A section with this name or code already exists")


def section_out(s):
    return {"id": s["_id"], "layout_id": s["layout_id"], "stand_id": s["stand_id"], "name": s["name"], "code": s["code"],
            "category_id": s["category_id"], "is_active": s["is_active"]}


def add_section(db, stand_id, data):
    stand = _get(db, "seat_stands", stand_id, "Stand")
    lay = _get(db, "seat_layouts", stand["layout_id"], "Layout")
    name = clean_name(data.get("name"), "Section name")
    _category_in_layout(db, data.get("category_id"), lay["_id"])
    code = clean_code(data.get("code") or (stand["code"] + slug_code(name)), "Section code")
    return section_out(_create_section(db, lay, stand, name, code, data["category_id"]))


def set_section_active(db, section_id, active):
    db.seat_sections.update_one({"_id": section_id}, {"$set": {"is_active": bool(active)}})
    db.match_seat_inventory.update_many({"section_id": section_id}, {"$set": {"is_active": bool(active)}})
    if active:   # seats that were individually switched off stay off
        off = [s["_id"] for s in db.seats.find({"section_id": section_id, "is_active": False}, {"_id": 1})]
        if off:
            db.match_seat_inventory.update_many({"seat_id": {"$in": off}}, {"$set": {"is_active": False}})


def update_section(db, section_id, data):
    sec = _get(db, "seat_sections", section_id, "Section")
    upd = {}
    if data.get("name") is not None:
        upd["name"] = clean_name(data["name"], "Section name")
        upd["name_key"] = upd["name"].lower()
    if upd:
        _dup(lambda: db.seat_sections.update_one({"_id": section_id}, {"$set": upd}), "A section with this name already exists in the stand")
    if data.get("category_id") is not None:
        _category_in_layout(db, data["category_id"], sec["layout_id"])
        db.seat_sections.update_one({"_id": section_id}, {"$set": {"category_id": data["category_id"]}})
        _recategorise(db, "section", section_id, data["category_id"])   # overrides set on single rows/seats are replaced
    if data.get("is_active") is not None:
        set_section_active(db, section_id, data["is_active"])
    return section_out(db.seat_sections.find_one({"_id": section_id}))


def delete_section(db, section_id):
    _get(db, "seat_sections", section_id, "Section")
    _delete_seats(db, "section", section_id)
    db.seat_rows.delete_many({"section_id": section_id})
    db.seat_sections.delete_one({"_id": section_id})


# ------------------------------------------------------------------ rows and seats
def _seat_doc(sid, lay, section, row, number, category_id, now):
    return {"_id": sid, "layout_id": lay["_id"], "stadium_id": lay["stadium_id"], "stand_id": section["stand_id"], "section_id": section["_id"],
            "row_id": row["_id"], "row_label": row["label"], "seat_number": number, "seat_key": f'{section["code"]}-{row["label"]}-{number}',
            "category_id": category_id, "price_override": None, "is_active": True, "created_at": now}


def _insert_seats(db, docs):
    for i in range(0, len(docs), CHUNK):
        try:
            db.seats.insert_many(docs[i:i + CHUNK])
        except (BulkWriteError, DuplicateKeyError):
            raise SeatingError(409, "Duplicate seat: a seat with this number or key already exists")


def _check_capacity(db, layout_id, extra):
    if _seat_total(db, layout_id) + extra > settings.SEATING_MAX_SEATS_PER_LAYOUT:
        _bad(f"A layout can have at most {settings.SEATING_MAX_SEATS_PER_LAYOUT} seats")


def _inv_doc(match_id, seat, active, now):
    return {"_id": f'{match_id}:{seat["_id"]}', "match_id": match_id, "seat_id": seat["_id"], "layout_id": seat["layout_id"],
            "stadium_id": seat["stadium_id"], "stand_id": seat["stand_id"], "section_id": seat["section_id"], "row_id": seat["row_id"],
            "category_id": seat["category_id"], "status": "available", "is_active": bool(active and seat["is_active"]),
            "created_at": now}   # hold_id / hold_expires_at / booking_id are added by Phase 3B/3C when a seat is first held


def _insert_inventory(db, docs):
    """Insert-if-absent (unique _id): safe to repeat, never overwrites a row that already holds a status."""
    for i in range(0, len(docs), CHUNK):
        try:
            db.match_seat_inventory.insert_many(docs[i:i + CHUNK], ordered=False)
        except BulkWriteError as e:
            errs = (e.details or {}).get("writeErrors", [])
            if any(w.get("code") != 11000 for w in errs):
                raise
        except DuplicateKeyError:
            pass


def _sync_new_seats(db, layout_id, seats):
    """Give seats that were just added to a layout an inventory row in every match that already uses it."""
    if not seats:
        return
    sections = {}
    now = clock_now()
    for m in db.matches.find({"seat_layout_id": layout_id, "status": "scheduled", "seat_inventory_status": "ready"}, {"_id": 1}):
        docs = []
        for s in seats:
            if s["section_id"] not in sections:
                sections[s["section_id"]] = db.seat_sections.find_one({"_id": s["section_id"]})["is_active"]
            docs.append(_inv_doc(m["_id"], s, sections[s["section_id"]], now))
        _insert_inventory(db, docs)


def _create_row(db, lay, section, label, seat_count, start, category_id, sync=True):
    _check_capacity(db, lay["_id"], seat_count)
    cat = category_id if category_id is not None else section["category_id"]
    row = _insert(db, "seat_rows", {"layout_id": lay["_id"], "stand_id": section["stand_id"], "section_id": section["_id"], "label": label,
                                    "category_id": category_id, "start_number": start, "sort_order": _next_order(db, "seat_rows", {"section_id": section["_id"]}),
                                    "created_at": clock_now()}, f"Row {label} already exists in this section")
    try:
        ids, now = next_ids(db, "seats", seat_count), clock_now()
        docs = [_seat_doc(ids[i], lay, section, row, start + i, cat, now) for i in range(seat_count)]
        _insert_seats(db, docs)
    except Exception:
        db.seats.delete_many({"row_id": row["_id"]})
        db.seat_rows.delete_one({"_id": row["_id"]})
        raise
    if sync:
        _sync_new_seats(db, lay["_id"], docs)
    return row


def row_out(db, r):
    return {"id": r["_id"], "section_id": r["section_id"], "label": r["label"], "category_id": r.get("category_id"),
            "start_number": r.get("start_number", 1), "seat_count": db.seats.count_documents({"row_id": r["_id"]})}


def add_row(db, section_id, data):
    sec = _get(db, "seat_sections", section_id, "Section")
    lay = _get(db, "seat_layouts", sec["layout_id"], "Layout")
    label = clean_label(data.get("label"))
    cnt = clean_int(data.get("seat_count"), "Seat count", 1, settings.SEATING_MAX_SEATS_PER_ROW)
    start = clean_int(data.get("start_number", 1), "Start number", 1, 9999)
    if start + cnt - 1 > 9999:
        _bad("Seat numbers cannot exceed 9999")
    if data.get("category_id") is not None:
        _category_in_layout(db, data["category_id"], lay["_id"])
    return row_out(db, _create_row(db, lay, sec, label, cnt, start, data.get("category_id")))


def update_row(db, row_id, data):
    row = _get(db, "seat_rows", row_id, "Row")
    sec = _get(db, "seat_sections", row["section_id"], "Section")
    lay = _get(db, "seat_layouts", row["layout_id"], "Layout")
    if data.get("label") is not None:
        label = clean_label(data["label"])
        if label != row["label"]:
            _guard_removable(db, {"row_id": row_id}, "This row")
            _dup(lambda: db.seat_rows.update_one({"_id": row_id}, {"$set": {"label": label}}), f"Row {label} already exists in this section")
            for s in db.seats.find({"row_id": row_id}):
                db.seats.update_one({"_id": s["_id"]}, {"$set": {"row_label": label, "seat_key": f'{sec["code"]}-{label}-{s["seat_number"]}'}})
    if data.get("category_id") is not None:
        _category_in_layout(db, data["category_id"], lay["_id"])
        db.seat_rows.update_one({"_id": row_id}, {"$set": {"category_id": data["category_id"]}})
        _recategorise(db, "row", row_id, data["category_id"])
    if data.get("seat_count") is not None:
        _set_row_capacity(db, lay, sec, db.seat_rows.find_one({"_id": row_id}), clean_int(data["seat_count"], "Seat count", 1, settings.SEATING_MAX_SEATS_PER_ROW))
    return row_out(db, db.seat_rows.find_one({"_id": row_id}))


def _set_row_capacity(db, lay, sec, row, target):
    seats = list(db.seats.find({"row_id": row["_id"]}).sort("seat_number", 1))
    cur = len(seats)
    if target > cur:
        top = seats[-1]["seat_number"] if seats else row.get("start_number", 1) - 1
        if top + (target - cur) > 9999:
            _bad("Seat numbers cannot exceed 9999")
        _check_capacity(db, lay["_id"], target - cur)
        ids, now = next_ids(db, "seats", target - cur), clock_now()
        cat = row.get("category_id") or sec["category_id"]
        docs = [_seat_doc(ids[i], lay, sec, row, top + 1 + i, cat, now) for i in range(target - cur)]
        _insert_seats(db, docs)
        _sync_new_seats(db, lay["_id"], docs)
    elif target < cur:
        drop = [s["_id"] for s in seats[target:]]   # the highest-numbered seats go
        if db.match_seat_inventory.count_documents({"seat_id": {"$in": drop}, "status": {"$in": LOCKED}}, limit=1):
            raise SeatingError(409, "Some of the seats that would be removed are held or booked")
        db.match_seat_inventory.delete_many({"seat_id": {"$in": drop}})
        db.seats.delete_many({"_id": {"$in": drop}})


def delete_row(db, row_id):
    _get(db, "seat_rows", row_id, "Row")
    _delete_seats(db, "row", row_id)
    db.seat_rows.delete_one({"_id": row_id})


def seat_out(db_cats, s):
    cat = db_cats.get(s["category_id"])
    price = s.get("price_override") if s.get("price_override") is not None else (cat or {}).get("base_price")
    return {"id": s["_id"], "seat_key": s["seat_key"], "row_id": s["row_id"], "row_label": s["row_label"], "seat_number": s["seat_number"],
            "section_id": s["section_id"], "category_id": s["category_id"], "category": (cat or {}).get("name"),
            "price_override": f(s.get("price_override")), "base_price": f(price), "is_active": s["is_active"]}


def list_row_seats(db, row_id):
    row = _get(db, "seat_rows", row_id, "Row")
    cats = {c["_id"]: c for c in db.seat_categories.find({"layout_id": row["layout_id"]})}
    return [seat_out(cats, s) for s in db.seats.find({"row_id": row_id}).sort("seat_number", 1)]


def add_seat(db, row_id, data):
    """Add ONE individual seat to a row (duplicate numbers are rejected both here and by the unique index)."""
    row = _get(db, "seat_rows", row_id, "Row")
    sec = _get(db, "seat_sections", row["section_id"], "Section")
    lay = _get(db, "seat_layouts", row["layout_id"], "Layout")
    number = clean_int(data.get("seat_number"), "Seat number", 1, 9999)
    if db.seats.find_one({"row_id": row_id, "seat_number": number}):
        raise SeatingError(409, f"Seat {number} already exists in row {row['label']}")
    cat = data.get("category_id") or row.get("category_id") or sec["category_id"]
    _category_in_layout(db, cat, lay["_id"])
    _check_capacity(db, lay["_id"], 1)
    doc = _seat_doc(next_ids(db, "seats", 1)[0], lay, sec, row, number, cat, clock_now())
    if data.get("price_override") is not None:
        doc["price_override"] = d128(clean_price(data["price_override"], "Price"))
    _insert_seats(db, [doc])
    _sync_new_seats(db, lay["_id"], [doc])
    return seat_out({c["_id"]: c for c in db.seat_categories.find({"layout_id": lay["_id"]})}, doc)


def update_seat(db, seat_id, data):
    s = _get(db, "seats", seat_id, "Seat")
    upd = {}
    if data.get("category_id") is not None:
        _category_in_layout(db, data["category_id"], s["layout_id"])
        upd["category_id"] = data["category_id"]
    if "price_override" in data:
        upd["price_override"] = None if data["price_override"] is None else d128(clean_price(data["price_override"], "Price"))
    if data.get("is_active") is not None:
        upd["is_active"] = bool(data["is_active"])
    if upd:
        db.seats.update_one({"_id": seat_id}, {"$set": upd})
        inv = {}
        if "category_id" in upd:
            inv["category_id"] = upd["category_id"]
        if "is_active" in upd:
            inv["is_active"] = upd["is_active"] and db.seat_sections.find_one({"_id": s["section_id"]})["is_active"]
        if inv:
            db.match_seat_inventory.update_many({"seat_id": seat_id}, {"$set": inv})
    s = db.seats.find_one({"_id": seat_id})
    return seat_out({c["_id"]: c for c in db.seat_categories.find({"layout_id": s["layout_id"]})}, s)


def delete_seat(db, seat_id):
    _get(db, "seats", seat_id, "Seat")
    _delete_seats(db, "seat", seat_id)


# ------------------------------------------------------------------ match assignment and inventory
def _build_inventory(db, match_id, layout_id):
    sections = {s["_id"]: s["is_active"] for s in db.seat_sections.find({"layout_id": layout_id}, {"is_active": 1})}
    have = {r["seat_id"] for r in db.match_seat_inventory.find({"match_id": match_id}, {"seat_id": 1})}
    now, batch = clock_now(), []
    for s in db.seats.find({"layout_id": layout_id}).sort("_id", 1):
        if s["_id"] in have:
            continue
        batch.append(_inv_doc(match_id, s, sections.get(s["section_id"], True), now))
        if len(batch) >= CHUNK:
            _insert_inventory(db, batch)
            batch = []
    _insert_inventory(db, batch)


def assign_layout(db, match_id, layout_id):
    m = _get(db, "matches", match_id, "Match")
    if m["status"] != "scheduled":
        raise SeatingError(409, "Seating can only be assigned to a scheduled match")
    lay = _get(db, "seat_layouts", layout_id, "Layout")
    if lay["stadium_id"] != m["stadium_id"]:
        raise SeatingError(409, "This layout belongs to a different stadium than the match")
    if not lay["is_active"]:
        raise SeatingError(409, "This layout is not active")
    if _seat_total(db, layout_id) == 0:
        raise SeatingError(409, "This layout has no seats yet")
    if not m.get("seat_layout_id") and db.bookings.count_documents({"match_id": match_id, "status": {"$in": ["pending_payment", "confirmed"]}, "booking_type": {"$ne": "seat"}}, limit=1):
        raise SeatingError(409, "This match already has category-ticket bookings. Seat selection can only be enabled for a match without live bookings.")
    cur = m.get("seat_layout_id")
    if cur and cur != layout_id:
        _guard_removable(db, {"match_id": match_id}, "The current seating of this match")
        db.match_seat_inventory.delete_many({"match_id": match_id})
    db.matches.update_one({"_id": match_id}, {"$set": {"seat_layout_id": layout_id, "seat_inventory_status": "building"}})
    _build_inventory(db, match_id, layout_id)   # idempotent: a failed/partial build is completed by simply repeating this call
    db.matches.update_one({"_id": match_id}, {"$set": {"seat_inventory_status": "ready", "seat_inventory_built_at": clock_now()}})
    return inventory_summary(db, match_id)


def unassign_layout(db, match_id):
    m = _get(db, "matches", match_id, "Match")
    if not m.get("seat_layout_id"):
        return
    _guard_removable(db, {"match_id": match_id}, "This match")
    db.match_seat_inventory.delete_many({"match_id": match_id})
    db.matches.update_one({"_id": match_id}, {"$unset": {"seat_layout_id": "", "seat_inventory_status": "", "seat_inventory_built_at": ""}})


def _grouped(db, match_id, key, extra=None):
    flt = {"match_id": match_id, **(extra or {})}
    return db.match_seat_inventory.aggregate([{"$match": flt}, {"$group": {"_id": key, "n": {"$sum": 1}}}])


def inventory_summary(db, match_id):
    m = _get(db, "matches", match_id, "Match")
    sweep_expired_holds(db, match_id)
    lay_id = m.get("seat_layout_id")
    if not lay_id:
        return {"match_id": match_id, "seating_enabled": False}
    by_status = {s: 0 for s in STATUSES}
    for r in _grouped(db, match_id, "$status"):
        by_status[r["_id"]] = r["n"]
    total = sum(by_status.values())
    sellable = db.match_seat_inventory.count_documents({"match_id": match_id, "status": "available", "is_active": True})
    cats = {c["_id"]: c for c in db.seat_categories.find({"layout_id": lay_id})}
    by_cat = {}
    for r in db.match_seat_inventory.aggregate([{"$match": {"match_id": match_id}}, {"$group": {"_id": {"c": "$category_id", "s": "$status"}, "n": {"$sum": 1}}}]):
        row = by_cat.setdefault(r["_id"]["c"], {s: 0 for s in STATUSES})
        row[r["_id"]["s"]] += r["n"]
    secs = {s["_id"]: s for s in db.seat_sections.find({"layout_id": lay_id})}
    by_sec = {}
    for r in db.match_seat_inventory.aggregate([{"$match": {"match_id": match_id}}, {"$group": {"_id": {"c": "$section_id", "s": "$status"}, "n": {"$sum": 1}}}]):
        row = by_sec.setdefault(r["_id"]["c"], {s: 0 for s in STATUSES})
        row[r["_id"]["s"]] += r["n"]
    return {"match_id": match_id, "seating_enabled": True, "layout_id": lay_id, "inventory_status": m.get("seat_inventory_status"),
            "total": total, **by_status, "sellable_available": sellable,
            "inactive": db.match_seat_inventory.count_documents({"match_id": match_id, "is_active": False}),
            "by_category": [{"category_id": cid, "category": cats[cid]["name"] if cid in cats else None, **row, "total": sum(row.values())} for cid, row in by_cat.items()],
            "by_section": [{"section_id": sid, "section": secs[sid]["name"] if sid in secs else None, "is_active": secs[sid]["is_active"] if sid in secs else None,
                            **row, "total": sum(row.values())} for sid, row in by_sec.items()]}


def list_inventory(db, match_id, section_id=None, row_id=None, status=None, limit=100, offset=0):
    _get(db, "matches", match_id, "Match")
    sweep_expired_holds(db, match_id)
    if status is not None and status not in STATUSES:
        _bad("Unknown status")
    flt = {"match_id": match_id}
    for k, v in (("section_id", section_id), ("row_id", row_id), ("status", status)):
        if v is not None:
            flt[k] = v
    limit, offset = min(max(int(limit), 1), 500), max(int(offset), 0)
    rows = list(db.match_seat_inventory.find(flt).sort("seat_id", 1).skip(offset).limit(limit))
    seats = {s["_id"]: s for s in db.seats.find({"_id": {"$in": [r["seat_id"] for r in rows]}})}
    bk = {b["_id"]: b for b in db.bookings.find({"_id": {"$in": [r["booking_id"] for r in rows if r.get("booking_id")]}}, {"reference": 1, "status": 1, "user_id": 1})}
    us = {u["_id"]: u for u in db.users.find({"_id": {"$in": [b["user_id"] for b in bk.values()]}}, {"name": 1})}
    def booking_info(r):
        b = bk.get(r.get("booking_id"))
        return {"booking_id": b["_id"], "booking_reference": b["reference"], "booking_status": b["status"], "customer": us.get(b["user_id"], {}).get("name"),
                "hold_expires_at": r["hold_expires_at"].isoformat() if r.get("hold_expires_at") else None} if b else {}
    return {"total": db.match_seat_inventory.count_documents(flt), "limit": limit, "offset": offset,
            "items": [{"seat_id": r["seat_id"], "seat_key": seats.get(r["seat_id"], {}).get("seat_key"), "status": r["status"], "is_active": r["is_active"],
                       "section_id": r["section_id"], "row_id": r["row_id"], "category_id": r["category_id"], **booking_info(r)} for r in rows]}


def set_seat_status(db, match_id, seat_ids, target):
    """Admin block / unblock. Only available <-> blocked is allowed; held and booked seats are never touched."""
    _get(db, "matches", match_id, "Match")
    if target not in ("blocked", "available"):
        _bad("Status can only be set to 'blocked' or 'available'")
    ids = list(dict.fromkeys(seat_ids or []))
    if not ids or len(ids) > 2000:
        _bad("Provide between 1 and 2000 seat ids")
    src = "available" if target == "blocked" else "blocked"
    res = db.match_seat_inventory.update_many({"match_id": match_id, "seat_id": {"$in": ids}, "status": src}, {"$set": {"status": target}})
    return {"requested": len(ids), "changed": res.modified_count, "skipped": len(ids) - res.modified_count}


# ------------------------------------------------------------------ public reads and server-side pricing
def seat_price(seat, cat):
    return dec(seat["price_override"]) if seat.get("price_override") is not None else dec(cat["base_price"])


def stadium_seating(db, stadium_id):
    _get(db, "stadiums", stadium_id, "Stadium")
    lay = db.seat_layouts.find_one({"stadium_id": stadium_id, "is_active": True})
    if not lay:
        return {"stadium_id": stadium_id, "seating_enabled": False}
    tree = layout_tree(db, lay["_id"])
    cats = {c["id"]: c for c in tree["categories"]}
    return {"stadium_id": stadium_id, "seating_enabled": True, "layout_id": lay["_id"], "name": lay["name"], "total_seats": tree["totals"]["active_seats"],
            "categories": [{"id": c["id"], "name": c["name"], "base_price": c["base_price"], "color": c["color"]} for c in tree["categories"]],
            "stands": [{"id": st["id"], "name": st["name"], "sections": [
                {"id": s["id"], "name": s["name"], "code": s["code"], "category": cats[s["category_id"]]["name"], "seat_count": s["seat_count"]}
                for s in st["sections"] if s["is_active"]]} for st in tree["stands"] if st["is_active"]]}


def match_seating(db, match_id):
    """Section-level seat map for one match. Matches without a layout answer seating_enabled=false (never an error)."""
    m = _get(db, "matches", match_id, "Match")
    sweep_expired_holds(db, match_id)
    lay_id = m.get("seat_layout_id")
    if not lay_id or m.get("seat_inventory_status") != "ready":
        return {"match_id": match_id, "seating_enabled": False}
    q = {"layout_id": lay_id}
    cats = {c["_id"]: c for c in db.seat_categories.find(q)}
    counts = {}
    for r in db.match_seat_inventory.aggregate([{"$match": {"match_id": match_id, "is_active": True}}, {"$group": {"_id": {"c": "$section_id", "s": "$status"}, "n": {"$sum": 1}}}]):
        counts.setdefault(r["_id"]["c"], {})[r["_id"]["s"]] = r["n"]
    sections = {}
    for s in db.seat_sections.find({**q, "is_active": True}).sort([("sort_order", 1), ("_id", 1)]):
        c = counts.get(s["_id"], {})
        total = sum(c.values())
        sections.setdefault(s["stand_id"], []).append({"id": s["_id"], "name": s["name"], "code": s["code"], "category": cats[s["category_id"]]["name"],
                                                       "base_price": f(cats[s["category_id"]]["base_price"]), "total": total, "available": c.get("available", 0),
                                                       "held": c.get("held", 0), "booked": c.get("booked", 0)})
    stands = [{"id": st["_id"], "name": st["name"], "sections": sections.get(st["_id"], []),
               "available": sum(x["available"] for x in sections.get(st["_id"], []))}
              for st in db.seat_stands.find({**q, "is_active": True}).sort([("sort_order", 1), ("_id", 1)])]
    return {"match_id": match_id, "seating_enabled": True, "layout_id": lay_id, "max_seats": settings.MAX_TICKETS, "stands": stands}


def match_section_seats(db, match_id, section_id, user_id=None):
    m = _get(db, "matches", match_id, "Match")
    sweep_expired_holds(db, match_id)
    sec = db.seat_sections.find_one({"_id": section_id})
    if not sec or sec["layout_id"] != m.get("seat_layout_id") or not sec["is_active"]:
        raise SeatingError(404, "Section not found for this match")
    cats = {c["_id"]: c for c in db.seat_categories.find({"layout_id": sec["layout_id"]})}
    inv = {r["seat_id"]: r for r in db.match_seat_inventory.find({"match_id": match_id, "section_id": section_id})}
    rows = []
    for row in db.seat_rows.find({"section_id": section_id}).sort([("sort_order", 1), ("_id", 1)]):
        seats = []
        for s in db.seats.find({"row_id": row["_id"]}).sort("seat_number", 1):
            r = inv.get(s["_id"])
            if r is None:
                continue
            if r["status"] == "available" and r["is_active"]:
                status = "available"
            elif r["status"] == "held" and user_id is not None and r.get("hold_user_id") == user_id:
                status = "mine"                       # held by the logged-in user (shown blue)
            elif r["status"] in LOCKED:
                status = r["status"]
            else:
                status = "unavailable"   # blocked by an admin, or its seat/section is switched off
            seats.append({"seat_id": s["_id"], "seat_key": s["seat_key"], "number": s["seat_number"], "status": status,
                          "selectable": status == "available", "price": f(seat_price(s, cats[s["category_id"]])), "category": cats[s["category_id"]]["name"]})
        rows.append({"id": row["_id"], "label": row["label"], "seats": seats})
    return {"match_id": match_id, "section": {"id": sec["_id"], "name": sec["name"], "code": sec["code"]}, "rows": rows}


def quote_seats(db, match_id, seat_ids):
    """Server-side price for specific seats. The client sends ids only; prices are looked up here. Does NOT hold or reserve anything."""
    m = _get(db, "matches", match_id, "Match")
    sweep_expired_holds(db, match_id)
    if not m.get("seat_layout_id") or m.get("seat_inventory_status") != "ready":
        raise SeatingError(409, "This match has no seat selection")
    ids = list(seat_ids or [])
    if not ids:
        _bad("Select at least one seat")
    if len(set(ids)) != len(ids):
        _bad("Duplicate seats in the request")
    if len(ids) > settings.MAX_TICKETS:
        _bad(f"You can select at most {settings.MAX_TICKETS} seats")
    seats = {s["_id"]: s for s in db.seats.find({"_id": {"$in": ids}, "layout_id": m["seat_layout_id"]})}
    inv = {r["seat_id"]: r for r in db.match_seat_inventory.find({"match_id": match_id, "seat_id": {"$in": ids}})}
    gone = [i for i in ids if i not in seats or i not in inv]
    if gone:
        raise SeatingError(404, f"Seats not found for this match: {gone}")
    unavailable = [i for i in ids if inv[i]["status"] != "available" or not inv[i]["is_active"]]
    if unavailable:
        raise SeatingError(409, f"Seats not available: {unavailable}")
    cats = {c["_id"]: c for c in db.seat_categories.find({"layout_id": m["seat_layout_id"]})}
    items = [{"seat_id": i, "seat_key": seats[i]["seat_key"], "category": cats[seats[i]["category_id"]]["name"],
              "price": seat_price(seats[i], cats[seats[i]["category_id"]])} for i in ids]
    q = seat_quote([it["price"] for it in items], settings.FEE_PERCENT, settings.TICKET_TAX_PERCENT)
    return {"match_id": match_id, "items": [{**it, "price": float(it["price"])} for it in items],
            **{k: (float(v) if k != "quantity" else v) for k, v in q.items()},
            "note": "Quote only: seats are not reserved until Phase 3B holds exist."}
