# Phase 3A – Stadium seating database, backend APIs and admin management

Scope: data model, validation, admin APIs, read-only public APIs, server-side seat pricing and the admin UI.
**Not in scope (and not implemented): seat selection UI, holds/locking, expiry, payment confirmation of seats (Phase 3B/3C/3D).**

## 1. Design decisions (read these first)

| Decision | Why | Consequence |
|---|---|---|
| **One seating layout per stadium** (`seat_layouts.stadium_id` is unique) | "Every seat has a unique identifier within its stadium" becomes a plain unique index on `(stadium_id, seat_key)` | Different stadiums have different layouts; a stadium has one layout you edit. |
| **Dense per-match inventory**: one `match_seat_inventory` row per seat per match | Each match has its own status per seat; Phase 3B can lock a seat with a single atomic conditional update | Storage grows with seats x assigned matches (see 6). Only assign layouts to matches that need seat selection. |
| **Existing ticketing is untouched.** Category tickets (`ticket_inventory`, `bookings`, `payments`, `tickets`) are neither read nor written by Phase 3A | Existing bookings/payments must not change | **Resolved in Phase 3B-3D:** a match that has a layout now refuses category bookings, and a layout cannot be assigned to a match with live category bookings. (Original note:  a match that has BOTH category inventory and seat inventory can be sold twice (a category ticket and a seat). Before seat booking goes live, decide that seat-mode matches stop selling category tickets (recommended) or reconcile the two counts. Nothing breaks today because no UI sells seats yet.) |
| Seat price = `price_override` if set, else its category's `base_price`, resolved **live** | No denormalised price to drift when an admin edits a category | A booking (Phase 3D) must snapshot the unit price it charged. Changing a price never changes an existing booking. |
| Integer `_id`s from the `counters` collection (project convention); inventory rows use the string id `"<match_id>:<seat_id>"` | Keeps the frontend's `+id` conversions working; the string id makes "one row per seat per match" a primary-key guarantee | Seat ids are allocated in blocks (`next_ids`) so a 40,000-seat layout needs one counter update, not 40,000. |
| Admin-only writes, public reads | Matches/stadiums are already public | Reads never expose admin fields, inactive sections, or who holds a seat. |

## 2. Collections

| Collection | Purpose | Key fields |
|---|---|---|
| `seat_layouts` | one per stadium | `_id, stadium_id, name, is_active, created_by, created_at, updated_at` |
| `seat_categories` | seat categories and prices (per layout) | `_id, layout_id, name, name_key, base_price (Decimal128), color, description` |
| `seat_stands` | stands (North Stand, VIP Section ...) | `_id, layout_id, stadium_id, name, name_key, code, is_active, sort_order` |
| `seat_sections` | blocks inside a stand | `_id, layout_id, stadium_id, stand_id, name, name_key, code, category_id, is_active, sort_order` |
| `seat_rows` | rows inside a section | `_id, layout_id, stand_id, section_id, label, category_id (row override or null), start_number, sort_order` |
| `seats` | **individual seats** | `_id, layout_id, stadium_id, stand_id, section_id, row_id, row_label, seat_number, seat_key, category_id, price_override (Decimal128 or null), is_active` |
| `match_seat_inventory` | **per-match seat state** | `_id "<match>:<seat>", match_id, seat_id, layout_id, stadium_id, stand_id, section_id, row_id, category_id, status, is_active, created_at` (+ `hold_id, hold_expires_at, booking_id` added by Phase 3B/3C) |
| `seat_holds` | reserved for Phase 3B/3C (created empty, indexed, unused) | – |
| `matches` (existing) | gains OPTIONAL `seat_layout_id`, `seat_inventory_status` (`building`/`ready`), `seat_inventory_built_at` | existing matches simply do not have them |

`seat_key` = `<section code>-<row label>-<seat number>`, e.g. `NBLOCKA-A1-7`. Codes and labels are upper-case letters/digits only (no `-`), so the key is unambiguous and unique per stadium.
`status` is one of `available`, `held`, `booked`, `blocked`. Phase 3A only moves seats between `available` and `blocked`.
`is_active` on an inventory row = the seat is on sale (its seat AND section/stand switches are on). The Phase 3B lock query must include `is_active: true`.

## 3. Indexes (created automatically by `ensure_indexes` at startup; nothing to run by hand)

