"""One-off, re-runnable migration: SQLite or PostgreSQL  ->  MongoDB.

  python -m app.migrate_to_mongo --dry-run              # read + convert + validate only; touches no database
  python -m app.migrate_to_mongo                        # migrate (needs MONGODB_URL in env/.env)
  python -m app.migrate_to_mongo --verify-only          # compare MongoDB with the source again

Source is SOURCE_DATABASE_URL (or --source), e.g. sqlite:///./book_my_match.db or the Render PostgreSQL URL.
The source is opened READ-ONLY and never modified.

Safety properties
  * Insert-if-absent by _id ($setOnInsert): re-running never duplicates and never overwrites newer data
    (e.g. bookings made after the first run).
  * Integer ids and all relationships are preserved, so the API and frontend behave exactly as before.
  * Passwords are copied as the existing bcrypt hashes, so users keep their logins.
  * Refuses to import into a database that already holds other data unless --force.
Imports are kept free of FastAPI/pymongo at module level so --dry-run works anywhere."""
import argparse
import os
import sqlite3
import sys
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

TABLES = ["users", "sports", "stadiums", "matches", "ticket_categories", "ticket_inventory", "food_categories",
          "food_items", "bookings", "food_orders", "food_order_items"]
BACKEND_DIR = Path(__file__).resolve().parents[1]

# (collection, field, target collection, nullable)
FOREIGN_KEYS = [("stadiums", "sport_id", "sports", False), ("matches", "stadium_id", "stadiums", False),
                ("ticket_categories", "stadium_id", "stadiums", False), ("ticket_inventory", "match_id", "matches", False),
                ("ticket_inventory", "category_id", "ticket_categories", False), ("food_items", "category_id", "food_categories", False),
                ("bookings", "user_id", "users", False), ("bookings", "match_id", "matches", False),
                ("bookings", "category_id", "ticket_categories", False), ("food_orders", "user_id", "users", False),
                ("food_orders", "booking_id", "bookings", True)]
# fields that must be identical in MongoDB right after import (verification)
VERIFY_FIELDS = {"users": ["email", "password_hash", "role"], "stadiums": ["name", "capacity"],
                 "matches": ["stadium_id", "team_a", "team_b", "match_date"], "ticket_categories": ["name", "price"],
                 "food_items": ["name", "price"], "bookings": ["reference", "user_id", "match_id", "category_id", "quantity", "total"],
                 "food_orders": ["user_id", "total"]}


# ---------------- reading the source ----------------
def default_source() -> str:
    for var in ("SOURCE_DATABASE_URL", "DATABASE_URL"):
        v = os.environ.get(var, "")
        if v.startswith(("sqlite", "postgres")):
            return v
    return f"sqlite:///{BACKEND_DIR / 'book_my_match.db'}"


def source_kind(url: str) -> str:
    return "postgresql" if url.startswith(("postgres://", "postgresql")) else "sqlite"


def load_source(url: str) -> dict:
    if source_kind(url) == "sqlite":
        path = url.split("sqlite:///", 1)[1] if url.startswith("sqlite") else url
        if not Path(path).is_file():
            sys.exit(f"SQLite file not found: {path}")
        con = sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True)  # read-only
        con.row_factory = sqlite3.Row
        try:
            return {t: [dict(r) for r in con.execute(f'SELECT * FROM "{t}" ORDER BY id')] for t in TABLES}
        except sqlite3.OperationalError as e:
            sys.exit(f"Source database does not look like a Book My Match database: {e}")
        finally:
            con.close()
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:
        sys.exit("Reading PostgreSQL needs psycopg: pip install -r requirements-migrate.txt")
    url = url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url, row_factory=dict_row) as conn:
        conn.read_only = True
        return {t: conn.execute(f'SELECT * FROM "{t}" ORDER BY id').fetchall() for t in TABLES}


# ---------------- value conversion ----------------
def D(v):
    return None if v is None else Decimal(str(v)).quantize(Decimal("0.01"))


def DATE(v):
    if isinstance(v, datetime): return v.date().isoformat()
    if isinstance(v, date): return v.isoformat()
    return str(v)[:10]


def TIME(v):
    if v is None: return None
    if isinstance(v, time): return v.replace(microsecond=0, tzinfo=None).isoformat()
    return time.fromisoformat(str(v).strip()).replace(microsecond=0, tzinfo=None).isoformat()


def DT(v):
    if v is None: return None
    if not isinstance(v, datetime): v = datetime.fromisoformat(str(v).strip())
    return v.replace(tzinfo=None)


