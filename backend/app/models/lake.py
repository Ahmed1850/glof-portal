import os
from sqlalchemy import Column, Integer, String, Float
from app.db.base import Base

# GeoAlchemy2 Geometry needs PostGIS (Postgres) or SpatiaLite (SQLite).
# Local default is plain SQLite without SpatiaLite, so store WKT text instead.
# Set DATABASE_URL to a PostGIS URL to use real spatial columns.
_USE_POSTGIS = os.getenv("DATABASE_URL", "sqlite:///./glof.db").startswith("postgresql")

if _USE_POSTGIS:
    from geoalchemy2 import Geometry
    _PointGeom = Geometry(geometry_type="POINT", srid=4326)
else:
    _PointGeom = String  # WKT text fallback for local SQLite


class Lake(Base):
    __tablename__ = "lakes"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    area_ha = Column(Float)
    risk_level = Column(String)
    # Point geometry (PostGIS) or WKT string (local SQLite)
    geom = Column(_PointGeom)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)


class KnownLake(Base):
    __tablename__ = "known_lakes"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    geom = Column(_PointGeom)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    area_ha = Column(Float, nullable=True)
    district = Column(String, nullable=True)
    source = Column(String, default="manual")
