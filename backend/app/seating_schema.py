"""MongoDB server-side validation ($jsonSchema) for the Phase 3A seating collections.

Applied with `collMod` (or `create`) using validationLevel "moderate" and validationAction "error":
new/updated documents must match, but documents that already exist and do not match never block unrelated updates.
This is a second line of defence behind the service-layer validation in seating.py, and it is best effort:
if the database user may not run collMod, or the server does not support it (e.g. mongomock in tests) the application
logs a warning and keeps working because seating.py validates everything itself.
"""
import logging

log = logging.getLogger("bookmymatch")

_INT = ["int", "long"]
INVENTORY_STATUSES = ["available", "held", "booked", "blocked"]


def _obj(required, props):
    return {"$jsonSchema": {"bsonType": "object", "required": required, "properties": props}}


VALIDATORS = {
    "seat_layouts": _obj(["stadium_id", "name", "is_active"], {
        "stadium_id": {"bsonType": _INT}, "name": {"bsonType": "string", "minLength": 2, "maxLength": 80},
        "is_active": {"bsonType": "bool"}}),
    "seat_categories": _obj(["layout_id", "name", "name_key", "base_price"], {
        "layout_id": {"bsonType": _INT}, "name": {"bsonType": "string", "minLength": 1, "maxLength": 50},
        "base_price": {"bsonType": "decimal"}}),
    "seat_stands": _obj(["layout_id", "name", "code", "is_active"], {
        "layout_id": {"bsonType": _INT}, "name": {"bsonType": "string", "minLength": 2, "maxLength": 60},
        "code": {"bsonType": "string", "pattern": "^[A-Z0-9]{1,12}$"}, "is_active": {"bsonType": "bool"}}),
    "seat_sections": _obj(["layout_id", "stand_id", "name", "code", "category_id", "is_active"], {
        "layout_id": {"bsonType": _INT}, "stand_id": {"bsonType": _INT}, "category_id": {"bsonType": _INT},
        "code": {"bsonType": "string", "pattern": "^[A-Z0-9]{1,12}$"}, "is_active": {"bsonType": "bool"}}),
    "seat_rows": _obj(["layout_id", "section_id", "label"], {
        "layout_id": {"bsonType": _INT}, "section_id": {"bsonType": _INT},
        "label": {"bsonType": "string", "pattern": "^[A-Z0-9]{1,8}$"}}),
    "seats": _obj(["layout_id", "stadium_id", "stand_id", "section_id", "row_id", "seat_number", "seat_key", "category_id", "is_active"], {
        "seat_number": {"bsonType": _INT, "minimum": 1, "maximum": 9999},
        "seat_key": {"bsonType": "string", "minLength": 5, "maxLength": 40},
        "category_id": {"bsonType": _INT}, "is_active": {"bsonType": "bool"},
        "price_override": {"bsonType": ["decimal", "null"]}}),
    "match_seat_inventory": _obj(["match_id", "seat_id", "layout_id", "section_id", "row_id", "category_id", "status", "is_active"], {
        "match_id": {"bsonType": _INT}, "seat_id": {"bsonType": _INT},
        "status": {"enum": INVENTORY_STATUSES}, "is_active": {"bsonType": "bool"}}),
}


def ensure_seating_validators(db=None):
    from .database import get_db
    db = db if db is not None else get_db()
    existing = set(db.list_collection_names())
    for name, validator in VALIDATORS.items():
        try:
            if name in existing:
                db.command("collMod", name, validator=validator, validationLevel="moderate", validationAction="error")
            else:
                db.create_collection(name, validator=validator, validationLevel="moderate", validationAction="error")
        except Exception as e:  # unsupported or not permitted: keep going, the service layer validates anyway
            log.warning("Could not apply MongoDB validator to %s: %s", name, e)


if __name__ == "__main__":   # python -m app.seating_schema  -> apply the validators by hand (needs collMod privilege)
    logging.basicConfig(level=logging.INFO)
    ensure_seating_validators()
    print("Done. Warnings above (if any) mean the validator was skipped; the application still validates everything itself.")
