from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import Response
from app.services.report_service import generate_sitrep

router = APIRouter(prefix="/reports", tags=["Reports"])

@router.get("/sitrep/{lake_id}")
def get_sitrep(lake_id: int):
    # In a real scenario, fetch data from DB using lake_id
    # Here using placeholder data
    lake_data = {"name": f"Lake {lake_id}", "lat": 36.5, "lon": 74.5, "risk": "High"}
    
    pdf_bytes = generate_sitrep(lake_data)
    
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=SITREP_{lake_id}.pdf"}
    )
