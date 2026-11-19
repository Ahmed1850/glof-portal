import { useState, useEffect } from 'react';
import {
  MapContainer,
  TileLayer,
  Marker,
  Popup,
  GeoJSON,
  Circle,
  Polyline,
  CircleMarker,
  useMapEvents,
} from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { IconSatellite, IconMoon, IconMap, IconGlobe, IconMountain, IconDownload } from './Icons';
import { API_BASE } from '../api';

// Fix default Leaflet marker icons
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon-2x.png',
  iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png',
});

// ==================== RISK CALCULATION ====================
const getRisk = (area, explicitRisk) => {
  if (explicitRisk && ['High', 'Medium', 'Low'].includes(explicitRisk)) {
    return explicitRisk;
  }
  if (area >= 20) return 'High';
  if (area >= 10) return 'Medium';
  return 'Low';
};

// Buffer radii in meters based on risk
const getZoneRadii = (risk) => {
  if (risk === 'High') return { danger: 5000, warning: 10000 };
  if (risk === 'Medium') return { danger: 2000, warning: 5000 };
  return { danger: 1000, warning: 2000 };
};

// ==================== 3D MARKER ====================
const get3DMarkerIcon = (riskLevel) => {
  let color = '#2dd48e';
  let glowColor = 'rgba(45, 212, 142, 0.6)';
  let core = '#6ee7b0';

  if (riskLevel === 'High') {
    color = '#f0433a';
    glowColor = 'rgba(240, 67, 58, 0.65)';
    core = '#fb736c';
  } else if (riskLevel === 'Medium') {
    color = '#f5a524';
    glowColor = 'rgba(245, 165, 36, 0.65)';
    core = '#fbc35e';
  }

  return L.divIcon({
    className: 'custom-3d-marker',
    html: `
      <div class="marker-3d-container">
        <div class="marker-ring" style="
          background-color: ${color};
          border: 1px solid ${color};
          box-shadow: 0 0 0 4px ${glowColor};
        "></div>
        <div class="marker-sphere" style="
          background: radial-gradient(circle at 35% 30%, #ffffff 0%, ${core} 45%, ${color} 100%);
          box-shadow: 
            0 0 16px ${glowColor},
            0 3px 10px rgba(0,0,0,0.45),
            inset 0 -2px 6px rgba(0,0,0,0.3);
          border: 2px solid rgba(255,255,255,0.7);
        "></div>
      </div>
    `,
    iconSize: [48, 48],
    iconAnchor: [24, 24],
    popupAnchor: [0, -20],
  });
};

// ==================== BASEMAPS (Google Maps + GIS Tiles) ====================
const MAP_LAYERS = {
  hybrid: {
    name: 'Google Hybrid',
    IconComponent: IconSatellite,
    url: 'https://{s}.google.com/vt/lyrs=s,h&x={x}&y={y}&z={z}',
    subdomains: ['mt0', 'mt1', 'mt2', 'mt3'],
    attribution: '&copy; Google Maps Satellite &amp; Hybrid',
  },
  satellite: {
    name: 'Google Satellite',
    IconComponent: IconGlobe,
    url: 'https://{s}.google.com/vt/lyrs=s&x={x}&y={y}&z={z}',
    subdomains: ['mt0', 'mt1', 'mt2', 'mt3'],
    attribution: '&copy; Google Maps Satellite',
  },
  terrain: {
    name: 'Google Terrain',
    IconComponent: IconMountain,
    url: 'https://{s}.google.com/vt/lyrs=p&x={x}&y={y}&z={z}',
    subdomains: ['mt0', 'mt1', 'mt2', 'mt3'],
    attribution: '&copy; Google Maps Terrain',
  },
  roadmap: {
    name: 'Google Streets',
    IconComponent: IconMap,
    url: 'https://{s}.google.com/vt/lyrs=m&x={x}&y={y}&z={z}',
    subdomains: ['mt0', 'mt1', 'mt2', 'mt3'],
    attribution: '&copy; Google Maps',
  },
  dark: {
    name: 'Dark GIS',
    IconComponent: IconMoon,
    url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
    subdomains: ['a', 'b', 'c', 'd'],
    attribution: '&copy; OpenStreetMap &copy; CARTO',
  },
};

const provincialStyle = {
  color: '#38bdf8',
  weight: 3.5,
  opacity: 1,
  fillColor: '#38bdf8',
  fillOpacity: 0.12,
  dashArray: '8 5',
};

const districtsStyle = {
  color: '#5eead4',
  weight: 2.2,
  opacity: 0.95,
  fillColor: '#5eead4',
  fillOpacity: 0.16,
};

