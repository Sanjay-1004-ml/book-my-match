from datetime import datetime, timezone
"""Migration tests: build a small SQLite database with the OLD schema, convert it, import it into the test MongoDB."""
import sqlite3
from decimal import Decimal
from app import migrate_to_mongo as M

DDL = """
CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT, email TEXT, phone TEXT, password_hash TEXT, role TEXT, is_active BOOLEAN, created_at DATETIME);
CREATE TABLE sports(id INTEGER PRIMARY KEY, name TEXT, description TEXT);
CREATE TABLE stadiums(id INTEGER PRIMARY KEY, name TEXT, location TEXT, capacity INT, image_url TEXT, sport_id INT);
CREATE TABLE matches(id INTEGER PRIMARY KEY, stadium_id INT, team_a TEXT, team_b TEXT, match_date DATE, start_time TIME, timing TEXT, status TEXT);
CREATE TABLE ticket_categories(id INTEGER PRIMARY KEY, stadium_id INT, name TEXT, price NUMERIC);
CREATE TABLE ticket_inventory(id INTEGER PRIMARY KEY, match_id INT, category_id INT, total INT, available INT);
CREATE TABLE food_categories(id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE food_items(id INTEGER PRIMARY KEY, category_id INT, name TEXT, price NUMERIC, is_active BOOLEAN);
CREATE TABLE bookings(id INTEGER PRIMARY KEY, reference TEXT, user_id INT, match_id INT, category_id INT, quantity INT, unit_price NUMERIC, subtotal NUMERIC, fee NUMERIC, tax NUMERIC, total NUMERIC, status TEXT, payment_status TEXT, payment_ref TEXT, refund_status TEXT, idempotency_key TEXT, created_at DATETIME, cancelled_at DATETIME);
CREATE TABLE food_orders(id INTEGER PRIMARY KEY, user_id INT, booking_id INT, subtotal NUMERIC, tax NUMERIC, total NUMERIC, status TEXT, payment_status TEXT, created_at DATETIME);
CREATE TABLE food_order_items(id INTEGER PRIMARY KEY, order_id INT, food_item_id INT, name TEXT, unit_price NUMERIC, quantity INT);
INSERT INTO users VALUES (7,'Ann','Ann@Example.com',NULL,'$2b$12$hash',  'user',1,'2026-10-26 17:14:20');
INSERT INTO sports VALUES (3,'Cricket',NULL);
INSERT INTO stadiums VALUES (4,'Melbourne','Chennai',1000,NULL,3);
INSERT INTO matches VALUES (9,4,'A','B','2027-01-09','18:00:00.000000','6:00pm-9:00pm','scheduled');
INSERT INTO ticket_categories VALUES (5,4,'Luxury',6000);
INSERT INTO ticket_inventory VALUES (11,9,5,250,248);
INSERT INTO food_categories VALUES (2,'Desserts');
INSERT INTO food_items VALUES (6,2,'Brownie',80,1);
INSERT INTO bookings VALUES (8,'BMM-TEST1234',7,9,5,2,6000,12000,240,2203.2,14443.2,'confirmed','successful','DEMO-TEST1234',NULL,'k1','2026-10-26 17:15:15',NULL);
INSERT INTO food_orders VALUES (3,7,8,160,8,168,'placed','pending','2026-10-26 17:16:00');
INSERT INTO food_order_items VALUES (1,3,6,'Brownie',80,2);
"""


def make_source(tmp_path):
    p = tmp_path / "old.db"

    recent_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    test_ddl = DDL.replace(
        "2026-10-26 17:15:15",
        recent_time
    )

    con = sqlite3.connect(p)
    con.executescript(test_ddl)
    con.commit()
    con.close()

    return f"sqlite:///{p}"

def test_transform_preserves_ids_values_and_relationships(tmp_path):
    docs = M.transform(M.load_source(make_source(tmp_path)))
    assert docs["users"][0]["_id"] == 7 and docs["users"][0]["email"] == "ann@example.com"
    assert docs["bookings"][0]["total"] == Decimal("14443.20")
    assert docs["matches"][0]["start_time"] == "18:00:00" and docs["matches"][0]["match_date"] == "2027-01-09"
    assert docs["food_orders"][0]["items"] == [{"food_item_id": 6, "name": "Brownie", "unit_price": Decimal("80.00"), "quantity": 2}]
    assert docs["idempotency_keys"][0]["_id"] == "7:k1"
    errors, warnings = M.check_integrity(docs)
    assert errors == [] and warnings == []


def test_integrity_check_catches_orphans(tmp_path):
    docs = M.transform(M.load_source(make_source(tmp_path)))
    docs["bookings"][0]["match_id"] = 999
    assert any("no matching matches" in e for e in M.check_integrity(docs)[0])


def test_import_is_idempotent_and_never_overwrites_newer_data(tmp_path, db):
    for c in db.list_collection_names():
        db.drop_collection(c)
    docs = M.transform(M.load_source(make_source(tmp_path)))
    r1 = M.write(db, docs, "sqlite", force=False)
    assert r1["bookings"]["inserted"] == 1 and M.verify(db, docs) == []
    db.ticket_inventory.update_one({"_id": 11}, {"$set": {"available": 100}})  # app activity after migration
    r2 = M.write(db, docs, "sqlite", force=False)
    assert all(v["inserted"] == 0 for v in r2.values())
    assert db.bookings.count_documents({}) == 1
    assert db.ticket_inventory.find_one({"_id": 11})["available"] == 100  # NOT reset to the SQL value
    assert db.counters.find_one({"_id": "bookings"})["seq"] == 8  # next booking id continues after 8
