"""MongoDB connection, index definitions, integer-id counters and money helpers.

Design note: every document keeps the integer id it had in the SQL database as its `_id`
(exposed as `id` by the API). This keeps all API URLs and JSON responses, and the unchanged
frontend (which does `+id` conversions), working exactly as before. New ids come from the
atomic `counters` collection."""
from decimal import Decimal
from typing import Optional
from bson.decimal128 import Decimal128
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError
from .config import settings
from .pricing import money

_client: Optional[MongoClient] = None
_override_db = None  # tests may inject a mongomock database via use_database()

# (collection, keys, options)
INDEXES = [
    ("users", [("email", 1)], {"unique": True}),
    ("sports", [("name", 1)], {"unique": True}),
    ("stadiums", [("name", 1)], {"unique": True}),
    ("stadiums", [("sport_id", 1)], {}),
    ("stadiums", [("location", 1)], {}),
    ("matches", [("match_date", 1), ("start_time", 1)], {}),
    ("matches", [("stadium_id", 1), ("match_date", 1)], {}),
    ("matches", [("status", 1)], {}),
    ("ticket_categories", [("stadium_id", 1), ("name", 1)], {"unique": True}),
    ("ticket_inventory", [("match_id", 1), ("category_id", 1)], {"unique": True}),
    ("food_categories", [("name", 1)], {"unique": True}),
    ("food_items", [("category_id", 1)], {}),
    ("bookings", [("reference", 1)], {"unique": True}),
    ("bookings", [("user_id", 1), ("created_at", -1)], {}),
    ("bookings", [("match_id", 1)], {}),
    ("food_orders", [("user_id", 1)], {}),
    ("food_orders", [("booking_id", 1)], {}),
    ("idempotency_keys", [("created_at", 1)], {"expireAfterSeconds": 7 * 24 * 3600}),
    # --- payments, tickets, entry verification ---
    ("bookings", [("status", 1), ("expires_at", 1)], {}),
    ("payments", [("transaction_id", 1)], {"unique": True}),
    ("payments", [("booking_id", 1), ("created_at", 1)], {}),
    ("payments", [("user_id", 1)], {}),
    ("favorites", [("user_id", 1), ("kind", 1)], {}),   # new collection (Phase 2): per-user favorite teams/matches
    ("payments", [("food_order_id", 1)], {"sparse": True}),   # food-only payments (booking payments don't have it)
    ("tickets", [("ticket_id", 1)], {"unique": True}),
    ("tickets", [("verification_token_hash", 1)], {"unique": True}),
    ("tickets", [("booking_id", 1), ("seq", 1)], {"unique": True}),  # one ticket per seat-slot of a booking -> no duplicate issuing
    ("tickets", [("match_id", 1), ("ticket_status", 1)], {}),
    ("tickets", [("user_id", 1), ("created_at", -1)], {}),
    ("tickets", [("booking_reference", 1)], {}),
    ("entry_logs", [("scanned_at", -1)], {}),
    ("entry_logs", [("match_id", 1), ("scanned_at", -1)], {}),
    ("entry_logs", [("ticket_id", 1)], {}),
    # --- Phase 3A: stadium seating (see docs/PHASE_3A_SEATING.md) ---
    ("seat_layouts", [("stadium_id", 1)], {"unique": True}),                  # one seating layout per stadium -> seat ids are unique per stadium
    ("seat_categories", [("layout_id", 1), ("name_key", 1)], {"unique": True}),
    ("seat_stands", [("layout_id", 1), ("code", 1)], {"unique": True}),
    ("seat_stands", [("layout_id", 1), ("name_key", 1)], {"unique": True}),
    ("seat_sections", [("layout_id", 1), ("code", 1)], {"unique": True}),   # section code is part of seat_key, so it must be unique per layout
    ("seat_sections", [("stand_id", 1), ("name_key", 1)], {"unique": True}),
    ("seat_rows", [("section_id", 1), ("label", 1)], {"unique": True}),
    ("seat_rows", [("layout_id", 1)], {}),
    ("seats", [("stadium_id", 1), ("seat_key", 1)], {"unique": True}),        # THE seat identifier: unique within a stadium
    ("seats", [("row_id", 1), ("seat_number", 1)], {"unique": True}),         # duplicate seat numbers in a row are impossible
    ("seats", [("section_id", 1)], {}),
    ("seats", [("layout_id", 1), ("category_id", 1)], {}),
    ("match_seat_inventory", [("match_id", 1), ("seat_id", 1)], {"unique": True}),   # one inventory row per seat per match
    ("match_seat_inventory", [("match_id", 1), ("status", 1), ("is_active", 1)], {}),
    ("match_seat_inventory", [("match_id", 1), ("section_id", 1), ("status", 1)], {}),
    ("match_seat_inventory", [("match_id", 1), ("row_id", 1)], {}),
    ("match_seat_inventory", [("seat_id", 1)], {}),
    ("match_seat_inventory", [("layout_id", 1), ("status", 1)], {}),
    # Reserved for Phase 3B/3C (no logic uses them yet). Fields hold_id / booking_id are only written once holds exist, hence sparse.
    ("match_seat_inventory", [("status", 1), ("hold_expires_at", 1)], {}),
    ("match_seat_inventory", [("hold_id", 1)], {"sparse": True}),
    ("match_seat_inventory", [("booking_id", 1)], {"sparse": True}),
    ("seat_holds", [("hold_id", 1)], {"unique": True}),
    ("seat_holds", [("user_id", 1), ("status", 1)], {}),
    ("seat_holds", [("status", 1), ("expires_at", 1)], {}),
    ("seat_holds", [("match_id", 1), ("status", 1)], {}),
]


