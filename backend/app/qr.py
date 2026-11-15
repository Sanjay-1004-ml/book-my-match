"""QR code generation with ReportLab's built-in encoder (no extra dependency).
The QR content is only the opaque ticket token; no personal or payment data ever goes into it."""
import base64
from reportlab.graphics.barcode import qrencoder


def qr_matrix(text: str, level: str = "M") -> list:
    q = qrencoder.QRCode(None, getattr(qrencoder.QRErrorCorrectLevel, level))
    q.addData(text)
    q.make()
    return [[bool(c) for c in row] for row in q.modules]


def _runs(row):
    x = 0
    n = len(row)
    while x < n:
        if row[x]:
            start = x
            while x < n and row[x]:
                x += 1
            yield start, x - start
        else:
            x += 1


def qr_svg(text: str, border: int = 4) -> str:
    m = qr_matrix(text)
    size = len(m) + 2 * border
    path = "".join(f"M{border + x} {border + y}h{w}v1h-{w}z" for y, row in enumerate(m) for x, w in _runs(row))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" shape-rendering="crispEdges">'
            f'<rect width="{size}" height="{size}" fill="#fff"/><path d="{path}" fill="#000"/></svg>')


def qr_data_uri(text: str) -> str:
    """Safe to use as <img src>: an image cannot run scripts."""
    return "data:image/svg+xml;base64," + base64.b64encode(qr_svg(text).encode()).decode()


def draw_qr(canvas, text: str, x: float, y: float, size: float, border: int = 4):
    """Draw a crisp vector QR (with white quiet zone) into a ReportLab canvas; (x, y) is the bottom-left corner."""
    m = qr_matrix(text)
    n = len(m) + 2 * border
    mod = size / n
    canvas.setFillColorRGB(1, 1, 1)
    canvas.rect(x, y, size, size, stroke=0, fill=1)
    canvas.setFillColorRGB(0, 0, 0)
    for r, row in enumerate(m):
        for c0, w in _runs(row):
            # tiny overlap avoids hairline seams between modules in some PDF viewers
            canvas.rect(x + (border + c0) * mod, y + size - (border + r + 1) * mod - 0.03, w * mod + 0.03, mod + 0.06, stroke=0, fill=1)