// ==================== GIS TELEMETRY & CLICK HANDLER ====================
const MapInteractionEvents = ({ onCursorMove, spatialMode, onSpatialClick }) => {
  useMapEvents({
    mousemove(e) {
      onCursorMove({ lat: e.latlng.lat, lon: e.latlng.lng });
    },
    click(e) {
      if (spatialMode) {
        onSpatialClick({ lat: e.latlng.lat, lon: e.latlng.lng });
      }
    },
  });
  return null;
};

// ==================== MAIN COMPONENT ====================
const MapView = ({
  selectedRisk,
  setSelectedRisk,
  lakes,
  setSelectedLake,
  loading,
  showZones = false,
  defaultShowDanger = true,
  defaultShowWarning = true,
}) => {
  const [activeLayer, setActiveLayer] = useState('hybrid');
  const [showLayersPanel, setShowLayersPanel] = useState(true);

  const [layers, setLayers] = useState({
    lakes: true,
    provincial: true,
    districts: true,
    danger: defaultShowDanger,
    warning: defaultShowWarning,
    corridor: true,
  });

  const [provincialData, setProvincialData] = useState(null);
  const [districtsData, setDistrictsData] = useState(null);
  const [loadError, setLoadError] = useState(null);

  // Advanced GIS States: Cursor Telemetry, Flood Corridor Trace, Spatial Proximity Search
  const [cursorCoords, setCursorCoords] = useState({ lat: 36.2, lon: 74.5 });
  const [corridorData, setCorridorData] = useState(null);
  const [corridorLoadingId, setCorridorLoadingId] = useState(null);
  const [spatialMode, setSpatialMode] = useState(false);
  const [spatialResult, setSpatialResult] = useState(null);
  const [spatialRadiusKm, setSpatialRadiusKm] = useState(35);

  useEffect(() => {
    fetch('/geo/glof_provincial_boundary.geojson')
      .then((res) => {
        if (!res.ok) throw new Error(`Provincial file not found (${res.status})`);
        return res.json();
      })
      .then((data) => setProvincialData(data))
      .catch((err) => setLoadError(err.message));

    fetch('/geo/glof_districts.geojson')
      .then((res) => {
        if (!res.ok) throw new Error(`Districts file not found (${res.status})`);
        return res.json();
      })
      .then((data) => setDistrictsData(data))
      .catch((err) => setLoadError(err.message));
  }, []);

  const toggleLayer = (key) => {
    setLayers((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const traceFloodCorridor = async (lakeId) => {
    if (!lakeId) return;
    setCorridorLoadingId(lakeId);
    try {
      const res = await fetch(`${API_BASE}/lakes/${lakeId}/flood-corridor`);
      if (!res.ok) throw new Error(`Corridor trace failed (${res.status})`);
      const data = await res.json();
      setCorridorData(data);
      setLayers((prev) => ({ ...prev, corridor: true }));
    } catch (err) {
      console.error(err);
    } finally {
      setCorridorLoadingId(null);
    }
  };

  const runSpatialSearch = async ({ lat, lon }) => {
    try {
      const url = `${API_BASE}/lakes/spatial-search?lat=${lat}&lon=${lon}&radius_km=${spatialRadiusKm}&risk_level=${selectedRisk}`;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`Spatial query failed (${res.status})`);
      const data = await res.json();
      setSpatialResult({ center: { lat, lon }, radiusKm: spatialRadiusKm, ...data });
    } catch (err) {
      console.error(err);
    }
  };

  const exportGeoJSON = async () => {
    try {
      const res = await fetch(`${API_BASE}/lakes/geojson?risk_level=${selectedRisk}`);
      if (!res.ok) throw new Error('GeoJSON export failed');
      const geojson = await res.json();
      const blob = new Blob([JSON.stringify(geojson, null, 2)], { type: 'application/geo+json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `glof_lakes_${(selectedRisk || 'all').toLowerCase()}.geojson`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error(err);
    }
  };

  const onEachDistrict = (feature, layer) => {
    const name = feature.properties?.adm2_name || feature.properties?.name || 'Unknown District';
    const province = feature.properties?.adm1_name || '';
    layer.bindPopup(`
      <div style="min-width:160px;font-family:var(--font-body, system-ui)">
        <strong style="font-size:14px">${name}</strong><br/>
        <span style="color:#5b7690;font-size:12px">${province}</span>
      </div>
    `);
  };

  const filteredLakes =
    selectedRisk === 'All'
      ? lakes
      : lakes.filter((lake) => getRisk(lake.area_ha, lake.risk_level) === selectedRisk);

  // Extract parsed LineStrings & storage nodes from active flood corridor GeoJSON
  const corridorFeatures = corridorData?.features || [];
  const downstreamLine = corridorFeatures.find((f) => f.properties?.layer === 'downstream_corridor');
  const glacierLine = corridorFeatures.find((f) => f.properties?.layer === 'glacier_feeder');
  const storageNodes = corridorFeatures.filter((f) => f.properties?.layer === 'storage_node');

  if (loading) {
    return (
      <div style={{
        display: 'flex', flexDirection: 'column', justifyContent: 'center',
        alignItems: 'center', height: '580px', color: '#8ea3ba', fontSize: '15px', gap: '16px'
      }}>
        <div style={{
          width: '44px', height: '44px', borderRadius: '50%',
          border: '3px solid rgba(94,234,212,0.15)', borderTopColor: '#5eead4',
          animation: 'spin-ring 1s linear infinite'
        }}></div>
        <p className="mono-label" style={{ fontWeight: 600, fontSize: 11.5 }}>
          Loading Geospatial Intelligence Map...
        </p>
      </div>
    );
  }

  return (
    <div style={{ width: '100%' }}>
      {/* Top Bar */}
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        marginBottom: '20px', flexWrap: 'wrap', gap: '14px'
      }}>
        {/* Risk Filter */}
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          {['All', 'High', 'Medium', 'Low'].map((risk) => {
            const isActive = selectedRisk === risk;
            let activeColor = '#38bdf8';
            let activeBg = 'rgba(56,189,248,0.1)';
            let border = 'rgba(56,189,248,0.3)';

            if (risk === 'High') {
              activeColor = '#f0433a';
              activeBg = 'rgba(240,67,58,0.1)';
              border = 'rgba(240,67,58,0.3)';
            } else if (risk === 'Medium') {
              activeColor = '#f5a524';
              activeBg = 'rgba(245,165,36,0.1)';
              border = 'rgba(245,165,36,0.3)';
            } else if (risk === 'Low') {
              activeColor = '#2dd48e';
              activeBg = 'rgba(45,212,142,0.1)';
              border = 'rgba(45,212,142,0.3)';
            }

            return (
              <button
                key={risk}
                onClick={() => setSelectedRisk(risk)}
                className="btn-3d"
                style={{
                  display: 'inline-flex', alignItems: 'center', gap: '8px',
                  padding: '8px 16px', borderRadius: '10px',
                  border: isActive ? `1px solid ${border}` : '1px solid var(--line, #dbe4ec)',
                  cursor: 'pointer', fontSize: '13px', fontWeight: 700,
                  fontFamily: 'var(--font-display, inherit)',
                  background: isActive ? activeBg : 'transparent',
                  color: isActive ? activeColor : '#8ea3ba',
                }}
              >
                {risk === 'All' && <IconGlobe size={15} color={isActive ? activeColor : '#8ea3ba'} />}
                {risk === 'All' ? 'All Monitored Lakes' : `${risk} Risk`}
              </button>
            );
          })}
        </div>

        {/* Right controls: Spatial Radius Query, GeoJSON Export, Layers, Basemaps */}
        <div style={{ display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' }}>
          <button
            onClick={() => {
              setSpatialMode((prev) => !prev);
              if (spatialMode) setSpatialResult(null);
            }}
            className="btn-3d"
            title="Click anywhere on the map to find lakes within 35 km radius"
            style={{
              display: 'flex', alignItems: 'center', gap: '7px',
              padding: '8px 14px', borderRadius: '10px',
              border: spatialMode ? '1px solid rgba(236,72,153,0.5)' : '1px solid var(--line, #dbe4ec)',
              background: spatialMode ? 'rgba(236,72,153,0.14)' : 'transparent',
              color: spatialMode ? '#f472b6' : '#8ea3ba',
              fontWeight: 700, fontSize: '12.5px', cursor: 'pointer',
              fontFamily: 'var(--font-display, inherit)',
            }}
          >
            <span>🎯</span>
            {spatialMode ? `Radius Query (${spatialRadiusKm} km ON)` : 'Spatial Query'}
          </button>

          <button
            onClick={exportGeoJSON}
            className="btn-3d"
            title="Download RFC 7946 GeoJSON for QGIS / PostGIS / BigQuery GIS"
            style={{
              display: 'flex', alignItems: 'center', gap: '7px',
              padding: '8px 14px', borderRadius: '10px',
              border: '1px solid var(--line, #dbe4ec)',
              background: 'transparent',
              color: '#8ea3ba',
              fontWeight: 700, fontSize: '12.5px', cursor: 'pointer',
              fontFamily: 'var(--font-display, inherit)',
            }}
          >
            <IconDownload size={14} color="#5eead4" />
            GeoJSON
          </button>

          <button
            onClick={() => setShowLayersPanel(!showLayersPanel)}
            className="btn-3d"
            style={{
              display: 'flex', alignItems: 'center', gap: '8px',
              padding: '8px 16px', borderRadius: '10px',
              border: showLayersPanel ? '1px solid rgba(94,234,212,0.35)' : '1px solid var(--line, #dbe4ec)',
              background: showLayersPanel ? 'rgba(94,234,212,0.1)' : 'transparent',
              color: showLayersPanel ? '#5eead4' : '#8ea3ba',
              fontWeight: 700, fontSize: '13px', cursor: 'pointer',
              fontFamily: 'var(--font-display, inherit)',
            }}
          >
            <span style={{ fontSize: 16 }}>🗂️</span>
            Layers
          </button>

          <div style={{
            display: 'flex', background: 'rgba(94,234,212,0.06)', padding: '4px',
            borderRadius: '12px', border: '1px solid var(--line, #dbe4ec)'
          }}>
            {Object.keys(MAP_LAYERS).map((key) => {
              const isSelected = activeLayer === key;
              const Icon = MAP_LAYERS[key].IconComponent;
              return (
                <button
                  key={key}
                  onClick={() => setActiveLayer(key)}
                  style={{
                    display: 'inline-flex', alignItems: 'center', gap: '6px',
                    padding: '6px 12px', borderRadius: '8px', border: 'none',
                    background: isSelected ? 'linear-gradient(135deg,#5eead4,#38bdf8)' : 'transparent',
                    color: isSelected ? '#06131a' : '#8ea3ba',
                    fontSize: '12px', fontWeight: isSelected ? 700 : 600,
                    cursor: 'pointer',
                  }}
                >
                  <Icon size={14} color={isSelected ? '#06131a' : '#8ea3ba'} />
                  {MAP_LAYERS[key].name}
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {spatialMode && (
        <div style={{
          background: 'rgba(236,72,153,0.1)', border: '1px solid rgba(236,72,153,0.35)',
          color: '#f472b6', padding: '10px 16px', borderRadius: 10, marginBottom: 14,
          fontSize: 13, fontWeight: 600, display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 10
        }}>
          <span>
            🎯 <strong>Spatial Proximity Mode Active:</strong> Click any point on the map to query all glacial lakes within <strong>{spatialRadiusKm} km</strong>.
          </span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 12 }}>Radius:</span>
            {[15, 35, 60, 100].map((r) => (
              <button
                key={r}
                onClick={() => setSpatialRadiusKm(r)}
                style={{
                  padding: '3px 9px', borderRadius: 6, border: '1px solid rgba(236,72,153,0.4)',
                  background: spatialRadiusKm === r ? '#ec4899' : 'transparent',
                  color: spatialRadiusKm === r ? '#fff' : '#f472b6',
                  fontSize: 11.5, fontWeight: 700, cursor: 'pointer'
                }}
              >
                {r} km
              </button>
            ))}
          </div>
        </div>
      )}

      {loadError && (
        <div style={{
          background: 'rgba(240,67,58,0.1)', border: '1px solid rgba(240,67,58,0.3)',
          color: '#f0433a', padding: '12px 16px', borderRadius: 10, marginBottom: 16,
          fontSize: 14, fontWeight: 600
        }}>
          ⚠️ GeoJSON Error: {loadError} — Make sure files are in <code>public/geo/</code>
        </div>
      )}

      {/* Map */}
      <div className="hud-frame" style={{
        height: '600px', width: '100%', borderRadius: '18px', overflow: 'hidden',
        border: '1px solid var(--line, #dbe4ec)', boxShadow: '0 10px 34px rgba(6,11,19,0.18)',
        position: 'relative'
      }}>

        {/* Live WGS84 Coordinate Telemetry HUD */}
        <div style={{
          position: 'absolute', bottom: 14, left: 14, zIndex: 1000,
          background: 'rgba(6,16,26,0.86)', backdropFilter: 'blur(10px)',
          borderRadius: 10, border: '1px solid rgba(94,234,212,0.22)',
          padding: '6px 12px', color: '#eaf4f8', fontSize: 11.5, fontFamily: 'monospace',
          pointerEvents: 'none', display: 'flex', gap: 12, alignItems: 'center'
        }}>
          <span style={{ color: '#5eead4', fontWeight: 700 }}>WGS84 (EPSG:4326)</span>
          <span>Lat: {cursorCoords.lat.toFixed(4)}° N</span>
          <span>Lon: {cursorCoords.lon.toFixed(4)}° E</span>
        </div>

        {/* Active Flood Corridor Telemetry Overlay */}
        {corridorData?.lake && layers.corridor && (
          <div style={{
            position: 'absolute', top: 16, left: 56, zIndex: 1000,
            background: 'rgba(6,16,26,0.92)', backdropFilter: 'blur(14px)',
            borderRadius: 14, border: '1px solid rgba(236,72,153,0.45)',
            boxShadow: '0 10px 36px rgba(0,0,0,0.45)',
            padding: '14px 16px', maxWidth: 340, color: '#eaf4f8'
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <span style={{ fontSize: 11, fontWeight: 800, color: '#f472b6', textTransform: 'uppercase', letterSpacing: 0.7 }}>
                🌊 GLOF Outburst Routing Trace
              </span>
              <button
                onClick={() => setCorridorData(null)}
                style={{ background: 'none', border: 'none', color: '#8ea3ba', cursor: 'pointer', fontSize: 16 }}
              >
                ×
              </button>
            </div>
            <div style={{ fontSize: 15, fontWeight: 800, marginBottom: 4 }}>
              {corridorData.lake.name}
            </div>
            <div style={{ fontSize: 12, color: '#8ea3ba', marginBottom: 10 }}>
              {corridorData.lake.basin_name} · {corridorData.lake.river}
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, fontSize: 12, marginBottom: 10 }}>
              <div style={{ background: 'rgba(255,255,255,0.04)', padding: '6px 8px', borderRadius: 8 }}>
                <div style={{ fontSize: 10, color: '#8ea3ba' }}>PEAK DISCHARGE (Qp)</div>
                <div style={{ fontWeight: 800, color: '#f472b6' }}>{corridorData.lake.peak_discharge_m3s?.toLocaleString()} m³/s</div>
              </div>
              <div style={{ background: 'rgba(255,255,255,0.04)', padding: '6px 8px', borderRadius: 8 }}>
                <div style={{ fontSize: 10, color: '#8ea3ba' }}>OUTBURST VOLUME</div>
                <div style={{ fontWeight: 800, color: '#38bdf8' }}>{corridorData.lake.estimated_volume_million_m3} M m³</div>
              </div>
              <div style={{ background: 'rgba(255,255,255,0.04)', padding: '6px 8px', borderRadius: 8 }}>
                <div style={{ fontSize: 10, color: '#8ea3ba' }}>GLACIER FEEDER</div>
                <div style={{ fontWeight: 800, color: '#5eead4' }}>{corridorData.lake.nearest_glacier_km} km</div>
              </div>
              <div style={{ background: 'rgba(255,255,255,0.04)', padding: '6px 8px', borderRadius: 8 }}>
                <div style={{ fontSize: 10, color: '#8ea3ba' }}>CORRIDOR LENGTH</div>
                <div style={{ fontWeight: 800, color: '#f5a524' }}>{corridorData.lake.total_corridor_km} km</div>
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <a
                href={`${API_BASE}/reports/sitrep/${corridorData.lake.id}`}
                target="_blank"
                rel="noreferrer"
                style={{
                  flex: 1, textAlign: 'center', padding: '6px 10px', borderRadius: 8,
                  background: 'linear-gradient(135deg, #ec4899, #38bdf8)', color: '#fff',
                  fontSize: 11.5, fontWeight: 800, textDecoration: 'none'
                }}
              >
                📄 Download SITREP PDF
              </a>
            </div>
          </div>
        )}

        {/* Spatial Proximity Query Results Overlay */}
        {spatialResult && (
          <div style={{
            position: 'absolute', bottom: 48, left: 14, zIndex: 1000,
            background: 'rgba(6,16,26,0.92)', backdropFilter: 'blur(14px)',
            borderRadius: 14, border: '1px solid rgba(236,72,153,0.45)',
            padding: '12px 14px', maxWidth: 320, maxHeight: 230, overflowY: 'auto', color: '#eaf4f8'
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
              <span style={{ fontSize: 11, fontWeight: 800, color: '#f472b6', textTransform: 'uppercase' }}>
                🎯 {spatialResult.count} Lakes Within {spatialResult.radiusKm} km
              </span>
              <button
                onClick={() => setSpatialResult(null)}
                style={{ background: 'none', border: 'none', color: '#8ea3ba', cursor: 'pointer', fontSize: 15 }}
              >
                ×
              </button>
            </div>
            {spatialResult.results?.length === 0 ? (
              <div style={{ fontSize: 12, color: '#8ea3ba' }}>No monitored lakes inside this radius.</div>
            ) : (
              spatialResult.results?.slice(0, 8).map((item) => (
                <div
                  key={item.id}
                  onClick={() => traceFloodCorridor(item.id)}
                  style={{
                    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                    padding: '5px 6px', borderBottom: '1px solid rgba(255,255,255,0.07)',
                    fontSize: 12, cursor: 'pointer'
                  }}
                >
                  <span style={{ fontWeight: 700 }}>{item.name}</span>
                  <span style={{ color: '#5eead4', fontFamily: 'monospace', fontSize: 11 }}>
                    {item.distance_km} km ({item.bearing_deg}°)
                  </span>
                </div>
              ))
            )}
          </div>
        )}

        {/* Layers Panel */}
        {showLayersPanel && (
          <div style={{
            position: 'absolute', top: 16, right: 16, zIndex: 1000,
            background: 'rgba(6,16,26,0.88)', backdropFilter: 'blur(14px)',
            borderRadius: 14, border: '1px solid rgba(94,234,212,0.2)',
            boxShadow: '0 10px 40px rgba(0,0,0,0.35)',
            padding: '16px 18px', minWidth: 230
          }}>
            <div className="mono-label" style={{
              fontSize: 11, fontWeight: 800, color: '#eaf4f8', marginBottom: 14,
              display: 'flex', justifyContent: 'space-between', alignItems: 'center'
            }}>
              <span>Map Layers</span>
              <button
                onClick={() => setShowLayersPanel(false)}
                style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 16, color: '#8ea3ba' }}
              >×</button>
            </div>

            {[
              { key: 'lakes', label: 'GLOF Lakes', color: '#38bdf8', icon: '💧' },
              { key: 'provincial', label: 'Provincial Boundary', color: '#38bdf8', icon: '🗺️' },
              { key: 'districts', label: 'GLOF Districts', color: '#5eead4', icon: '📍' },
              ...(corridorData ? [
                { key: 'corridor', label: 'Flood Routing Trace', color: '#ec4899', icon: '🌊' },
              ] : []),
              ...(showZones ? [
                { key: 'danger', label: 'Danger Zone', color: '#f0433a', icon: '🔴' },
                { key: 'warning', label: 'Warning Zone', color: '#f5a524', icon: '🟠' },
              ] : []),
            ].map((item) => (
              <div
                key={item.key}
                onClick={() => toggleLayer(item.key)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 12,
                  padding: '10px 12px', borderRadius: 10, cursor: 'pointer',
                  background: layers[item.key] ? 'rgba(94,234,212,0.08)' : 'transparent',
                  marginBottom: 6, transition: 'all 0.15s ease'
                }}
              >
                <div style={{
                  width: 18, height: 18, borderRadius: 5,
                  border: `2px solid ${item.color}`,
                  background: layers[item.key] ? item.color : 'transparent',
                  display: 'flex', alignItems: 'center', justifyContent: 'center'
                }}>
                  {layers[item.key] && (
                    <span style={{ color: '#06131a', fontSize: 11, fontWeight: 800 }}>✓</span>
                  )}
                </div>
                <span style={{ fontSize: 15 }}>{item.icon}</span>
                <span style={{ fontSize: 13.5, fontWeight: 600, color: '#eaf4f8' }}>{item.label}</span>
              </div>
            ))}
          </div>
        )}

        <MapContainer center={[36.2, 74.5]} zoom={7} style={{ height: '100%', width: '100%' }}>
          <MapInteractionEvents
            onCursorMove={setCursorCoords}
            spatialMode={spatialMode}
            onSpatialClick={runSpatialSearch}
          />

          <TileLayer
            key={activeLayer}
            attribution={MAP_LAYERS[activeLayer].attribution}
            url={MAP_LAYERS[activeLayer].url}
            subdomains={MAP_LAYERS[activeLayer].subdomains || ['a', 'b', 'c']}
            maxZoom={20}
          />

          {layers.provincial && provincialData && (
            <GeoJSON key="provincial" data={provincialData} style={provincialStyle} />
          )}

          {layers.districts && districtsData && (
            <GeoJSON
              key="districts"
              data={districtsData}
              style={districtsStyle}
              onEachFeature={onEachDistrict}
            />
          )}

          {/* Spatial Query Search Circle */}
          {spatialResult?.center && (
            <Circle
              center={[spatialResult.center.lat, spatialResult.center.lon]}
              radius={spatialResult.radiusKm * 1000}
              pathOptions={{
                color: '#ec4899',
                fillColor: '#ec4899',
                fillOpacity: 0.1,
                weight: 2,
                dashArray: '6 4',
              }}
            />
          )}

          {/* Active Flood Routing Corridor & Glacier Feeder LineStrings */}
          {corridorData && layers.corridor && glacierLine && (
            <Polyline
              positions={glacierLine.geometry.coordinates.map(([lon, lat]) => [lat, lon])}
              pathOptions={{ color: '#38bdf8', weight: 3, dashArray: '5 5' }}
            />
          )}

          {corridorData && layers.corridor && downstreamLine && (
            <Polyline
              positions={downstreamLine.geometry.coordinates.map(([lon, lat]) => [lat, lon])}
              pathOptions={{ color: '#ec4899', weight: 4, opacity: 0.95 }}
            />
          )}

          {corridorData && layers.corridor && storageNodes.map((node, idx) => {
            const [lon, lat] = node.geometry.coordinates;
            const props = node.properties || {};
            return (
              <CircleMarker
                key={`storage-node-${idx}`}
                center={[lat, lon]}
                radius={7}
                pathOptions={{
                  color: '#ffffff',
                  fillColor: '#ec4899',
                  fillOpacity: 1,
                  weight: 2,
                }}
              >
                <Popup>
                  <div style={{ minWidth: 190 }}>
                    <div style={{ fontSize: 11, fontWeight: 800, color: '#ec4899', textTransform: 'uppercase' }}>
                      Downstream Node #{props.sequence} ({props.node_type})
                    </div>
                    <div style={{ fontSize: 14, fontWeight: 800, color: '#0c1826', margin: '4px 0' }}>
                      {props.name}
                    </div>
                    <div style={{ fontSize: 12, color: '#334155' }}>
                      Distance from Lake: <strong>{props.distance_from_lake_km} km</strong><br />
                      Est. Wave Arrival: <strong>~{props.estimated_arrival_min} min</strong>
                    </div>
                  </div>
                </Popup>
              </CircleMarker>
            );
          })}

          {/* WARNING ZONES (draw first so danger appears on top) */}
          {showZones && layers.warning && filteredLakes.map((lake) => {
            if (!lake.latitude || !lake.longitude) return null;
            const risk = getRisk(lake.area_ha, lake.risk_level);
            const { warning } = getZoneRadii(risk);
            return (
              <Circle
                key={`warn-${lake.id}`}
                center={[lake.latitude, lake.longitude]}
                radius={warning}
                pathOptions={{
                  color: '#f5a524',
                  fillColor: '#f5a524',
                  fillOpacity: 0.08,
                  weight: 1.5,
                  dashArray: '6 4',
                }}
              />
            );
          })}

          {/* DANGER ZONES */}
          {showZones && layers.danger && filteredLakes.map((lake) => {
            if (!lake.latitude || !lake.longitude) return null;
            const risk = getRisk(lake.area_ha, lake.risk_level);
            const { danger } = getZoneRadii(risk);
            return (
              <Circle
                key={`danger-${lake.id}`}
                center={[lake.latitude, lake.longitude]}
                radius={danger}
                pathOptions={{
                  color: '#f0433a',
                  fillColor: '#f0433a',
                  fillOpacity: 0.15,
                  weight: 2,
                }}
              />
            );
          })}

          {/* LAKE MARKERS */}
          {layers.lakes && filteredLakes.map((lake) => {
            if (!lake.latitude || !lake.longitude) return null;

            const risk = getRisk(lake.area_ha, lake.risk_level);
            const riskColor =
              risk === 'High' ? '#f0433a' :
              risk === 'Medium' ? '#f5a524' : '#2dd48e';

            return (
              <Marker
                key={lake.id}
                position={[lake.latitude, lake.longitude]}
                icon={get3DMarkerIcon(risk)}
              >
                <Popup>
                  <div style={{ minWidth: '240px', padding: '4px' }}>
                    <div style={{ marginBottom: '8px' }}>
                      <h4 style={{ margin: 0, fontSize: '16px', fontWeight: 800, color: '#0c1826' }}>
                        {lake.name}
                      </h4>
                    </div>

                    <div style={{ marginBottom: '12px' }}>
                      <span style={{
                        backgroundColor: `${riskColor}15`, color: riskColor,
                        border: `1px solid ${riskColor}35`, padding: '3px 10px',
                        borderRadius: '16px', fontSize: '11.5px', fontWeight: 700
                      }}>
                        {risk} Risk Level
                      </span>
                    </div>

                    <div style={{
                      display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px',
                      marginBottom: '12px', background: '#f6f9fb', padding: '10px',
                      borderRadius: '10px', border: '1px solid #dde5ec'
                    }}>
                      <div>
                        <div style={{ fontSize: '10px', color: '#5b7690', fontWeight: 700, textTransform: 'uppercase' }}>Area</div>
                        <div style={{ fontSize: '13px', fontWeight: 700, color: '#0c1826' }}>{lake.area_ha} ha</div>
                      </div>
                      <div>
                        <div style={{ fontSize: '10px', color: '#5b7690', fontWeight: 700, textTransform: 'uppercase' }}>Coordinates</div>
                        <div style={{ fontSize: '11px', fontWeight: 600, color: '#334155' }}>
                          {lake.latitude?.toFixed(3)}, {lake.longitude?.toFixed(3)}
                        </div>
                      </div>
                    </div>

                    <div style={{ display: 'flex', flexDirection: 'column', gap: '7px' }}>
                      <button
                        onClick={() => setSelectedLake(lake)}
                        className="btn-3d"
                        style={{
                          width: '100%', padding: '8px 0', borderRadius: '8px', border: 'none',
                          background: 'linear-gradient(135deg, #5eead4, #38bdf8)',
                          color: '#06131a', fontSize: '12.5px', fontWeight: 800, cursor: 'pointer'
                        }}
                      >
                        View Risk Assessment →
                      </button>
                      <button
                        onClick={() => traceFloodCorridor(lake.id)}
                        className="btn-3d"
                        style={{
                          width: '100%', padding: '7px 0', borderRadius: '8px',
                          border: '1px solid rgba(236,72,153,0.4)',
                          background: 'rgba(236,72,153,0.1)',
                          color: '#be185d', fontSize: '12px', fontWeight: 800, cursor: 'pointer'
                        }}
                      >
                        {corridorLoadingId === lake.id ? 'Tracing Corridor...' : '🌊 Trace Flood Corridor'}
                      </button>
                    </div>
                  </div>
                </Popup>
              </Marker>
            );
          })}
        </MapContainer>
      </div>

      {/* Legend */}
      <div style={{ marginTop: '16px', display: 'flex', justifyContent: 'center', flexWrap: 'wrap', gap: 12 }}>
        <div style={{
          background: 'rgba(94,234,212,0.05)', padding: '10px 24px', borderRadius: '14px',
          border: '1px solid var(--line, #dbe4ec)', display: 'flex', alignItems: 'center',
          gap: '24px', fontSize: '13px', color: 'var(--text-hi, #0c1826)',
        }}>
          <span className="mono-label" style={{ color: '#8ea3ba', fontSize: '10.5px' }}>
            Risk Classification
          </span>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <div style={{ width: '12px', height: '12px', borderRadius: '50%', backgroundColor: '#f0433a', boxShadow: '0 0 6px rgba(240,67,58,0.5)', border: '2px solid rgba(255,255,255,0.8)' }}></div>
            <span style={{ fontWeight: 600 }}>High Risk</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <div style={{ width: '12px', height: '12px', borderRadius: '50%', backgroundColor: '#f5a524', boxShadow: '0 0 6px rgba(245,165,36,0.5)', border: '2px solid rgba(255,255,255,0.8)' }}></div>
            <span style={{ fontWeight: 600 }}>Medium Risk</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <div style={{ width: '12px', height: '12px', borderRadius: '50%', backgroundColor: '#2dd48e', boxShadow: '0 0 6px rgba(45,212,142,0.5)', border: '2px solid rgba(255,255,255,0.8)' }}></div>
            <span style={{ fontWeight: 600 }}>Low Risk</span>
          </div>
        </div>

        {showZones && (
          <div style={{
            background: 'rgba(240,67,58,0.06)', padding: '10px 20px', borderRadius: '14px',
            border: '1px solid rgba(240,67,58,0.2)', display: 'flex', alignItems: 'center',
            gap: '18px', fontSize: '13px',
          }}>
            <span className="mono-label" style={{ color: '#8ea3ba', fontSize: '10.5px' }}>Zones</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <div style={{ width: 14, height: 14, borderRadius: '50%', background: 'rgba(240,67,58,0.35)', border: '2px solid #f0433a' }}></div>
              <span style={{ fontWeight: 600, color: '#f0433a' }}>Danger</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <div style={{ width: 14, height: 14, borderRadius: '50%', background: 'rgba(245,165,36,0.25)', border: '2px dashed #f5a524' }}></div>
              <span style={{ fontWeight: 600, color: '#f5a524' }}>Warning</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default MapView;
