from app.services.celery_app import celery_app
from app.utils.gee_detection import detect_glacial_lakes

@celery_app.task
def check_for_glof_growth():
    # Detect lakes
    lakes = detect_glacial_lakes()
    
    # Logic to compare against historical baseline
    for lake in lakes:
        if lake["area_ha"] > 30: # Placeholder threshold
            # Trigger alert (e.g., via socketio or SMS)
            print(f"ALERT: High growth detected for {lake['name']}")
    return "Detection check completed"