def get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(settings.MONGODB_URL, serverSelectionTimeoutMS=8000, appname="book-my-match")
    return _client


def get_db():
    """FastAPI dependency and general accessor."""
    return _override_db if _override_db is not None else get_client()[settings.DATABASE_NAME]


def use_database(db):
    """Tests only: route all access to the given database object."""
    global _override_db
    _override_db = db


def ensure_indexes(db=None):
    db = db if db is not None else get_db()
    for coll, keys, opts in INDEXES:
        db[coll].create_index(keys, **opts)


def next_id(db, name: str) -> int:
    """Atomic auto-increment (replaces SQL AUTOINCREMENT)."""
    for _ in range(3):
        try:
            r = db.counters.find_one_and_update({"_id": name}, {"$inc": {"seq": 1}}, upsert=True,
                                                return_document=ReturnDocument.AFTER)
            return int(r["seq"])
        except DuplicateKeyError:  # two first-time upserts raced; retry
            continue
    raise RuntimeError("Could not allocate id")


def next_ids(db, name: str, n: int) -> list:
    """Reserve a block of n consecutive ids with ONE atomic counter update (a 40,000-seat stadium must not need 40,000 round trips)."""
    if n <= 0:
        return []
    for _ in range(3):
        try:
            r = db.counters.find_one_and_update({"_id": name}, {"$inc": {"seq": n}}, upsert=True, return_document=ReturnDocument.AFTER)
            end = int(r["seq"])
            return list(range(end - n + 1, end + 1))
        except DuplicateKeyError:
            continue
    raise RuntimeError("Could not allocate ids")


ID_COLLECTIONS = ["users", "sports", "stadiums", "matches", "ticket_categories", "ticket_inventory",
                  "food_categories", "food_items", "bookings", "food_orders", "payments", "tickets", "entry_logs",
                  "seat_layouts", "seat_categories", "seat_stands", "seat_sections", "seat_rows", "seats"]


def sync_counters(db):
    """Make every counter at least the highest existing _id (needed after seeding/importing explicit ids)."""
    for name in ID_COLLECTIONS:
        top = db[name].find_one({}, {"_id": 1}, sort=[("_id", -1)])
        if top:
            db.counters.update_one({"_id": name}, {"$max": {"seq": int(top["_id"])}}, upsert=True)


def d128(x) -> Decimal128:
    return Decimal128(str(money(Decimal(str(x)))))


def dec(x) -> Decimal:
    if isinstance(x, Decimal128):
        return x.to_decimal()
    return Decimal(str(x))