# ---------------- SQL rows -> Mongo documents ----------------
def transform(src: dict) -> dict:
    out = {}
    out["users"] = [{"_id": r["id"], "name": r["name"], "email": r["email"].lower(), "phone": r["phone"],
                     "password_hash": r["password_hash"], "role": r["role"], "is_active": bool(r["is_active"]),
                     "created_at": DT(r["created_at"])} for r in src["users"]]
    out["sports"] = [{"_id": r["id"], "name": r["name"], "description": r["description"]} for r in src["sports"]]
    out["stadiums"] = [{"_id": r["id"], "name": r["name"], "location": r["location"], "capacity": r["capacity"],
                        "image_url": r["image_url"], "sport_id": r["sport_id"]} for r in src["stadiums"]]
    out["matches"] = [{"_id": r["id"], "stadium_id": r["stadium_id"], "team_a": r["team_a"], "team_b": r["team_b"],
                       "match_date": DATE(r["match_date"]), "start_time": TIME(r["start_time"]), "timing": r["timing"],
                       "status": r["status"]} for r in src["matches"]]
    out["ticket_categories"] = [{"_id": r["id"], "stadium_id": r["stadium_id"], "name": r["name"], "price": D(r["price"])}
                                for r in src["ticket_categories"]]
    out["ticket_inventory"] = [{"_id": r["id"], "match_id": r["match_id"], "category_id": r["category_id"],
                                "total": r["total"], "available": r["available"]} for r in src["ticket_inventory"]]
    out["food_categories"] = [{"_id": r["id"], "name": r["name"]} for r in src["food_categories"]]
    out["food_items"] = [{"_id": r["id"], "category_id": r["category_id"], "name": r["name"], "price": D(r["price"]),
                          "is_active": bool(r["is_active"])} for r in src["food_items"]]
    out["bookings"] = [{"_id": r["id"], "reference": r["reference"], "user_id": r["user_id"], "match_id": r["match_id"],
                        "category_id": r["category_id"], "quantity": r["quantity"], "unit_price": D(r["unit_price"]),
                        "subtotal": D(r["subtotal"]), "fee": D(r["fee"]), "tax": D(r["tax"]), "total": D(r["total"]),
                        "status": r["status"], "payment_status": r["payment_status"], "payment_ref": r["payment_ref"],
                        "refund_status": r["refund_status"], "idempotency_key": r["idempotency_key"],
                        "created_at": DT(r["created_at"]), "cancelled_at": DT(r["cancelled_at"])} for r in src["bookings"]]
    items_by_order = {}
    for r in src["food_order_items"]:
        items_by_order.setdefault(r["order_id"], []).append(
            {"food_item_id": r["food_item_id"], "name": r["name"], "unit_price": D(r["unit_price"]), "quantity": r["quantity"]})
    out["food_orders"] = [{"_id": r["id"], "user_id": r["user_id"], "booking_id": r["booking_id"], "subtotal": D(r["subtotal"]),
                           "tax": D(r["tax"]), "total": D(r["total"]), "status": r["status"], "payment_status": r["payment_status"],
                           "created_at": DT(r["created_at"]), "items": items_by_order.get(r["id"], [])} for r in src["food_orders"]]
    # keep Idempotency-Key replay protection for migrated bookings
    out["idempotency_keys"] = [{"_id": f'{b["user_id"]}:{b["idempotency_key"]}', "booking_id": b["_id"], "created_at": b["created_at"]}
                               for b in out["bookings"] if b["idempotency_key"]]
    return out


def check_integrity(docs: dict) -> tuple:
    """Returns (errors, warnings). Errors mean the data would be inconsistent in MongoDB."""
    errors, warnings = [], []
    ids = {c: {d["_id"] for d in rows} for c, rows in docs.items()}
    for coll, field, target, nullable in FOREIGN_KEYS:
        for d in docs[coll]:
            v = d[field]
            if v is None and nullable: continue
            if v not in ids[target]:
                errors.append(f"{coll}#{d['_id']}.{field}={v} has no matching {target} document")
    for o in docs["food_orders"]:
        for it in o["items"]:
            if it["food_item_id"] not in ids["food_items"]:
                errors.append(f"food_orders#{o['_id']} references missing food item {it['food_item_id']}")
    st_of_match = {m["_id"]: m["stadium_id"] for m in docs["matches"]}
    st_of_cat = {c["_id"]: c["stadium_id"] for c in docs["ticket_categories"]}
    for b in docs["bookings"]:
        if st_of_match.get(b["match_id"]) != st_of_cat.get(b["category_id"]):
            errors.append(f"bookings#{b['_id']}: ticket category belongs to a different stadium than the match")
    sold = {}
    for b in docs["bookings"]:
        if b["status"] == "confirmed":
            k = (b["match_id"], b["category_id"]); sold[k] = sold.get(k, 0) + b["quantity"]
    for i in docs["ticket_inventory"]:
        if not (0 <= i["available"] <= i["total"]):
            errors.append(f"ticket_inventory#{i['_id']}: available {i['available']} outside 0..{i['total']}")
        if i["total"] - i["available"] != sold.get((i["match_id"], i["category_id"]), 0):
            warnings.append(f"ticket_inventory#{i['_id']}: sold={i['total'] - i['available']} but confirmed bookings={sold.get((i['match_id'], i['category_id']), 0)}")
    for coll, field in [("users", "email"), ("sports", "name"), ("stadiums", "name"), ("bookings", "reference"), ("food_categories", "name")]:
        vals = [d[field] for d in docs[coll]]
        if len(vals) != len(set(vals)):
            errors.append(f"{coll}.{field} has duplicate values (unique index would reject them)")
    for coll, keyf in [("ticket_categories", ("stadium_id", "name")), ("ticket_inventory", ("match_id", "category_id"))]:
        keys = [tuple(d[k] for k in keyf) for d in docs[coll]]
        if len(keys) != len(set(keys)):
            errors.append(f"{coll}.{keyf} has duplicates (unique index would reject them)")
    return errors, warnings


