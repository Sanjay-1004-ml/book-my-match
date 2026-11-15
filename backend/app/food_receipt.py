"""Food order receipt PDF (A5). Same look and tooling as ticket_pdf.py; the QR holds only the order number and pickup code."""
import io
from datetime import datetime
from reportlab.lib.pagesizes import A5
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas
from .qr import draw_qr

NAVY = (0.043, 0.071, 0.125)
GREEN = (0.133, 0.773, 0.369)
INK = (0.05, 0.07, 0.12)
GREY = (0.4, 0.45, 0.55)
LEFT, RIGHT_PAD = 28, 28


def _dt(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%d %b %Y, %I:%M %p")
    except (TypeError, ValueError):
        return "-"


def _inr(x) -> str:
    return f"INR {float(x or 0):,.2f}"


def _header(c, w, h, title):
    c.setFillColorRGB(*NAVY); c.rect(0, h - 78, w, 78, stroke=0, fill=1)
    c.setFillColorRGB(1, 1, 1); c.setFont("Helvetica-Bold", 20); c.drawString(LEFT, h - 40, "BOOK MY MATCH")
    c.setFillColorRGB(*GREEN); c.setFont("Helvetica-Bold", 10); c.drawString(LEFT, h - 58, title)


def _footer(c, w):
    c.setFillColorRGB(0.5, 0.5, 0.5); c.setFont("Helvetica", 7.5)
    c.drawCentredString(w / 2, 22, "Demo receipt for a demo payment. No real money was charged.")


def build_receipt_pdf(o: dict) -> bytes:
    """o: a paid order dict from food_service.food_orders_out(..., with_qr=True)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A5)
    c.setTitle(f'Food receipt {o["order_number"]}')
    w, h = A5
    _header(c, w, h, "FOOD ORDER RECEIPT")
    y = h - 106

    def kv(k, v):
        nonlocal y
        c.setFillColorRGB(*GREY); c.setFont("Helvetica", 8.5); c.drawString(LEFT, y, k.upper())
        c.setFillColorRGB(*INK); c.setFont("Helvetica-Bold", 10); c.drawString(150, y, str(v)[:46])
        y -= 16

    kv("Order number", o["order_number"])
    kv("Order ID", o["id"])
    kv("Payment", f'{o["payment_status"].upper()} (demo)')
    if o.get("transaction_id"):
        kv("Transaction ID", o["transaction_id"])
    kv("Paid at", _dt(o.get("paid_at")))
    if o.get("booking_reference"):
        kv("Booking ref", o["booking_reference"])

    # pickup block: details on the left, QR on the right
    y -= 6
    p = o.get("pickup") or {}
    top = y
    c.setFillColorRGB(*GREEN); c.setFont("Helvetica-Bold", 10); c.drawString(LEFT, y, "PICKUP"); y -= 18
    c.setFillColorRGB(*INK); c.setFont("Helvetica-Bold", 22); c.drawString(LEFT, y, o.get("pickup_code") or "-"); y -= 16
    text_w = w - LEFT - RIGHT_PAD - 120
    lines = []
    if p.get("stadium"):
        lines += simpleSplit(f'{p["stadium"]}, {p["location"]}', "Helvetica-Bold", 9, text_w)
        lines += simpleSplit(f'{p["match"]} - {p["match_date"]} {p["timing"]}', "Helvetica", 9, text_w)
    lines += simpleSplit(p.get("instructions") or "", "Helvetica", 8.5, text_w)
    c.setFont("Helvetica", 8.5)
    for ln in lines[:7]:
        c.drawString(LEFT, y, ln); y -= 11
    size = 104
    if o.get("qr_payload"):
        draw_qr(c, o["qr_payload"], w - RIGHT_PAD - size, top - size + 6, size)
    y = min(y, top - size) - 14

    # items
    c.setStrokeColorRGB(0.85, 0.87, 0.9); c.line(LEFT, y + 8, w - RIGHT_PAD, y + 8)
    c.setFillColorRGB(*GREY); c.setFont("Helvetica-Bold", 8.5); c.drawString(LEFT, y - 6, "ITEM"); c.drawRightString(w - RIGHT_PAD, y - 6, "AMOUNT")
    y -= 22
    for it in o["items"]:
        if y < 130:
            _footer(c, w); c.showPage(); _header(c, w, h, "FOOD ORDER RECEIPT (continued)"); y = h - 106
        c.setFillColorRGB(*INK); c.setFont("Helvetica", 10)
        c.drawString(LEFT, y, f'{it["name"][:30]} x {it["quantity"]}  @ {_inr(it["unit_price"])}'[:58])
        c.drawRightString(w - RIGHT_PAD, y, _inr(it["line_total"])); y -= 15
    y -= 4
    rows = [("Subtotal", o["subtotal"])]
    if o.get("fee"):
        rows.append(("Service fee", o["fee"]))
    rows.append(("Tax", o["tax"]))
    for k, v in rows:
        if y < 70:
            _footer(c, w); c.showPage(); _header(c, w, h, "FOOD ORDER RECEIPT (continued)"); y = h - 106
        c.setFillColorRGB(*GREY); c.setFont("Helvetica", 10); c.drawString(LEFT, y, k); c.drawRightString(w - RIGHT_PAD, y, _inr(v)); y -= 14
    c.setFillColorRGB(*INK); c.setFont("Helvetica-Bold", 12)
    c.drawString(LEFT, y - 2, "Total paid (demo)"); c.drawRightString(w - RIGHT_PAD, y - 2, _inr(o["total"]))
    _footer(c, w)
    c.showPage(); c.save()
    return buf.getvalue()