| Collection | Index | Purpose |
|---|---|---|
| seat_layouts | `(stadium_id)` **unique** | one layout per stadium |
| seat_categories | `(layout_id, name_key)` **unique** | no duplicate category names |
| seat_stands | `(layout_id, code)` **unique**, `(layout_id, name_key)` **unique** | |
| seat_sections | `(layout_id, code)` **unique**, `(stand_id, name_key)` **unique** | section code is part of `seat_key` |
| seat_rows | `(section_id, label)` **unique**, `(layout_id)` | no duplicate rows |
| seats | `(stadium_id, seat_key)` **unique**, `(row_id, seat_number)` **unique**, `(section_id)`, `(layout_id, category_id)` | duplicate seats are impossible even if the service check is bypassed |
| match_seat_inventory | `(match_id, seat_id)` **unique** (plus the string `_id`), `(match_id, status, is_active)`, `(match_id, section_id, status)`, `(match_id, row_id)`, `(seat_id)`, `(layout_id, status)` | availability counts, seat maps, guards |
| match_seat_inventory | `(status, hold_expires_at)`, `(hold_id)` sparse, `(booking_id)` sparse | **for Phase 3B/3C**: expiry sweeps, releasing/confirming a hold |
| seat_holds | `(hold_id)` unique, `(user_id, status)`, `(status, expires_at)`, `(match_id, status)` | **for Phase 3B/3C** |

**Validation:** `app/seating_schema.py` defines `$jsonSchema` validators for the seven seating collections (validationLevel `moderate`, action `error`). They are applied at startup with `collMod`/`create` on a best-effort basis: if your Atlas user lacks the `collMod` privilege the app logs a warning and keeps working, because `app/seating.py` validates everything itself. To apply them manually with an admin user: `cd backend && python -m app.seating_schema`.

## 4. API

All paths are under `/api`. Interactive docs: `/docs`. Errors: `{"detail": "..."}` (401 not logged in, 403 not an admin, 404 not found, 409 conflict/guard, 422 validation).

### Admin (administrators only)
| Method & path | Body / query | Notes |
|---|---|---|
| `GET /admin/seating/layouts?stadium_id=` | | list with totals |
| `POST /admin/seating/layouts` | `{stadium_id, name, categories?:[{name,base_price,color?}], stands?:[{name,code?,sections:[{name,code?,category,rows:[{label,seat_count,start_number?,category?}]}]}]}` | creates a whole layout; validated completely before anything is written; all-or-nothing |
| `GET /admin/seating/layouts/{id}` | | full tree with per-row seat counts, categories and totals (capacity) |
| `PUT /admin/seating/layouts/{id}` | `{name?, is_active?}` | cannot deactivate while assigned to a scheduled match |
| `DELETE /admin/seating/layouts/{id}` | | refused while assigned to any match |
| `POST /admin/seating/layouts/{id}/categories` | `{name, base_price, color?, description?}` | price: 0-1,000,000, max 2 decimals |
| `PUT /admin/seating/categories/{id}` | `{name?, base_price?, color?, description?}` | price change = future quotes only |
| `DELETE /admin/seating/categories/{id}` | | refused while in use |
| `POST /admin/seating/layouts/{id}/stands` | `{name, code?}` | |
| `PUT /admin/seating/stands/{id}` | `{name?, is_active?}` | `is_active` cascades to its sections (on sale / off sale) |
| `DELETE /admin/seating/stands/{id}` | | cascades; refused if any seat is held/booked |
| `POST /admin/seating/stands/{id}/sections` | `{name, code?, category_id}` | |
| `PUT /admin/seating/sections/{id}` | `{name?, category_id?, is_active?}` | category change re-categorises seats and inventory |
| `DELETE /admin/seating/sections/{id}` | | cascades; same guard |
| `POST /admin/seating/sections/{id}/rows` | `{label, seat_count (1-500), start_number?, category_id?}` | creates seats `start..start+count-1` |
| `PUT /admin/seating/rows/{id}` | `{label?, seat_count?, category_id?}` | growing adds seats; shrinking removes the highest numbers (guarded) |
| `DELETE /admin/seating/rows/{id}` | | |
| `GET /admin/seating/rows/{id}/seats` | | seats with effective price |
| `POST /admin/seating/rows/{id}/seats` | `{seat_number, category_id?, price_override?}` | one individual seat; duplicate number -> 409 |
| `PUT /admin/seating/seats/{id}` | `{category_id?, price_override? (null clears), is_active?}` | |
| `DELETE /admin/seating/seats/{id}` | | |
| `PUT /admin/matches/{match_id}/seating` | `{layout_id}` | layout must belong to the match's stadium, be active and non-empty; match must be scheduled; **idempotent** (repeat to finish an interrupted build); returns the inventory summary |
| `DELETE /admin/matches/{match_id}/seating` | | removes the match's seat inventory; refused if any seat is held/booked |
| `GET /admin/matches/{match_id}/seat-inventory/summary` | | totals by status, by category, by section; `sellable_available` |
| `GET /admin/matches/{match_id}/seat-inventory?section_id=&row_id=&status=&limit=&offset=` | | paged (max 500) |
| `PUT /admin/matches/{match_id}/seat-inventory/status` | `{seat_ids:[..<=2000], status:"blocked"\|"available"}` | only `available <-> blocked`; held/booked are skipped and counted |

