"""Create (or promote) an administrator from ADMIN_EMAIL / ADMIN_PASSWORD env vars.
Usage: python -m app.create_admin   (password is never stored in code or committed files)"""
import os
import sys
from datetime import datetime
from .clock import now as clock_now
from .database import get_db, ensure_indexes, next_id
from .security import hash_password


def main():
    email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not email or len(password) < 10:
        sys.exit("Set ADMIN_EMAIL and ADMIN_PASSWORD (min 10 chars) in the environment or backend/.env")
    db = get_db()
    ensure_indexes(db)
    u = db.users.find_one({"email": email})
    if u:
        db.users.update_one({"_id": u["_id"]}, {"$set": {"role": "admin", "is_active": True, "password_hash": hash_password(password)}})
        print(f"Updated existing user {email} to admin.")
    else:
        db.users.insert_one({"_id": next_id(db, "users"), "name": os.environ.get("ADMIN_NAME", "Administrator"), "email": email,
                             "phone": None, "password_hash": hash_password(password), "role": "admin", "is_active": True,
                             "created_at": clock_now()})
        print(f"Created admin {email}.")


if __name__ == "__main__":
    main()
