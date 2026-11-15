"""Pure pricing logic (no framework imports) so it can be unit-tested alone."""
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")


def money(x) -> Decimal:
    return Decimal(x).quantize(CENT, rounding=ROUND_HALF_UP)


def pct(amount: Decimal, percent: Decimal) -> Decimal:
    return money(Decimal(amount) * Decimal(percent) / Decimal(100))


def ticket_quote(unit_price, quantity: int, fee_percent, tax_percent) -> dict:
    unit = money(unit_price)
    subtotal = money(unit * quantity)
    fee = pct(subtotal, fee_percent)
    tax = pct(subtotal + fee, tax_percent)
    return {"unit_price": unit, "quantity": quantity, "subtotal": subtotal,
            "fee": fee, "tax": tax, "total": money(subtotal + fee + tax)}


def food_quote(lines, tax_percent, fee_percent=0) -> dict:
    """lines: iterable of (unit_price, quantity). Same shape as ticket_quote: fee on the subtotal, tax on subtotal + fee.
    With fee_percent=0 (the default) the result is identical to the original subtotal + tax calculation."""
    subtotal = money(sum((money(p) * q for p, q in lines), Decimal(0)))
    fee = pct(subtotal, fee_percent)
    tax = pct(subtotal + fee, tax_percent)
    return {"subtotal": subtotal, "fee": fee, "tax": tax, "total": money(subtotal + fee + tax)}


def seat_quote(unit_prices, fee_percent, tax_percent) -> dict:
    """Phase 3A: price a set of individual seats (each may have a different price). Same fee/tax rules as ticket_quote:
    fee on the subtotal, tax on subtotal + fee. With equal prices the result equals ticket_quote(price, len(prices), ...)."""
    prices = [money(p) for p in unit_prices]
    subtotal = money(sum(prices, Decimal(0)))
    fee = pct(subtotal, fee_percent)
    tax = pct(subtotal + fee, tax_percent)
    return {"quantity": len(prices), "subtotal": subtotal, "fee": fee, "tax": tax, "total": money(subtotal + fee + tax)}
