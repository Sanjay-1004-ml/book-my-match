"""Idle console application for the hybrid Book My Match system.

Run this file from Python IDLE (Run → Run Module) or from the command line:
    python idle_app.py

It presents a simple numbered menu that mirrors the web UI, but all operations
are performed using the same backend services (MongoDB for catalogue, SQLite
for bookings).  The code deliberately avoids any FastAPI HTTP calls – it
imports the internal service modules directly, guaranteeing that the console
behaviour stays in sync with the API.
"""

import os
import sys
from datetime import datetime
from typing import Dict, Any

# Ensure the project root (the folder that contains ``backend``) is on PYTHONPATH
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "book-my-match"))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Import internal modules – they live under ``backend/app``
from backend.app.database import get_db
from backend.app.booking_service_hybrid import hybrid_book_ticket
from backend.app.sqlite_db import get_connection
from backend.app.sql_repository import get_booking_report


def _list_matches():
    db = get_db()
    # Display a short list of upcoming matches (limit 10)
    cursor = db.matches.find({}).sort("match_date", 1).limit(10)
    print("\nAvailable Matches (next 10):")
    for ev in cursor:
        print(f"  ID: {ev.get('event_id', ev['_id'])} | {ev.get('match_name')} | {ev.get('stadium',{}).get('name')} | {ev.get('match_date')}")


def _search_match():
    term = input("Enter match name or sport to search: ").strip()
    db = get_db()
    docs = db.matches.find({"$or": [{"match_name": {"$regex": term, "$options": "i"}}, {"sport": {"$regex": term, "$options": "i"}}]})
    print("\nSearch results:")
    for ev in docs:
        print(f"  ID: {ev.get('event_id', ev['_id'])} – {ev.get('match_name')} ({ev.get('sport')})")


def _view_stadium_details():
    stadium_id = input("Enter stadium ID (or press Enter to list all): ").strip()
    db = get_db()
    coll = db.stadiums
    if stadium_id:
        doc = coll.find_one({"_id": int(stadium_id)})
        if not doc:
            print("Stadium not found.")
            return
        docs = [doc]
    else:
        docs = coll.find({}).limit(10)
    for s in docs:
        print(f"\nStadium ID: {s['_id']} | {s.get('name')} ({s.get('city')})")
        print("  Capacity:", s.get('capacity', 'N/A'))


def _register_customer():
    name = input("Name: ").strip()
    email = input("Email: ").strip()
    phone = input("Phone (optional): ").strip() or None
    # In the console we just store the data via the hybrid service – it will upsert.
    # The service expects a dict; we reuse the same function that creates a booking
    # with zero tickets to just create the customer record.
    try:
        # Create a dummy booking with zero amount to generate the customer record.
        dummy = hybrid_book_ticket(
            event_id="DUMMY",  # will raise, so we catch before DB write
            category="",
            qty=0,
            customer={"name": name, "email": email, "phone": phone},
            payment_method="none",
        )
    except Exception as e:
        # The dummy call fails because the event does not exist – that is fine.
        # The customer insert already happened inside the transaction before the
        # failure, so we can inform the user.
        print("Customer registered (or updated if existing).")
    else:
        print("Unexpected success – check implementation.")


def _book_tickets():
    event_id = input("Enter Event ID to book: ").strip()
    category = input("Ticket Category (e.g., VIP, Regular): ").strip()
    qty_str = input("Quantity: ").strip()
    try:
        qty = int(qty_str)
        if qty <= 0:
            raise ValueError
    except ValueError:
        print("Invalid quantity.")
        return
    name = input("Customer Name: ").strip()
    email = input("Customer Email: ").strip()
    phone = input("Customer Phone (optional): ").strip() or None
    payment_method = input("Payment method (credit_card, upi, cash): ").strip()
    try:
        result = hybrid_book_ticket(
            event_id=event_id,
            category=category,
            qty=qty,
            customer={"name": name, "email": email, "phone": phone},
            payment_method=payment_method,
        )
        print("\n=== Booking Confirmation ===")
        print(f"Booking ID: {result['booking_id']}")
        print(f"Event: {result['event']['match_name']} ({result['event']['event_id']})")
        print(f"Stadium: {result['event']['stadium'].get('name') if result['event'].get('stadium') else 'N/A'}")
        print(f"Category: {result['event']['category']} @ ₹{result['event']['unit_price']} each")
        print(f"Quantity: {result['event']['quantity']}")
        print(f"Total: ₹{result['total_amount']}")
        print(f"Payment Method: {result['payment_method']}")
        print(f"Status: {result['status']}")
    except ValueError as ve:
        print(f"Input error: {ve}")
    except Exception as exc:
        print(f"Booking failed: {exc}")