# ---------------- writing / verifying MongoDB ----------------
def encode(doc: dict) -> dict:
    from bson.decimal128 import Decimal128
    def enc(v):
        if isinstance(v, Decimal): return Decimal128(v)
        if isinstance(v, dict): return {k: enc(x) for k, x in v.items()}
        if isinstance(v, list): return [enc(x) for x in v]
        return v
    return enc(doc)


def write(db, docs: dict, source_type: str, force: bool) -> dict:
    from pymongo import UpdateOne
    from pymongo.errors import BulkWriteError
    from .database import ensure_indexes, sync_counters
    ensure_indexes(db)
    if not db.migration_log.find_one({"_id": "sql_import"}) and not force and \
            any(db[c].find_one({}, {"_id": 1}) for c in ("sports", "users", "matches", "bookings")):
        sys.exit("The target MongoDB database already contains data that did not come from this migration "
                 "(for example it was seeded with `python -m app.init_db`).\nMigrating into it could mix two different "
                 "data sets. Use an empty database (change DATABASE_NAME), or re-run with --force if you are sure.")
    report = {}
    for coll, rows in docs.items():
        ops = [UpdateOne({"_id": r["_id"]}, {"$setOnInsert": {k: v for k, v in encode(r).items() if k != "_id"}}, upsert=True) for r in rows]
        inserted = conflicts = 0
        if ops:
            try:
                inserted = db[coll].bulk_write(ops, ordered=False).upserted_count
            except BulkWriteError as e:
                inserted = e.details.get("nUpserted", 0)
                conflicts = len(e.details.get("writeErrors", []))
        report[coll] = {"source": len(rows), "inserted": inserted, "conflicts": conflicts}
    sync_counters(db)
    db.migration_log.update_one({"_id": "sql_import"}, {"$set": {"source_type": source_type, "imported_at": datetime.now(),
                                "counts": {c: r["source"] for c, r in report.items()}}}, upsert=True)
    return report


def verify(db, docs: dict) -> list:
    problems = []
    for coll, rows in docs.items():
        found = {d["_id"]: d for d in db[coll].find({"_id": {"$in": [r["_id"] for r in rows]}})}
        missing = [r["_id"] for r in rows if r["_id"] not in found]
        if missing:
            problems.append(f"{coll}: {len(missing)} source documents missing in MongoDB (first ids {missing[:5]})")
        for r in rows:
            got = found.get(r["_id"])
            for f in VERIFY_FIELDS.get(coll, []) if got else []:
                a, b = r[f], got.get(f)
                b = b.to_decimal() if hasattr(b, "to_decimal") else b
                if a != b:
                    problems.append(f"{coll}#{r['_id']}.{f}: source {a!r} != mongodb {b!r}")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", default=None, help="sqlite:///path or postgresql://... (default: SOURCE_DATABASE_URL / DATABASE_URL / backend/book_my_match.db)")
    ap.add_argument("--dry-run", action="store_true", help="read, convert and validate only; do not connect to MongoDB")
    ap.add_argument("--verify-only", action="store_true", help="only compare MongoDB with the source")
    ap.add_argument("--force", action="store_true", help="allow importing into a non-empty database")
    a = ap.parse_args(argv)
    try:
        from dotenv import load_dotenv; load_dotenv(BACKEND_DIR / ".env")
    except ImportError:
        pass
    url = a.source or default_source()
    kind = source_kind(url)
    src = load_source(url)
    docs = transform(src)
    print(f"Source ({kind}) rows -> documents:")
    for c, rows in docs.items():
        print(f"  {c:18s} {len(rows)}")
    errors, warnings = check_integrity(docs)
    for w in warnings: print("WARNING:", w)
    if errors:
        for e in errors: print("ERROR:", e)
        sys.exit("Source data failed validation; nothing was written.")
    print("Validation passed: all references resolve, inventory bounds and unique keys are consistent.")
    if a.dry_run:
        print("Dry run complete. No database was contacted or modified.")
        return docs
    from .database import get_db
    db = get_db()
    if not a.verify_only:
        report = write(db, docs, kind, a.force)
        print("Import result (inserted = new this run; 0 on a re-run means already present):")
        for c, r in report.items():
            print(f"  {c:18s} source={r['source']:<5} inserted={r['inserted']:<5} conflicts={r['conflicts']}")
        if any(r["conflicts"] for r in report.values()):
            print("WARNING: some documents conflicted with existing unique values and were skipped.")
    problems = verify(db, docs)
    if problems:
        for p in problems[:30]: print("VERIFY FAILED:", p)
        sys.exit(1)
    print("Verification passed: every source record exists in MongoDB with matching key fields.")
    return docs


if __name__ == "__main__":
    main()