### Public (read-only, no login, like matches and stadiums)
| Method & path | Notes |
|---|---|
| `GET /stadiums/{id}/seating` | structure (active stands/sections), categories, total seats; `{seating_enabled:false}` if none |
| `GET /matches/{id}/seating` | section-level availability for one match; `{seating_enabled:false}` for matches without a layout (never an error) |
| `GET /matches/{id}/seating/sections/{section_id}` | rows and seats with `status` (`available`/`held`/`booked`/`unavailable`), `selectable`, server price |
| `POST /matches/{id}/seat-quote` | `{seat_ids}` only (a `price`/`total` field is rejected with 422). Returns server-calculated `items`, `subtotal`, `fee`, `tax`, `total` using the existing `FEE_PERCENT` and `TICKET_TAX_PERCENT` (fee on subtotal, tax on subtotal + fee). **Quote only: nothing is reserved.** Max `MAX_TICKETS_PER_BOOKING` seats; 409 lists unavailable seats. |

## 5. Preparing for Phase 3B and 3C (recommended concurrency-safe design; deliberately NOT implemented)

Phase 3A guarantees the foundations: one atomic unit per seat per match (`match_seat_inventory` row), a status field, an `is_active` sellability flag, the unique key per seat per match, and the indexes below. No locking code exists yet, so nothing can claim to be "half-working".

**3B – hold (reserve) seats**
1. Insert a `seat_holds` document `{hold_id (uuid), match_id, user_id, seat_ids, status:"active", expires_at, created_at}`.
2. For each requested seat run ONE atomic conditional update:
   `find_one_and_update({match_id, seat_id, is_active:true, $or:[{status:"available"},{status:"held", hold_expires_at:{$lte:now}}]}, {$set:{status:"held", hold_id, hold_expires_at}})`.
   Only one concurrent request can win a seat; the loser sees `None`.
3. If any seat fails, **release the ones you won**: `update_many({hold_id, status:"held"}, {$set:{status:"available"}, $unset:{hold_id:"", hold_expires_at:""}})`, mark the hold `failed`, return 409 with the unavailable seat ids. (On a replica-set Atlas cluster you may instead wrap steps 2-3 in a multi-document transaction; keep the compensation path anyway.)
4. Always price from the server (`quote_seats`) and snapshot the unit prices into the hold/booking.
5. Limit seats per hold to `MAX_TICKETS_PER_BOOKING` and holds per user to avoid hoarding.

**Expiry** – never rely on a TTL index to free seats (TTL deletes documents, it does not update them, and runs only about once a minute). Use both: (a) *lazy*: the lock predicate above treats an expired hold as available; (b) *sweeper*: extend the existing `_release_expired_holds` loop in `main.py` to run `update_many({status:"held", hold_expires_at:{$lte:now}}, ...)` and mark `seat_holds` `expired`. Public seat maps must apply the same "expired hold = available" rule.

**3C – payment outcome**
- *Failure/cancel*: release by `hold_id` (as in step 3), idempotently.
- *Success*: `update_many({hold_id, status:"held"}, {$set:{status:"booked", booking_id}})` and require `modified_count == len(seat_ids)`; otherwise (hold expired and a rival took a seat) do not issue tickets, mark the payment for refund. Decide the policy for a payment that arrives just after expiry: confirm only if every seat still carries this `hold_id`.
- Make both operations idempotent with `hold_id` and the existing payment `transaction_id` uniqueness.
- Issue tickets with the seat (`seat_id`, `seat_key`) – the existing unique `(booking_id, seq)` index already prevents duplicate issuing.

**Indexes needed (already created by Phase 3A)**: `match_seat_inventory (match_id, seat_id)` unique; `(match_id, status, is_active)`; `(status, hold_expires_at)`; `(hold_id)` sparse; `(booking_id)` sparse; `seat_holds (hold_id)` unique, `(user_id, status)`, `(status, expires_at)`, `(match_id, status)`.

Guards already in place today: deleting/renaming/unassigning anything that contains a `held` or `booked` seat is refused with 409, so a future hold can never be orphaned by an admin edit.

## 6. Storage and operations

* Estimated cost (an estimate, not measured): about 0.3-0.5 KB per inventory row including indexes, so a 40,000-seat stadium costs roughly 12-20 MB **per assigned match**. A free M0 cluster has 512 MB: assign layouts only to matches that need them. `SEATING_MAX_SEATS_PER_LAYOUT` (default 60,000) caps a layout.
* Assigning a layout to a match inserts all inventory rows in batches of 1,000 (a few seconds for tens of thousands of seats). If the request is interrupted, repeat it: the build is idempotent and never overwrites a seat that already has a status.
* Seats added to a layout later are added to the inventory of matches that already use it. Matches that are not yet assigned pick them up on assignment.
* No migration is needed and nothing existing is modified. Rolling back = unassign layouts, delete layouts (admin UI), optionally drop the seating collections.