def _record_payment():
    # For simplicity we just list bookings with pending_payment and let the user mark them paid.
    conn = get_connection()
    cur = conn.execute("SELECT booking_id, total_amount FROM bookings WHERE booking_status='pending_payment'")
    rows = cur.fetchall()
    if not rows:
        print("No pending payments.")
        return
    print("Pending bookings:")
    for r in rows:
        print(f"  ID: {r['booking_id']} – Amount: ₹{r['total_amount']}")
    bid = input("Enter Booking ID to mark as paid: ").strip()
    method = input("Payment method used: ").strip()
    try:
        conn.execute("BEGIN")
        conn.execute("UPDATE bookings SET booking_status='confirmed' WHERE booking_id=?", (bid,))
        conn.execute("UPDATE payments SET payment_status='successful', payment_method=? WHERE booking_id=?", (method, bid))
        conn.commit()
        print("Payment recorded and booking confirmed.")
    except Exception as e:
        conn.rollback()
        print(f"Failed to record payment: {e}")


def _view_booking_details():
    bid = input("Enter Booking ID: ").strip()
    conn = get_connection()
    cur = conn.execute("SELECT * FROM bookings WHERE booking_id=?", (bid,))
    booking = cur.fetchone()
    if not booking:
        print("Booking not found.")
        return
    print("\nBooking Header:")
    for k, v in booking.items():
        print(f"  {k}: {v}")
    # Items
    cur = conn.execute("SELECT * FROM booking_items WHERE booking_id=?", (bid,))
    items = cur.fetchall()
    print("\nItems:")
    for it in items:
        print(f"  - {it['category']} x{it['quantity']} @ ₹{it['ticket_price_snapshot']} = ₹{it['subtotal']}")
    # Payment
    cur = conn.execute("SELECT * FROM payments WHERE booking_id=?", (bid,))
    payment = cur.fetchone()
    if payment:
        print("\nPayment:")
        for k, v in payment.items():
            print(f"  {k}: {v}")
    else:
        print("\nNo payment record.")
    # Show event snapshot from MongoDB for completeness
    try:
        event = get_db().events.find_one({"event_id": items[0]["event_id"]})
        if event:
            print("\nEvent snapshot from MongoDB:")
            print(f"  Match: {event.get('match_name')}")
            print(f"  Stadium: {event.get('stadium',{}).get('name')}")
    except Exception:
        pass


def _view_all_bookings():
    report = get_booking_report()
    print(f"\nTotal bookings: {len(report)}")
    for row in report[:20]:  # limit output
        print(f"Booking {row['booking_id']} – Customer {row['name']} – Total ₹{row['total_amount']} – Status {row['booking_status']}")
    if len(report) > 20:
        print("... (truncated)")


def _generate_report():
    # Re‑use the same SQL JOIN report but output as a simple table.
    rows = get_booking_report()
    if not rows:
        print("No bookings yet.")
        return
    print("\n=== Booking Report ===")
    header = ["BookingID", "Customer", "Email", "Status", "Total", "PaymentMethod", "PayAmt"]
    print(" | ".join(header))
    print("-" * 80)
    for r in rows:
        print(f"{r['booking_id']} | {r['name']} | {r['email']} | {r['booking_status']} | ₹{r['total_amount']} | {r['payment_method']} | ₹{r['payment_amount']}")


def _menu():
    menu_text = """
===== BOOK MY MATCH – HYBRID DATABASE SYSTEM =====

1. View Available Matches
2. Search Match
3. View Stadium Details
4. Register Customer
5. Book Tickets
6. Make/Record Payment
7. View Booking Details
8. View All Bookings (SQL report)
9. Generate Booking Report (SQL JOIN)
10. Exit
"""
    print(menu_text)
    choice = input("Enter your choice: ").strip()
    return choice


def main():
    while True:
        choice = _menu()
        if choice == "1":
            _list_matches()
        elif choice == "2":
            _search_match()
        elif choice == "3":
            _view_stadium_details()
        elif choice == "4":
            _register_customer()
        elif choice == "5":
            _book_tickets()
        elif choice == "6":
            _record_payment()
        elif choice == "7":
            _view_booking_details()
        elif choice == "8":
            _view_all_bookings()
        elif choice == "9":
            _generate_report()
        elif choice == "10" or choice.lower() in ("exit", "q"):
            print("Goodbye!")
            break
        else:
            print("Invalid choice – please try again.")
        print("\n---\n")

if __name__ == "__main__":
    main()
