# SQLite connection helper for hybrid booking system
import os
import sqlite3
from pathlib import Path

# Path can be overridden via environment variable; default lives next to the backend folder
DB_PATH = os.getenv("SQLITE_DB_PATH", str(Path(__file__).parent.parent / "book_my_match_hybrid.db"))


def get_connection() -> sqlite3.Connection:
    """Return a SQLite connection with foreign‑key enforcement enabled.
    The DB file is created automatically if it does not exist.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Ensure foreign key constraints are respected
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn
