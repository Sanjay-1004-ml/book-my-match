import sqlite3
from .sqlite_db import get_connection
from typing import List, Dict, Any

class HybridBookingError(RuntimeError):
    pass

def _execute_many(conn: sqlite3.Connection, query: str, params: List[tuple]):
    cur = conn.cursor()
    cur.executemany(query, params)
    return cur

def insert_customer(conn: sqlite3.Connection, name: str, email: str, phone: str = None) -> int:
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO customers (name, email, phone) VALUES (?,?,?)
           ON CONFLICT(email) DO UPDATE SET name=excluded.name, phone=excluded.phone
           RETURNING customer_id;""",
        (name, email, phone),
    )
    row = cur.fetchone()
    return row["customer_id"] if row else None

def insert_booking(conn: sqlite3.Connection, customer_id: int, booking_date: str, total_amount: float, status: str) -> int:
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO bookings (customer_id, booking_date, total_amount, booking_status)
           VALUES (?,?,?,?) RETURNING booking_id;""",
        (customer_id, booking_date, total_amount, status),
    )
    row = cur.fetchone()
    return row["booking_id"] if row else None

def insert_booking_items(conn: sqlite3.Connection, booking_id: int, items: List[Dict[str, Any]]):
    query = """INSERT INTO booking_items (booking_id, event_id, event_name_snapshot, category,
               ticket_price_snapshot, quantity, subtotal)
               VALUES (?,?,?,?,?,?,?);"""
    params = [
        (
            booking_id,
            it["event_id"],
            it["event_name_snapshot"],
            it["category"],
            it["ticket_price_snapshot"],
            it["quantity"],
            it["subtotal"],
        )
        for it in items
    ]
    _execute_many(conn, query, params)

def insert_payment(conn: sqlite3.Connection, booking_id: int, method: str, amount: float, status: str, date: str) -> int:
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO payments (booking_id, payment_method, payment_amount, payment_status, payment_date)
           VALUES (?,?,?,?,?) RETURNING payment_id;""",
        (booking_id, method, amount, status, date),
    )
    row = cur.fetchone()
    return row["payment_id"] if row else None

def create_full_booking(customer: Dict[str, str], booking_date: str, status: str,
                       items: List[Dict[str, Any]], payment: Dict[str, Any]) -> int:
    """Wrap all inserts into a single transaction.
    Returns the generated booking_id.
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN")
        cust_id = insert_customer(conn, customer["name"], customer["email"], customer.get("phone"))
        total = sum(it["subtotal"] for it in items)
        booking_id = insert_booking(conn, cust_id, booking_date, total, status)
        insert_booking_items(conn, booking_id, items)
        insert_payment(conn, booking_id, payment["method"], payment["amount"], payment["status"], payment["date"])
        conn.commit()
        return booking_id
    except Exception as exc:
        conn.rollback()
        raise HybridBookingError(str(exc))

def get_booking_report() -> List[Dict[str, Any]]:
    """Return a list of rows joining customers, bookings, items, and payments.
    Each row contains fields from all tables for reporting purposes.
    """
    conn = get_connection()
    query = """
        SELECT c.customer_id, c.name, c.email, b.booking_id, b.booking_date, b.total_amount, b.booking_status,
               i.item_id, i.event_id, i.category, i.quantity, i.subtotal,
               p.payment_id, p.payment_method, p.payment_amount, p.payment_status, p.payment_date
        FROM customers c
        JOIN bookings b ON c.customer_id = b.customer_id
        JOIN booking_items i ON b.booking_id = i.booking_id
        JOIN payments p ON b.booking_id = p.booking_id
        ORDER BY b.booking_date DESC;
    """
    cur = conn.execute(query)
    rows = [dict(row) for row in cur.fetchall()]
    return rows
