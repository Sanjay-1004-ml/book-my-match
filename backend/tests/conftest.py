"""Tests run against mongomock (in-memory) by default.
To run against a real MongoDB instead:  TEST_MONGODB_URL=mongodb://localhost:27017 pytest
(a throw-away database named bmm_test_<random> is created and dropped; your real database is never touched)."""
import os, sys, uuid
from pathlib import Path

os.environ["MONGODB_URL"] = os.environ.get("TEST_MONGODB_URL", "mongodb://localhost:27017")
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["DATABASE_NAME"] = "bmm_test_unused"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from datetime import datetime
from fastapi.testclient import TestClient
from app.main import app
from app import database, seed
from app.database import ensure_indexes, next_id
from app.security import hash_password

_REAL = os.environ.get("TEST_MONGODB_URL")


@pytest.fixture(scope="session")
def _mongo():
    if _REAL:
        from pymongo import MongoClient
        client = MongoClient(_REAL, serverSelectionTimeoutMS=5000)
        name = "bmm_test_" + uuid.uuid4().hex[:8]
        yield client, name
        client.drop_database(name)  # only the throw-away test database
    else:
        import mongomock
        yield mongomock.MongoClient(), "bmm_test"


@pytest.fixture()
def db(_mongo):
    client, name = _mongo
    client.drop_database(name)
    d = client[name]
    database.use_database(d)
    ensure_indexes(d)
    seed.run(d)
    d.users.insert_one({"_id": next_id(d, "users"), "name": "Admin", "email": "admin@test.com",
                        "password_hash": hash_password("AdminPass123"), "role": "admin", "is_active": True,
                        "phone": None, "created_at": __import__("app.clock", fromlist=["now"]).now()})
    for name, email, role in [("Gate Staff", "staff@test.com", "staff")]:
        if not d.users.find_one({"email": email}):
            d.users.insert_one({"_id": next_id(d, "users"), "name": name, "email": email, "password_hash": hash_password("StaffPass123"),
                                "role": role, "is_active": True, "phone": None, "created_at": __import__("app.clock", fromlist=["now"]).now()})
    yield d
    database.use_database(None)


@pytest.fixture()
def client(db):
    return TestClient(app)


def register(client, email="u1@test.com"):
    r = client.post("/api/auth/register", json={"name": "User One", "email": email, "password": "Passw0rd123", "confirm_password": "Passw0rd123"})
    assert r.status_code == 201, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def admin_headers(client):
    r = client.post("/api/auth/login", json={"email": "admin@test.com", "password": "AdminPass123"})
    return {"Authorization": "Bearer " + r.json()["token"]}


def first_match(client):
    m = client.get("/api/matches").json()[0]
    cats = client.get(f"/api/matches/{m['id']}/ticket-categories").json()
    return m, cats
