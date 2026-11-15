"""Create indexes (if missing) and seed (if empty). Non-destructive. Usage: python -m app.init_db"""
from .database import get_db, ensure_indexes
from . import seed


def main():
    db = get_db()
    ensure_indexes(db)
    print("Indexes ready.")
    print("Seeded." if seed.run(db) else "Database already has data; seed skipped.")


if __name__ == "__main__":
    main()
