## Hybrid Database Architecture

The **Book My Match** application now uses **two data stores**:

| Store | Purpose | What lives there |
|---|---|---|
| **MongoDB Atlas** | Flexible catalogue & inventory | `events`, `matches`, `stadiums`, `ticket_categories`, `ticket_inventory`, `payments`, `tickets`, `entry_logs` – all of the data that benefits from nested documents, rich queries, and atomic updates without multi‑document transactions. |
| **SQLite (local file)** | Structured, relational booking information | `customers`, `bookings`, `booking_items`, `payments` – tables with primary/foreign keys, constraints and classic SQL `JOIN`s for reporting. |

### Data flow
1. **Read‑only catalogue** – When a user starts a booking the service fetches the event document from MongoDB to get the current price and seat layout.
2. **Snapshot & persist** – The price, event name and selected category are **snap‑shotted** and written to SQLite together with the customer record, a booking header, line items and a payment record inside a single SQLite transaction.
3. **Cross‑reference** – `booking_items.event_id` stores the stable `event_id` from MongoDB, allowing later look‑ups (`JOIN`‑like) without a relational FK.
4. **Confirmation** – After the transaction succeeds the API (or the console UI) returns a combined view containing both the relational data and the MongoDB event snapshot.

### Why a hybrid approach?
*MongoDB* gives us schemaless flexibility for complex, nested event data and atomic inventory updates.
*SQLite* gives us mature relational guarantees (FKs, constraints, ACID transactions) for financial‑type records where data integrity is critical.

---

## Failure handling & consistency limits
| Failure scenario | System reaction | User message |
|---|---|---|
| **MongoDB unavailable before price lookup** | Abort the request; no SQLite transaction is started. | "Unable to retrieve event details – please try again later."
| **SQLite transaction error** (e.g., constraint violation, disk full) | Roll back the whole transaction; no partial rows remain. | "Booking failed – please verify your input or try again later."
| **SQLite succeeds but later MongoDB read fails** (e.g., to show confirmation) | Return the booking data from SQLite with a warning that event details could not be fetched. | "Booking recorded, but event information is temporarily unavailable."
| **Both DBs down** | Return HTTP 503 / console error. | "Service temporarily unavailable – both databases are offline."

> **Important:** SQLite cannot participate in the MongoDB atomic inventory update. If a race condition occurs between the inventory decrement and the SQLite transaction, the booking may be persisted while the inventory was not actually reserved. In production you would use a MongoDB multi‑document transaction (Atlas supports it) to eliminate that gap.

---

## Design justification (one‑page summary)
* See `docs/design_justification.md`.
