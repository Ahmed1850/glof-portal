from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import io

def generate_sitrep(lake_data: dict) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    c.drawString(100, 750, f"MILITARY SITREP: GLOF Alert for {lake_data['name']}")
    c.drawString(100, 730, f"Coordinates: {lake_data['lat']}, {lake_data['lon']}")
    c.drawString(100, 710, f"Risk: {lake_data['risk']}")
    c.save()
    return buffer.getvalue()
