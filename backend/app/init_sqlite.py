# Initialize SQLite tables if they don't exist
import os
from pathlib import Path
from .sqlite_db import get_connection


def init_sqlite():
    conn = get_connection()
    # Check if the main table exists; if not, run the schema script.
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='customers'")
    if cur.fetchone():
        return  # already initialized
    schema_path = Path(__file__).with_name('sql_schema.sql')
    if not schema_path.is_file():
        raise FileNotFoundError(f"SQL schema file not found: {schema_path}")
    with open(schema_path, 'r', encoding='utf-8') as f:
        conn.executescript(f.read())
    conn.commit()
    print('[Hybrid SQLite] Database schema initialized.')
