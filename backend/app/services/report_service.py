import io
from datetime import datetime, timezone


def _minimal_pdf_bytes(lines: list[str]) -> bytes:
    """Fallback PDF generator using only Python stdlib if reportlab is unavailable."""
    escaped = [
        line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        for line in lines
    ]
    text_ops = ["BT", "/F1 11 Tf", "50 750 Td", "15 TL"]
    for idx, line in enumerate(escaped):
        if idx == 0:
            text_ops.append(f"({line}) Tj")
        else:
            text_ops.append(f"T* ({line}) Tj")
    text_ops.append("ET")
    stream_data = "\n".join(text_ops).encode("latin-1", errors="replace")

    objs = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n",
        f"4 0 obj\n<< /Length {len(stream_data)} >>\nstream\n".encode("latin-1") + stream_data + b"\nendstream\nendobj\n",
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for obj in objs:
        offsets.append(out.tell())
        out.write(obj)
    xref_pos = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode("latin-1"))
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode("latin-1"))
    out.write(
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("latin-1")
    )
    return out.getvalue()


def generate_sitrep(lake_data: dict) -> bytes:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"GLOF PORTAL - GEOSPATIAL SITUATION REPORT (SITREP)",
        f"Generated: {timestamp}",
        f"------------------------------------------------------------",
        f"Lake Name: {lake_data.get('name')} (ID: {lake_data.get('id', 'N/A')})",
        f"Coordinates (WGS84): {lake_data.get('lat')} N, {lake_data.get('lon')} E",
        f"WKT Geometry: {lake_data.get('geom', 'N/A')}",
        f"Surface Area: {lake_data.get('area_ha')} ha",
        f"Risk Classification: {lake_data.get('risk')}",
        f"Drainage Basin: {lake_data.get('basin_name', 'N/A')} ({lake_data.get('river', 'N/A')})",
        f"Nearest Glacier Distance: {lake_data.get('nearest_glacier_km', 'N/A')} km",
        f"Estimated Outburst Volume: {lake_data.get('volume_million_m3', 'N/A')} million m3",
        f"At-Risk Downstream Settlements: {', '.join(lake_data.get('settlements', [])) or 'N/A'}",
    ]
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas

        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=letter)
        y = 750
        c.setFont("Helvetica-Bold", 13)
        c.drawString(50, y, lines[0])
        y -= 20
        c.setFont("Helvetica", 10)
        for line in lines[1:]:
            c.drawString(50, y, line)
            y -= 16
        c.save()
        return buffer.getvalue()
    except Exception:
        return _minimal_pdf_bytes(lines)
