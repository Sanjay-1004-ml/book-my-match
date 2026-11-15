import secrets


def new_reference() -> str:
    """Human-friendly unique booking reference, e.g. BMM-F3HBM7JG (same format as before the migration)."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "BMM-" + "".join(secrets.choice(alphabet) for _ in range(8))
