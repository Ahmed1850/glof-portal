# seed_lakes.py
from app.db.session import SessionLocal
from app.models.lake import Lake as LakeModel
from app.utils.risk import calculate_risk

# Realistic glacial lakes of Gilgit-Baltistan (approximate data)
REALISTIC_LAKES = [
    {"name": "Attabad Lake", "area_ha": 280.0, "latitude": 36.318, "longitude": 74.865},
    {"name": "Shisper Lake", "area_ha": 48.5, "latitude": 36.412, "longitude": 74.623},
    {"name": "Passu Lake", "area_ha": 9.2, "latitude": 36.468, "longitude": 74.895},
    {"name": "Rush Lake", "area_ha": 13.8, "latitude": 36.174, "longitude": 74.885},
    {"name": "Satpara Lake", "area_ha": 28.0, "latitude": 35.225, "longitude": 75.632},
    {"name": "Borit Lake", "area_ha": 7.5, "latitude": 36.432, "longitude": 74.862},
    {"name": "Khurdopin Lake", "area_ha": 22.0, "latitude": 36.385, "longitude": 75.112},
    {"name": "Sokha Lake", "area_ha": 11.4, "latitude": 35.918, "longitude": 75.421},
    {"name": "Ghamu Bar Lake", "area_ha": 16.7, "latitude": 36.291, "longitude": 74.978},
    {"name": "Baltoro Lake", "area_ha": 35.0, "latitude": 35.752, "longitude": 76.435},
    {"name": "Shimshal Lake", "area_ha": 19.5, "latitude": 36.485, "longitude": 75.325},
    {"name": "Hispar Lake", "area_ha": 14.2, "latitude": 36.178, "longitude": 75.187},
]

def seed_lakes():
    db = SessionLocal()
    try:
        # 1. Delete all existing lakes
        deleted = db.query(LakeModel).delete()
        db.commit()
        print(f"Deleted {deleted} old lakes.")

        # 2. Insert new realistic lakes
        for data in REALISTIC_LAKES:
            risk = calculate_risk(data["area_ha"])
            lake = LakeModel(
                name=data["name"],
                area_ha=data["area_ha"],
                risk_level=risk,
                latitude=data["latitude"],
                longitude=data["longitude"],
            )
            db.add(lake)

        db.commit()
        print(f"Successfully added {len(REALISTIC_LAKES)} realistic lakes.")
        print("Done! Refresh your frontend.")

    except Exception as e:
        db.rollback()
        print("Error:", e)
    finally:
        db.close()


if __name__ == "__main__":
    seed_lakes()