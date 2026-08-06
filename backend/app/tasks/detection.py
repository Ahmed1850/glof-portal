from app.services.celery_app import celery_app
from app.services.satellite_cascade import detect_lakes_cascade
from app.db.session import SessionLocal

@celery_app.task
def check_for_glof_growth():
    db = SessionLocal()
    try:
        result = detect_lakes_cascade(db=db)
        lakes = result.get("lakes") or []
        source = result.get("source_label") or result.get("source_used")
        print(f"Detection via {source}: {len(lakes)} lakes")

        # Logic to compare against historical baseline
        for lake in lakes:
            area = lake.get("area_ha") or 0
            if area > 30:  # Placeholder threshold
                # Trigger alert (e.g., via socketio or SMS)
                print(f"ALERT: High growth detected for {lake['name']} ({area} ha)")
        return {
            "message": "Detection check completed",
            "source_used": result.get("source_used"),
            "total": len(lakes),
        }
    finally:
        db.close()
