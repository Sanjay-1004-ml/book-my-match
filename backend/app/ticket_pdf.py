"""PDF tickets: one A5 page per ticket, vector QR so it stays sharp when printed or zoomed."""
import io
from datetime import datetime
from reportlab.lib.pagesizes import A5
from reportlab.pdfgen import canvas
from .qr import draw_qr

NAVY = (0.043, 0.071, 0.125)
GREEN = (0.133, 0.773, 0.369)


def _dt(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%d %b %Y, %I:%M %p")
    except (TypeError, ValueError):
        return "-"


def _page(c: canvas.Canvas, t: dict):
    w, h = A5
    c.setFillColorRGB(*NAVY); c.rect(0, h - 92, w, 92, stroke=0, fill=1)
    c.setFillColorRGB(1, 1, 1); c.setFont("Helvetica-Bold", 22); c.drawString(28, h - 46, "BOOK MY MATCH")
    c.setFillColorRGB(*GREEN); c.setFont("Helvetica-Bold", 10); c.drawString(28, h - 66, "DIGITAL ENTRY TICKET")
    c.setFillColorRGB(1, 1, 1); c.setFont("Helvetica", 9); c.drawRightString(w - 28, h - 46, t["sport"] or "")
    y = h - 126
    c.setFillColorRGB(0.05, 0.07, 0.12); c.setFont("Helvetica-Bold", 19); c.drawString(28, y, (t["match"] or "")[:34])
    y -= 22
    rows = [("Ticket ID", t["ticket_id"]), ("Booking reference", t["booking_reference"]), ("Customer", t["customer_name"] or "-"),
            ("Stadium", f'{t["stadium"]}, {t["location"]}'), ("Date", t["match_date"] or "-"), ("Time", t["timing"] or "-"),
            ("Category", t["category"]), ("Seat", t["seat_number"] or "Open seating (category ticket)"),
            ("Payment", (t["payment_status"] or "").upper() + " (demo)"), ("Ticket status", t["validity"].upper())]
    for k, v in rows:
        c.setFillColorRGB(0.4, 0.45, 0.55); c.setFont("Helvetica", 8.5); c.drawString(28, y, k.upper())
        c.setFillColorRGB(0.05, 0.07, 0.12); c.setFont("Helvetica-Bold", 10.5); c.drawString(150, y, str(v)[:44])
        y -= 17
    size = 150
    qx, qy = (w - size) / 2, 62
    if t["ticket_status"] == "valid" and t["validity"] == "valid" and t.get("qr_payload"):
        draw_qr(c, t["qr_payload"], qx, qy, size)
        c.setFillColorRGB(0.4, 0.45, 0.55); c.setFont("Helvetica", 8)
        c.drawCentredString(w / 2, qy - 12, "Scan at stadium entry  |  Backup code: " + t["backup_code"])
    else:
        c.setStrokeColorRGB(0.8, 0.1, 0.1); c.setFillColorRGB(0.8, 0.1, 0.1)
        c.setLineWidth(3); c.rect(qx, qy + 45, size, 60, stroke=1, fill=0)
        c.setFont("Helvetica-Bold", 26); c.drawCentredString(w / 2, qy + 66, t["validity"].upper())
        if t.get("used_at"):
            c.setFont("Helvetica", 8); c.drawCentredString(w / 2, qy + 28, "Entry recorded " + _dt(t["used_at"]))
    c.setFillColorRGB(0.5, 0.5, 0.5); c.setFont("Helvetica", 7.5)
    c.drawCentredString(w / 2, 24, "Demo ticket for a demo payment. No real money was charged. One entry per ticket.")


def build_tickets_pdf(tickets: list) -> bytes:
    """tickets: dicts from ticketing.tickets_out(..., with_qr=True)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A5)
    c.setTitle("Book My Match tickets")
    for t in tickets:
        _page(c, t)
        c.showPage()
    c.save()
    return buf.getvalue()
