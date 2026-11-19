import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  MapContainer,
  TileLayer,
  Marker,
  Popup,
  Tooltip,
  GeoJSON,
  Circle,
  Polyline,
  CircleMarker,
  useMap,
  useMapEvents,
} from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { IconSatellite, IconMoon, IconMap, IconGlobe, IconMountain, IconDownload } from './Icons';
import {
  fetchDistrictAnalytics,
  fetchFloodCorridor,
  fetchSpatialSearch,
  getLakesGeoJsonUrl,
  getSitrepPdfUrl,
  isAbortError,
} from '../api';
import BreachSimulatorPanel from './BreachSimulatorPanel';

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

const escapeHtml = (val) =>
  String(val ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

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

// ==================== WAVEFRONT INTERPOLATION ====================
function computeWavefrontState(downstreamLine, storageNodes, breachData, simTimeMin) {
  const rawCoords = downstreamLine?.geometry?.coordinates || [];
  const routeLatLngs = rawCoords.map(([lon, lat]) => [lat, lon]);
  if (routeLatLngs.length === 0) {
    return { position: null, trail: [], distanceKm: 0, phase: 'idle' };
  }

  const simNodes = breachData?.downstream_nodes || [];
  const breachDuration = Number(breachData?.params?.breach_duration_min ?? 45);
  const hasSimNodes = simNodes.length > 0;
  const tDepart = hasSimNodes ? 0.25 * breachDuration : 0;

  const times = [tDepart];
  const dists = [0];

  for (let k = 1; k < routeLatLngs.length; k += 1) {
    const simNode = simNodes[k - 1];
    const geoNode = storageNodes?.[k - 1]?.properties || {};
    const prevD = dists[dists.length - 1];
    const distKm = Number(simNode?.distance_km ?? geoNode.distance_from_lake_km ?? prevD + 15);
    const fallbackEta = Number(geoNode.estimated_arrival_min ?? (distKm / 16.2) * 60);
    const rawEta = Number(simNode?.eta_min ?? fallbackEta);
    const prevT = times[times.length - 1];
    times.push(Math.max(prevT + 0.01, rawEta));
    dists.push(distKm);
  }

  if (simTimeMin <= times[0] || routeLatLngs.length === 1) {
    return {
      position: routeLatLngs[0],
      trail: [routeLatLngs[0]],
      distanceKm: 0,
      phase: simTimeMin > 0 ? 'breach_forming' : 'ready',
    };
  }

  const lastIdx = routeLatLngs.length - 1;
  if (simTimeMin >= times[lastIdx]) {
    return {
      position: routeLatLngs[lastIdx],
      trail: routeLatLngs,
      distanceKm: dists[lastIdx],
      phase: 'recession',
    };
  }

  for (let i = 0; i < lastIdx; i += 1) {
    if (simTimeMin >= times[i] && simTimeMin <= times[i + 1]) {
      const span = times[i + 1] - times[i];
      const alpha = span > 0 ? (simTimeMin - times[i]) / span : 0;
      const p0 = routeLatLngs[i];
      const p1 = routeLatLngs[i + 1];
      const position = [
        p0[0] + alpha * (p1[0] - p0[0]),
        p0[1] + alpha * (p1[1] - p0[1]),
      ];
      return {
        position,
        trail: [...routeLatLngs.slice(0, i + 1), position],
        distanceKm: dists[i] + alpha * (dists[i + 1] - dists[i]),
        phase: 'propagating',
      };
    }
  }

  return { position: routeLatLngs[0], trail: [routeLatLngs[0]], distanceKm: 0, phase: 'ready' };
}

// ==================== LEAFLET SUB-CONTROLLERS ====================
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

const ScaleControl = () => {
  const map = useMap();
  useEffect(() => {
    const ctrl = L.control.scale({ imperial: false, metric: true, position: 'bottomright' });
    ctrl.addTo(map);
    return () => {
      ctrl.remove();
    };
  }, [map]);
  return null;
};

const MapFlyToController = ({ flyTarget }) => {
  const map = useMap();
  const lastSeqRef = useRef(0);
  useEffect(() => {
    if (!flyTarget || !flyTarget.seq || flyTarget.seq === lastSeqRef.current) return;
    lastSeqRef.current = flyTarget.seq;
    map.flyTo([flyTarget.lat, flyTarget.lon], flyTarget.zoom ?? 11, { duration: 1.0 });
  }, [flyTarget, map]);
  return null;
};

// ==================== MAIN COMPONENT ====================
const MapView = ({
  selectedRisk,
  setSelectedRisk,
  lakes = [],
  setSelectedLake,
  loading,
  showZones = false,
  defaultShowDanger,
  defaultShowWarning,
  mapHeight = '600px',
}) => {
  const [activeLayer, setActiveLayer] = useState('hybrid');
  const [showLayersPanel, setShowLayersPanel] = useState(true);

  const [layers, setLayers] = useState({
    lakes: true,
    provincial: true,
    districts: true,
    choropleth: false,
    danger: defaultShowDanger ?? showZones,
    warning: defaultShowWarning ?? showZones,
    corridor: true,
  });

  const [provincialData, setProvincialData] = useState(null);
  const [districtsData, setDistrictsData] = useState(null);
  const [districtAnalytics, setDistrictAnalytics] = useState(null);
  const [districtLoading, setDistrictLoading] = useState(false);
  const [loadError, setLoadError] = useState(null);
  const [actionError, setActionError] = useState(null);

  // Quick-Jump Search States
  const [jumpQuery, setJumpQuery] = useState('');
  const [jumpOpen, setJumpOpen] = useState(false);
  const [jumpIndex, setJumpIndex] = useState(0);
  const [flyTarget, setFlyTarget] = useState(null);
  const jumpBoxRef = useRef(null);

  // Advanced GIS States: Cursor Telemetry, Flood Corridor Trace, Spatial Proximity Search, Breach Sim
  const [cursorCoords, setCursorCoords] = useState({ lat: 36.2, lon: 74.5 });
  const [corridorData, setCorridorData] = useState(null);
  const [corridorLoadingId, setCorridorLoadingId] = useState(null);
  const [spatialMode, setSpatialMode] = useState(false);
  const [spatialResult, setSpatialResult] = useState(null);
  const [spatialRadiusKm, setSpatialRadiusKm] = useState(35);

  const [showBreachSim, setShowBreachSim] = useState(false);
  const [breachData, setBreachData] = useState(null);
  const [simTimeMin, setSimTimeMin] = useState(0);
  const [simPlaying, setSimPlaying] = useState(false);

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

  // Fetch district analytics when Choropleth layer is enabled
  useEffect(() => {
    if (!layers.choropleth || districtAnalytics) return undefined;
    const controller = new AbortController();
    setDistrictLoading(true);
    fetchDistrictAnalytics({ signal: controller.signal })
      .then((data) => {
        setDistrictAnalytics(data);
        setActionError(null);
      })
      .catch((err) => {
        if (isAbortError(err)) return;
        setActionError(err?.detail || err?.message || 'Failed to load district choropleth analytics');
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setDistrictLoading(false);
        }
      });
    return () => controller.abort();
  }, [layers.choropleth, districtAnalytics]);

  // Close Quick-Jump dropdown when clicking outside
  useEffect(() => {
    const handleOutside = (e) => {
      if (jumpBoxRef.current && !jumpBoxRef.current.contains(e.target)) {
        setJumpOpen(false);
      }
    };
    document.addEventListener('mousedown', handleOutside);
    return () => document.removeEventListener('mousedown', handleOutside);
  }, []);

  const toggleLayer = (key) => {
    setLayers((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const quickJumpMatches = useMemo(() => {
    const valid = (lakes || []).filter((l) => l.latitude != null && l.longitude != null);
    const q = jumpQuery.trim().toLowerCase();
    if (!q) return valid.slice(0, 8);
    return valid.filter((l) => (l.name || '').toLowerCase().includes(q)).slice(0, 8);
  }, [lakes, jumpQuery]);

  const handleJumpSelect = useCallback((lake) => {
    if (!lake || lake.latitude == null || lake.longitude == null) return;
    setJumpQuery(lake.name || '');
    setJumpOpen(false);
    setFlyTarget({
      lat: Number(lake.latitude),
      lon: Number(lake.longitude),
      zoom: 11,
      seq: Date.now(),
    });
  }, []);

  const traceFloodCorridor = async (lakeId) => {
    if (!lakeId) return;
    setCorridorLoadingId(lakeId);
    setActionError(null);
    try {
      const data = await fetchFloodCorridor(lakeId);
      setCorridorData(data);
      setLayers((prev) => ({ ...prev, corridor: true }));
      setShowBreachSim(false);
      setBreachData(null);
      setSimTimeMin(0);
      setSimPlaying(false);
    } catch (err) {
      if (!isAbortError(err)) {
        setActionError(err?.detail || err?.message || `Corridor trace failed for lake #${lakeId}`);
      }
    } finally {
      setCorridorLoadingId(null);
    }
  };

  const runSpatialSearch = async ({ lat, lon }) => {
    setActionError(null);
    try {
      const data = await fetchSpatialSearch({
        lat,
        lon,
        radius_km: spatialRadiusKm,
        risk_level: selectedRisk,
      });
      setSpatialResult({ center: { lat, lon }, radiusKm: spatialRadiusKm, ...data });
    } catch (err) {
      if (!isAbortError(err)) {
        setActionError(err?.detail || err?.message || 'Spatial proximity query failed');
      }
    }
  };

  const exportGeoJSON = async () => {
    setActionError(null);
    try {
      const res = await fetch(getLakesGeoJsonUrl({ risk_level: selectedRisk }));
      if (!res.ok) throw new Error(`GeoJSON export failed (${res.status})`);
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
      setActionError(err?.message || 'GeoJSON export failed');
    }
  };

  const exportSpatialResultsGeoJSON = () => {
    if (!spatialResult?.results) return;
    const fc = {
      type: 'FeatureCollection',
      metadata: {
        query: spatialResult.query,
        radius_km: spatialResult.radiusKm,
        count: spatialResult.count,
      },
      features: spatialResult.results.map((item) => ({
        type: 'Feature',
        geometry: {
          type: 'Point',
          coordinates: [item.longitude, item.latitude],
        },
        properties: {
          id: item.id,
          name: item.name,
          area_ha: item.area_ha,
          risk_level: item.risk_level,
          distance_km: item.distance_km,
          bearing_deg: item.bearing_deg,
          basin_name: item.basin_name,
          estimated_volume_m3: item.estimated_volume_m3,
        },
      })),
    };
    const blob = new Blob([JSON.stringify(fc, null, 2)], { type: 'application/geo+json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `glof_spatial_query_${spatialResult.radiusKm}km.geojson`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  const exportSpatialResultsCSV = () => {
    if (!spatialResult?.results) return;
    const headers = [
      'id',
      'name',
      'risk_level',
      'area_ha',
      'distance_km',
      'bearing_deg',
      'basin_name',
      'estimated_volume_m3',
      'latitude',
      'longitude',
    ];
    const escapeCsv = (v) => {
      const s = String(v ?? '');
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const rows = spatialResult.results.map((item) =>
      headers.map((h) => escapeCsv(item[h])).join(','),
    );
    const csv = [headers.join(','), ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `glof_spatial_query_${spatialResult.radiusKm}km.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  // District Choropleth Lookup & Styling
  const districtLookup = useMemo(() => {
    const byPcode = new Map();
    const byName = new Map();
    for (const d of districtAnalytics?.districts || []) {
      if (d.pcode) byPcode.set(String(d.pcode).trim(), d);
      if (d.name) byName.set(String(d.name).trim().toLowerCase(), d);
    }
    return { byPcode, byName };
  }, [districtAnalytics]);

  const resolveDistrictStats = useCallback(
    (feature) => {
      const props = feature?.properties || {};
      const pcode = String(props.adm2_pcode || '').trim();
      const nameLower = String(props.adm2_name || props.name || '').trim().toLowerCase();
      return districtLookup.byPcode.get(pcode) || districtLookup.byName.get(nameLower) || null;
    },
    [districtLookup],
  );

  const getDistrictStyle = useCallback(
    (feature) => {
      if (!layers.choropleth) return districtsStyle;
      const stats = resolveDistrictStats(feature);
      if (!stats || Number(stats.lake_count || 0) === 0) {
        return {
          color: '#64748b',
          weight: 1.8,
          opacity: 0.9,
          fillColor: '#334155',
          fillOpacity: 0.16,
        };
      }
      const density = Number(stats.hazard_density_per_1000sqkm || 0);
      const count = Number(stats.lake_count || 0);
      if (density >= 3.0 || count >= 10) {
        return {
          color: '#fca5a5',
          weight: 2.2,
          opacity: 0.95,
          fillColor: '#f0433a',
          fillOpacity: 0.44,
        };
      }
      if (density >= 1.5 || count >= 5) {
        return {
          color: '#fcd34d',
          weight: 2.1,
          opacity: 0.95,
          fillColor: '#f5a524',
          fillOpacity: 0.38,
        };
      }
      if (density >= 0.5 || count >= 2) {
        return {
          color: '#7dd3fc',
          weight: 2.0,
          opacity: 0.95,
          fillColor: '#38bdf8',
          fillOpacity: 0.32,
        };
      }
      return {
        color: '#6ee7b7',
        weight: 2.0,
        opacity: 0.95,
        fillColor: '#2dd48e',
        fillOpacity: 0.28,
      };
    },
    [layers.choropleth, resolveDistrictStats],
  );

  const onEachDistrict = useCallback(
    (feature, layer) => {
      const props = feature.properties || {};
      const name = escapeHtml(props.adm2_name || props.name || 'Unknown District');
      const province = escapeHtml(props.adm1_name || '');
      const pcode = escapeHtml(props.adm2_pcode || '');
      const stats = resolveDistrictStats(feature);

      if (stats) {
        const rc = stats.risk_counts || { High: 0, Medium: 0, Low: 0 };
        const volM = ((Number(stats.total_volume_m3) || 0) / 1e6).toFixed(2);
        layer.bindPopup(`
          <div style="min-width:210px;font-family:var(--font-body, system-ui)">
            <div style="font-size:10px;font-weight:700;color:#0284c7;text-transform:uppercase;letter-spacing:0.5px">${pcode} · ${province}</div>
            <strong style="font-size:14.5px;color:#0c1826">${name}</strong>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:8px;font-size:12px;color:#334155">
              <div>Lakes: <strong>${stats.lake_count}</strong></div>
              <div>Area: <strong>${stats.total_area_ha} ha</strong></div>
              <div>Density: <strong>${stats.hazard_density_per_1000sqkm}/1k km²</strong></div>
              <div>Vol: <strong>${volM} M m³</strong></div>
            </div>
            <div style="margin-top:7px;font-size:11.5px;color:#475569">
              Risk: <strong style="color:#dc2626">${rc.High || 0} H</strong> ·
              <strong style="color:#d97706">${rc.Medium || 0} M</strong> ·
              <strong style="color:#059669">${rc.Low || 0} L</strong>
            </div>
          </div>
        `);
      } else {
        layer.bindPopup(`
          <div style="min-width:160px;font-family:var(--font-body, system-ui)">
            <strong style="font-size:14px">${name}</strong><br/>
            <span style="color:#5b7690;font-size:12px">${province}${pcode ? ` (${pcode})` : ''}</span>
          </div>
        `);
      }
    },
    [resolveDistrictStats],
  );

  const filteredLakes = useMemo(
    () =>
      selectedRisk === 'All'
        ? lakes
        : lakes.filter((lake) => getRisk(lake.area_ha, lake.risk_level) === selectedRisk),
    [lakes, selectedRisk],
  );

  const spatialSummary = useMemo(() => {
    const items = spatialResult?.results || [];
    let totalAreaHa = 0;
    let totalVolM3 = 0;
    const riskCounts = { High: 0, Medium: 0, Low: 0 };
    for (const item of items) {
      totalAreaHa += Number(item.area_ha || 0);
      totalVolM3 += Number(item.estimated_volume_m3 || 0);
      const r = getRisk(item.area_ha, item.risk_level);
      riskCounts[r] = (riskCounts[r] || 0) + 1;
    }
    return {
      totalAreaHa: totalAreaHa.toFixed(1),
      totalVolumeMillionM3: (totalVolM3 / 1e6).toFixed(2),
      riskCounts,
    };
  }, [spatialResult]);

  // Extract parsed LineStrings & storage nodes from active flood corridor GeoJSON
  const corridorFeatures = corridorData?.features || [];
  const downstreamLine = corridorFeatures.find((f) => f.properties?.layer === 'downstream_corridor');
  const glacierLine = corridorFeatures.find((f) => f.properties?.layer === 'glacier_feeder');
  const storageNodes = corridorFeatures.filter((f) => f.properties?.layer === 'storage_node');

  const wavefrontState = useMemo(() => {
    if (!showBreachSim || !corridorData || !downstreamLine) {
      return { position: null, trail: [], distanceKm: 0, phase: 'idle' };
    }
    return computeWavefrontState(downstreamLine, storageNodes, breachData, simTimeMin);
  }, [showBreachSim, corridorData, downstreamLine, storageNodes, breachData, simTimeMin]);

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
        marginBottom: '16px', flexWrap: 'wrap', gap: '12px'
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
                type="button"
                aria-pressed={isActive}
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

        {/* Right controls: Quick-Jump Search, Spatial Radius Query, GeoJSON Export, Layers, Basemaps */}
        <div style={{ display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' }}>
          {/* Quick-Jump Lake Search */}
          <div ref={jumpBoxRef} style={{ position: 'relative', minWidth: 195 }}>
            <input
              type="search"
              value={jumpQuery}
              placeholder="Quick-jump lake..."
              aria-label="Quick-jump to lake"
              role="combobox"
              aria-expanded={jumpOpen}
              aria-controls="quick-jump-listbox"
              onFocus={() => setJumpOpen(true)}
              onChange={(e) => {
                setJumpQuery(e.target.value);
                setJumpOpen(true);
                setJumpIndex(0);
              }}
              onKeyDown={(e) => {
                if (e.key === 'Escape') {
                  setJumpOpen(false);
                } else if (e.key === 'ArrowDown') {
                  e.preventDefault();
                  setJumpOpen(true);
                  setJumpIndex((i) => Math.min(i + 1, Math.max(0, quickJumpMatches.length - 1)));
                } else if (e.key === 'ArrowUp') {
                  e.preventDefault();
                  setJumpIndex((i) => Math.max(0, i - 1));
                } else if (e.key === 'Enter' && jumpOpen && quickJumpMatches[jumpIndex]) {
                  e.preventDefault();
                  handleJumpSelect(quickJumpMatches[jumpIndex]);
                }
              }}
              className="input-3d"
              style={{
                width: '100%',
                padding: '8px 12px',
                borderRadius: 10,
                border: '1px solid var(--line, #dbe4ec)',
                background: 'rgba(12, 24, 38, 0.72)',
                color: '#eaf4f8',
                fontSize: 12.5,
              }}
            />
            {jumpOpen && quickJumpMatches.length > 0 && (
              <div
                id="quick-jump-listbox"
                role="listbox"
                aria-label="Matching glacial lakes"
                style={{
                  position: 'absolute',
                  top: 'calc(100% + 6px)',
                  left: 0,
                  right: 0,
                  zIndex: 1100,
                  background: 'rgba(6, 16, 26, 0.96)',
                  border: '1px solid rgba(94, 234, 212, 0.28)',
                  borderRadius: 10,
                  boxShadow: '0 14px 32px rgba(0,0,0,0.45)',
                  maxHeight: 240,
                  overflowY: 'auto',
                  padding: 4,
                }}
              >
                {quickJumpMatches.map((lake, idx) => {
                  const r = getRisk(lake.area_ha, lake.risk_level);
                  const rColor = r === 'High' ? '#f0433a' : r === 'Medium' ? '#f5a524' : '#2dd48e';
                  return (
                    <button
                      key={lake.id ?? `${lake.name}-${idx}`}
                      type="button"
                      role="option"
                      aria-selected={idx === jumpIndex}
                      onClick={() => handleJumpSelect(lake)}
                      style={{
                        width: '100%',
                        textAlign: 'left',
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        gap: 8,
                        padding: '7px 10px',
                        borderRadius: 7,
                        border: 'none',
                        background: idx === jumpIndex ? 'rgba(94,234,212,0.14)' : 'transparent',
                        color: '#eaf4f8',
                        fontSize: 12.5,
                        cursor: 'pointer',
                      }}
                    >
                      <span style={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {lake.name}
                      </span>
                      <span style={{ fontSize: 10.5, fontWeight: 700, color: rColor, flexShrink: 0 }}>
                        {r}
                      </span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          <button
            type="button"
            aria-pressed={spatialMode}
            onClick={() => {
              setSpatialMode((prev) => !prev);
              if (spatialMode) setSpatialResult(null);
            }}
            className="btn-3d"
            title="Click anywhere on the map to find lakes within radius"
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
            type="button"
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
            type="button"
            aria-expanded={showLayersPanel}
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
            borderRadius: '12px', border: '1px solid var(--line, #dbe4ec)', flexWrap: 'wrap'
          }}>
            {Object.keys(MAP_LAYERS).map((key) => {
              const isSelected = activeLayer === key;
              const Icon = MAP_LAYERS[key].IconComponent;
              return (
                <button
                  key={key}
                  type="button"
                  aria-pressed={isSelected}
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
                type="button"
                aria-pressed={spatialRadiusKm === r}
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

      {actionError && (
        <div
          role="alert"
          style={{
            background: 'rgba(240,67,58,0.12)', border: '1px solid rgba(240,67,58,0.38)',
            color: '#fca5a5', padding: '10px 14px', borderRadius: 10, marginBottom: 14,
            fontSize: 13, fontWeight: 600, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10
          }}
        >
          <span>{actionError}</span>
          <button
            type="button"
            onClick={() => setActionError(null)}
            aria-label="Dismiss map alert"
            style={{ background: 'none', border: 'none', color: '#fca5a5', cursor: 'pointer', fontSize: 16 }}
          >
            ×
          </button>
        </div>
      )}

      {/* Map */}
      <div className="hud-frame" style={{
        height: mapHeight, width: '100%', borderRadius: '18px', overflow: 'hidden',
        border: '1px solid var(--line, #dbe4ec)', boxShadow: '0 10px 34px rgba(6,11,19,0.18)',
        position: 'relative'
      }}>

        {/* Live WGS84 Coordinate Telemetry HUD */}
        <div style={{
          position: 'absolute', bottom: 14, left: 14, zIndex: 1000,
          background: 'rgba(6,16,26,0.86)', backdropFilter: 'blur(10px)',
          borderRadius: 10, border: '1px solid rgba(94,234,212,0.22)',
          padding: '6px 12px', color: '#eaf4f8', fontSize: 11.5, fontFamily: 'monospace',
          pointerEvents: 'none', display: 'flex', gap: 12, alignItems: 'center',
          maxWidth: 'calc(100% - 120px)', flexWrap: 'wrap'
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
            padding: '14px 16px', maxWidth: 'min(340px, calc(100% - 68px))', color: '#eaf4f8'
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <span style={{ fontSize: 11, fontWeight: 800, color: '#f472b6', textTransform: 'uppercase', letterSpacing: 0.7 }}>
                🌊 GLOF Outburst Routing Trace
              </span>
              <button
                type="button"
                aria-label="Close flood corridor overlay"
                onClick={() => {
                  setCorridorData(null);
                  setShowBreachSim(false);
                  setBreachData(null);
                  setSimPlaying(false);
                  setSimTimeMin(0);
                }}
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
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <a
                href={getSitrepPdfUrl(corridorData.lake.id)}
                target="_blank"
                rel="noreferrer"
                style={{
                  flex: 1, minWidth: 130, textAlign: 'center', padding: '7px 10px', borderRadius: 8,
                  background: 'linear-gradient(135deg, #ec4899, #38bdf8)', color: '#fff',
                  fontSize: 11.5, fontWeight: 800, textDecoration: 'none'
                }}
              >
                📄 Download SITREP PDF
              </a>
              <button
                type="button"
                aria-pressed={showBreachSim}
                onClick={() => setShowBreachSim((prev) => !prev)}
                style={{
                  flex: 1, minWidth: 120, padding: '7px 10px', borderRadius: 8,
                  border: '1px solid rgba(94, 234, 212, 0.45)',
                  background: showBreachSim ? 'rgba(94, 234, 212, 0.22)' : 'rgba(94, 234, 212, 0.1)',
                  color: '#5eead4', fontSize: 11.5, fontWeight: 800, cursor: 'pointer'
                }}
              >
                {showBreachSim ? 'Hide Breach Sim' : 'Simulate Breach'}
              </button>
            </div>
          </div>
        )}

        {/* Spatial Proximity Query Results Overlay */}
        {spatialResult && (
          <div style={{
            position: 'absolute', bottom: 48, left: 14, zIndex: 1000,
            background: 'rgba(6,16,26,0.92)', backdropFilter: 'blur(14px)',
            borderRadius: 14, border: '1px solid rgba(236,72,153,0.45)',
            padding: '12px 14px', maxWidth: 'min(320px, calc(100% - 28px))', maxHeight: 290, overflowY: 'auto', color: '#eaf4f8'
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <span style={{ fontSize: 11, fontWeight: 800, color: '#f472b6', textTransform: 'uppercase' }}>
                🎯 {spatialResult.count} Lakes Within {spatialResult.radiusKm} km
              </span>
              <button
                type="button"
                aria-label="Close spatial query results"
                onClick={() => setSpatialResult(null)}
                style={{ background: 'none', border: 'none', color: '#8ea3ba', cursor: 'pointer', fontSize: 15 }}
              >
                ×
              </button>
            </div>

            {/* Summary Telemetry */}
            <div style={{
              display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 6,
              marginBottom: 8, fontSize: 11, background: 'rgba(255,255,255,0.04)',
              padding: '7px 8px', borderRadius: 8
            }}>
              <div>Area: <strong style={{ color: '#5eead4' }}>{spatialSummary.totalAreaHa} ha</strong></div>
              <div>Vol: <strong style={{ color: '#38bdf8' }}>{spatialSummary.totalVolumeMillionM3} M m³</strong></div>
              <div style={{ gridColumn: '1 / -1', color: '#cbd5e1' }}>
                Risk: <strong style={{ color: '#f0433a' }}>{spatialSummary.riskCounts.High} H</strong> ·{' '}
                <strong style={{ color: '#f5a524' }}>{spatialSummary.riskCounts.Medium} M</strong> ·{' '}
                <strong style={{ color: '#2dd48e' }}>{spatialSummary.riskCounts.Low} L</strong>
              </div>
            </div>

            {spatialResult.results?.length > 0 && (
              <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
                <button
                  type="button"
                  onClick={exportSpatialResultsGeoJSON}
                  style={{
                    flex: 1, padding: '4px 8px', borderRadius: 6,
                    border: '1px solid rgba(94,234,212,0.35)', background: 'rgba(94,234,212,0.1)',
                    color: '#5eead4', fontSize: 11, fontWeight: 700, cursor: 'pointer'
                  }}
                >
                  Export GeoJSON
                </button>
                <button
                  type="button"
                  onClick={exportSpatialResultsCSV}
                  style={{
                    flex: 1, padding: '4px 8px', borderRadius: 6,
                    border: '1px solid rgba(56,189,248,0.35)', background: 'rgba(56,189,248,0.1)',
                    color: '#38bdf8', fontSize: 11, fontWeight: 700, cursor: 'pointer'
                  }}
                >
                  Export CSV
                </button>
              </div>
            )}

            {spatialResult.results?.length === 0 ? (
              <div style={{ fontSize: 12, color: '#8ea3ba' }}>No monitored lakes inside this radius.</div>
            ) : (
              spatialResult.results?.slice(0, 8).map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => traceFloodCorridor(item.id)}
                  style={{
                    width: '100%', background: 'transparent', border: 'none',
                    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                    padding: '6px 6px', borderBottom: '1px solid rgba(255,255,255,0.07)',
                    fontSize: 12, cursor: 'pointer', color: '#eaf4f8', textAlign: 'left'
                  }}
                >
                  <span style={{ fontWeight: 700 }}>{item.name}</span>
                  <span style={{ color: '#5eead4', fontFamily: 'monospace', fontSize: 11 }}>
                    {item.distance_km} km ({item.bearing_deg}°)
                  </span>
                </button>
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
            padding: '16px 18px', minWidth: 230, maxWidth: 'calc(100% - 32px)'
          }}>
            <div className="mono-label" style={{
              fontSize: 11, fontWeight: 800, color: '#eaf4f8', marginBottom: 14,
              display: 'flex', justifyContent: 'space-between', alignItems: 'center'
            }}>
              <span>Map Layers</span>
              <button
                type="button"
                aria-label="Close Map Layers panel"
                onClick={() => setShowLayersPanel(false)}
                style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 16, color: '#8ea3ba' }}
              >×</button>
            </div>

            {[
              { key: 'lakes', label: 'GLOF Lakes', color: '#38bdf8', icon: '💧' },
              { key: 'provincial', label: 'Provincial Boundary', color: '#38bdf8', icon: '🗺️' },
              { key: 'districts', label: 'GLOF Districts', color: '#5eead4', icon: '📍' },
              {
                key: 'choropleth',
                label: districtLoading ? 'District Risk Choropleth...' : 'District Risk Choropleth',
                color: '#f5a524',
                icon: '📊',
              },
              ...(corridorData ? [
                { key: 'corridor', label: 'Flood Routing Trace', color: '#ec4899', icon: '🌊' },
              ] : []),
              { key: 'danger', label: 'Danger Zone', color: '#f0433a', icon: '🔴' },
              { key: 'warning', label: 'Warning Zone', color: '#f5a524', icon: '🟠' },
            ].map((item) => (
              <button
                key={item.key}
                type="button"
                role="switch"
                aria-checked={Boolean(layers[item.key])}
                onClick={() => toggleLayer(item.key)}
                style={{
                  width: '100%',
                  border: 'none',
                  textAlign: 'left',
                  display: 'flex', alignItems: 'center', gap: 12,
                  padding: '9px 12px', borderRadius: 10, cursor: 'pointer',
                  background: layers[item.key] ? 'rgba(94,234,212,0.08)' : 'transparent',
                  marginBottom: 6, transition: 'all 0.15s ease'
                }}
              >
                <span style={{
                  width: 18, height: 18, borderRadius: 5, flexShrink: 0,
                  border: `2px solid ${item.color}`,
                  background: layers[item.key] ? item.color : 'transparent',
                  display: 'flex', alignItems: 'center', justifyContent: 'center'
                }}>
                  {layers[item.key] && (
                    <span style={{ color: '#06131a', fontSize: 11, fontWeight: 800 }}>✓</span>
                  )}
                </span>
                <span style={{ fontSize: 15 }}>{item.icon}</span>
                <span style={{ fontSize: 13.5, fontWeight: 600, color: '#eaf4f8' }}>{item.label}</span>
              </button>
            ))}
          </div>
        )}

        <MapContainer center={[36.2, 74.5]} zoom={7} style={{ height: '100%', width: '100%' }}>
          <ScaleControl />
          <MapFlyToController flyTarget={flyTarget} />
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
            <GeoJSON
              key={`provincial-${spatialMode ? 'spatial' : 'browse'}`}
              data={provincialData}
              style={provincialStyle}
              interactive={!spatialMode}
            />
          )}

          {(layers.districts || layers.choropleth) && districtsData && (
            <GeoJSON
              key={`districts-${layers.choropleth ? 'choro' : 'base'}-${districtAnalytics ? 'loaded' : 'raw'}-${spatialMode ? 'spatial' : 'browse'}`}
              data={districtsData}
              style={getDistrictStyle}
              interactive={!spatialMode}
              onEachFeature={spatialMode ? undefined : onEachDistrict}
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

          {/* Animated Wavefront Progressive Trail & Crest Marker */}
          {corridorData && layers.corridor && showBreachSim && wavefrontState.trail.length > 1 && (
            <Polyline
              positions={wavefrontState.trail}
              pathOptions={{ color: '#fbbf24', weight: 6, opacity: 0.92 }}
            />
          )}

          {corridorData && layers.corridor && showBreachSim && wavefrontState.position && (
            <CircleMarker
              center={wavefrontState.position}
              radius={10}
              pathOptions={{
                color: '#ffffff',
                fillColor: '#fbbf24',
                fillOpacity: 1,
                weight: 3,
              }}
            >
              <Tooltip permanent direction="top" offset={[0, -10]}>
                <span style={{ fontFamily: 'monospace', fontSize: 11, fontWeight: 700 }}>
                  Wavefront T+{Math.round(simTimeMin)}m · {wavefrontState.distanceKm.toFixed(1)} km
                </span>
              </Tooltip>
            </CircleMarker>
          )}

          {corridorData && layers.corridor && storageNodes.map((node, idx) => {
            const [lon, lat] = node.geometry.coordinates;
            const props = node.properties || {};
            const simNode = breachData?.downstream_nodes?.[idx];
            const isInundating =
              showBreachSim &&
              simNode &&
              simTimeMin >= Number(simNode.eta_min) &&
              simTimeMin <= Number(simNode.recession_min);
            const activeFill =
              simNode?.severity === 'Critical'
                ? '#f0433a'
                : simNode?.severity === 'Warning'
                  ? '#f5a524'
                  : '#ec4899';

            return (
              <CircleMarker
                key={`storage-node-${idx}-${isInundating ? 'pulse' : 'idle'}`}
                center={[lat, lon]}
                radius={isInundating ? 11 : 7}
                pathOptions={{
                  color: '#ffffff',
                  fillColor: isInundating ? activeFill : '#ec4899',
                  fillOpacity: 1,
                  weight: isInundating ? 3 : 2,
                }}
              >
                <Popup>
                  <div style={{ minWidth: 200 }}>
                    <div style={{ fontSize: 11, fontWeight: 800, color: '#ec4899', textTransform: 'uppercase' }}>
                      Downstream Node #{props.sequence} ({props.node_type})
                    </div>
                    <div style={{ fontSize: 14, fontWeight: 800, color: '#0c1826', margin: '4px 0' }}>
                      {props.name}
                    </div>
                    <div style={{ fontSize: 12, color: '#334155' }}>
                      Distance from Lake: <strong>{props.distance_from_lake_km} km</strong><br />
                      Est. Wave Arrival: <strong>~{simNode ? simNode.eta_min : props.estimated_arrival_min} min</strong>
                      {simNode && (
                        <>
                          <br />
                          Recession: <strong>{simNode.recession_min} min</strong><br />
                          Attenuated Q: <strong>{Number(simNode.attenuated_q_m3s).toLocaleString()} m³/s</strong><br />
                          Stage Rise: <strong>+{simNode.stage_rise_m} m ({simNode.severity})</strong>
                        </>
                      )}
                    </div>
                  </div>
                </Popup>
              </CircleMarker>
            );
          })}

          {/* WARNING ZONES (draw first so danger appears on top) */}
          {layers.warning && filteredLakes.map((lake) => {
            if (!lake.latitude || !lake.longitude) return null;
            const risk = getRisk(lake.area_ha, lake.risk_level);
            const { warning } = getZoneRadii(risk);
            return (
              <Circle
                key={`warn-${lake.id}`}
                center={[lake.latitude, lake.longitude]}
                radius={warning}
                interactive={!spatialMode}
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
          {layers.danger && filteredLakes.map((lake) => {
            if (!lake.latitude || !lake.longitude) return null;
            const risk = getRisk(lake.area_ha, lake.risk_level);
            const { danger } = getZoneRadii(risk);
            return (
              <Circle
                key={`danger-${lake.id}`}
                center={[lake.latitude, lake.longitude]}
                radius={danger}
                interactive={!spatialMode}
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
                        type="button"
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
                        type="button"
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

      {/* Docked Dam-Breach & Flood-Wave Simulator */}
      {showBreachSim && corridorData?.lake?.id && (
        <BreachSimulatorPanel
          lake={corridorData.lake}
          corridorData={corridorData}
          simTimeMin={simTimeMin}
          onTimeChange={setSimTimeMin}
          isPlaying={simPlaying}
          onPlayingChange={setSimPlaying}
          onSimulationData={setBreachData}
          onClose={() => {
            setShowBreachSim(false);
            setSimPlaying(false);
          }}
        />
      )}

      {/* Legend */}
      <div style={{ marginTop: '16px', display: 'flex', justifyContent: 'center', flexWrap: 'wrap', gap: 12 }}>
        <div style={{
          background: 'rgba(94,234,212,0.05)', padding: '10px 24px', borderRadius: '14px',
          border: '1px solid var(--line, #dbe4ec)', display: 'flex', alignItems: 'center',
          gap: '24px', fontSize: '13px', color: 'var(--text-hi, #0c1826)', flexWrap: 'wrap'
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

        {(layers.danger || layers.warning || showZones) && (
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

        {layers.choropleth && (
          <div style={{
            background: 'rgba(56,189,248,0.06)', padding: '10px 20px', borderRadius: '14px',
            border: '1px solid rgba(56,189,248,0.22)', display: 'flex', alignItems: 'center',
            gap: '14px', fontSize: '12.5px', flexWrap: 'wrap'
          }}>
            <span className="mono-label" style={{ color: '#8ea3ba', fontSize: '10.5px' }}>
              District Hazard Density
            </span>
            {[
              { color: '#334155', label: '0' },
              { color: '#2dd48e', label: '1' },
              { color: '#38bdf8', label: '2–4' },
              { color: '#f5a524', label: '5–9' },
              { color: '#f0433a', label: '10+ / High' },
            ].map((swatch) => (
              <div key={swatch.label} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ width: 12, height: 12, borderRadius: 3, backgroundColor: swatch.color, border: '1px solid rgba(255,255,255,0.45)' }} />
                <span style={{ fontWeight: 600 }}>{swatch.label}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

export default MapView;
