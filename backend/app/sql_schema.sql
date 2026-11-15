-- SQLite schema for hybrid booking system

-- 1. Customers
CREATE TABLE IF NOT EXISTS customers (
    customer_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    phone TEXT
);

-- 2. Bookings (header)
CREATE TABLE IF NOT EXISTS bookings (
    booking_id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL,
    booking_date TEXT NOT NULL,               -- ISO‑8601 timestamp
    total_amount REAL NOT NULL CHECK (total_amount >= 0),
    booking_status TEXT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);

-- 3. Booking_Items (snapshot of event info & price)
CREATE TABLE IF NOT EXISTS booking_items (
    item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL,
    event_id TEXT NOT NULL,                     -- stable ID from MongoDB
    event_name_snapshot TEXT NOT NULL,
    category TEXT NOT NULL,
    ticket_price_snapshot REAL NOT NULL,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    subtotal REAL NOT NULL,
    FOREIGN KEY (booking_id) REFERENCES bookings(booking_id)
);

-- 4. Payments (record of payment attempts)
CREATE TABLE IF NOT EXISTS payments (
    payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL,
    payment_method TEXT NOT NULL,
    payment_amount REAL NOT NULL CHECK (payment_amount >= 0),
    payment_status TEXT NOT NULL,
    payment_date TEXT NOT NULL,
    FOREIGN KEY (booking_id) REFERENCES bookings(booking_id)
);
