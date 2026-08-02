import { useState, useEffect } from 'react';
import { MapContainer, TileLayer, Marker, Popup, GeoJSON, Circle } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { IconSatellite, IconMoon, IconMap, IconGlobe } from './Icons';

// Fix default Leaflet marker icons
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon-2x.png',
  iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png',
});

// ==================== RISK CALCULATION ====================
const getRisk = (area) => {
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

// ==================== BASEMAPS ====================
const MAP_LAYERS = {
  light: {
    name: 'Clean Light',
    IconComponent: IconMap,
    url: 'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png',
    attribution: '&copy; OpenStreetMap &copy; CARTO',
  },
  satellite: {
    name: 'Satellite',
    IconComponent: IconSatellite,
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attribution: '&copy; Esri',
  },
  dark: {
    name: 'Dark',
    IconComponent: IconMoon,
    url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
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

// ==================== MAIN COMPONENT ====================
const MapView = ({
  selectedRisk,
  setSelectedRisk,
  lakes,
  setSelectedLake,
  loading,
  // New props for Population tab
  showZones = false,
  defaultShowDanger = true,
  defaultShowWarning = true,
}) => {
  const [activeLayer, setActiveLayer] = useState('dark');
  const [showLayersPanel, setShowLayersPanel] = useState(true);

  const [layers, setLayers] = useState({
    lakes: true,
    provincial: true,
    districts: true,
    danger: defaultShowDanger,
    warning: defaultShowWarning,
  });

  const [provincialData, setProvincialData] = useState(null);
  const [districtsData, setDistrictsData] = useState(null);
  const [loadError, setLoadError] = useState(null);

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
      : lakes.filter((lake) => getRisk(lake.area_ha) === selectedRisk);

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

        {/* Right controls */}
        <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
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
          <TileLayer
            key={activeLayer}
            attribution={MAP_LAYERS[activeLayer].attribution}
            url={MAP_LAYERS[activeLayer].url}
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

          {/* WARNING ZONES (draw first so danger appears on top) */}
          {showZones && layers.warning && filteredLakes.map((lake) => {
            if (!lake.latitude || !lake.longitude) return null;
            const risk = getRisk(lake.area_ha);
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
            const risk = getRisk(lake.area_ha);
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

            const risk = getRisk(lake.area_ha);
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
                      marginBottom: '14px', background: '#f6f9fb', padding: '10px',
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