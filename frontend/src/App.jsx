import { useEffect, useState, createContext, useContext } from 'react';
import axios from 'axios';
import { motion, AnimatePresence } from 'framer-motion';
import MapView from './components/MapView';
import LandingPage from './components/LandingPage';
import jsPDF from 'jspdf';
import autoTable from 'jspdf-autotable';
import {
  PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, Legend, LineChart, Line
} from 'recharts';
import {
  IconDashboard, IconLakes, IconPlus, IconMountain,
  IconSearch, IconDownload, IconFilePdf, IconShield,
  IconTrash, IconInfo, IconPulseDot, IconSatellite, IconGlobe
} from './components/Icons';
import {
  pageTransition,
  staggerContainer,
  staggerFast,
  cardItem,
  slideFromLeft,
  modalBackdrop,
  modalPanel,
  springSnappy,
  easeOut,
} from './motion';
import { API_BASE } from './api';
import './App.css';

const ThemeContext = createContext();

function ThemeProvider({ children }) {
  const [theme, setTheme] = useState(() => localStorage.getItem('glof-theme') || 'light');
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('glof-theme', theme);
  }, [theme]);
  const toggleTheme = () => setTheme(prev => (prev === 'light' ? 'dark' : 'light'));
  return (
    <ThemeContext.Provider value={{ theme, toggleTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

function useTheme() {
  return useContext(ThemeContext);
}

const FAMOUS_LAKES = [
  { name: 'Attabad Lake', area_ha: 280, latitude: 36.3369, longitude: 74.8675 },
  { name: 'Shisper Lake', area_ha: 48.5, latitude: 36.4120, longitude: 74.6230 },
  { name: 'Hassanabad Lake', area_ha: 42, latitude: 36.4050, longitude: 74.6150 },
  { name: 'Passu Lake', area_ha: 12.5, latitude: 36.4680, longitude: 74.8950 },
  { name: 'Ghamu Bar Lake', area_ha: 16.7, latitude: 36.6420, longitude: 73.4090 },
  { name: 'Khurdopin Lake', area_ha: 22, latitude: 36.3850, longitude: 75.1120 },
  { name: 'Rush Lake', area_ha: 13.8, latitude: 36.1740, longitude: 74.8850 },
  { name: 'Borit Lake', area_ha: 7.5, latitude: 36.4320, longitude: 74.8620 },
  { name: 'Shimshal Lake', area_ha: 19.5, latitude: 36.4850, longitude: 75.3250 },
  { name: 'Hispar Lake', area_ha: 14.2, latitude: 36.1780, longitude: 75.1870 },
  { name: 'Satpara Lake', area_ha: 28, latitude: 35.2250, longitude: 75.6320 },
  { name: 'Sokha Lake', area_ha: 11.4, latitude: 35.9180, longitude: 75.4210 },
  { name: 'Baltoro Lake', area_ha: 35, latitude: 35.7520, longitude: 76.4350 },
  { name: 'Talidas Lake', area_ha: 5.2, latitude: 36.2100, longitude: 73.8500 },
];

const calculateRisk = (area) => {
  if (area >= 20) return 'High';
  if (area >= 10) return 'Medium';
  return 'Low';
};

const RISK_COLOR = { High: '#f0433a', Medium: '#f5a524', Low: '#2dd48e' };

const isUnknownName = (name) => {
  if (!name) return true;
  const n = name.toLowerCase().trim();
  return (
    n.startsWith('glof lake') ||
    n.startsWith('unknown') ||
    n === 'unnamed' ||
    n.startsWith('unnamed lake') ||
    /^lake\s*\d+$/.test(n) ||
    /^glof[-_\s]*\d+$/.test(n)
  );
};

function AppContent() {
  const { theme, toggleTheme } = useTheme();
  const isDark = theme === 'dark';

  const [enteredPortal, setEnteredPortal] = useState(false);
  const [lakes, setLakes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedRisk, setSelectedRisk] = useState('All');
  const [selectedLake, setSelectedLake] = useState(null);
  const [activeTab, setActiveTab] = useState('dashboard');
  const [searchQuery, setSearchQuery] = useState('');
  const [showUnknownOnly, setShowUnknownOnly] = useState(false);

  const [detecting, setDetecting] = useState(false);
  const [detectedLakes, setDetectedLakes] = useState([]);
  const [lastGEEResults, setLastGEEResults] = useState([]);
  const [saving, setSaving] = useState(false);
  const [satelliteRisk, setSatelliteRisk] = useState('All');

  const [populationRisk, setPopulationRisk] = useState('All');
  const [exposureData, setExposureData] = useState({});
  const [exposureLoading, setExposureLoading] = useState(false);

  // Flood Monitoring / Early Warning
  const [ewData, setEwData] = useState(null);
  const [ewLoading, setEwLoading] = useState(false);
  const [ewUseGee, setEwUseGee] = useState(false);
  const [ewFilter, setEwFilter] = useState('All');
  const [ewSelected, setEwSelected] = useState(null);

  // GLOF Basins
  const [basinsData, setBasinsData] = useState(null);
  const [basinsLoading, setBasinsLoading] = useState(false);
  const [selectedBasinId, setSelectedBasinId] = useState(null);
  const [basinThumbNdwi, setBasinThumbNdwi] = useState(null);
  const [basinThumbRgb, setBasinThumbRgb] = useState(null);
  const [basinThumbLoading, setBasinThumbLoading] = useState(false);

  // Historical
  const [histLakeId, setHistLakeId] = useState('');
  const [histData, setHistData] = useState(null);
  const [histLoading, setHistLoading] = useState(false);
  const [thumbNdwi, setThumbNdwi] = useState(null);
  const [thumbRgb, setThumbRgb] = useState(null);
  const [thumbLoading, setThumbLoading] = useState(false);

  // Lake Details modal (inventory → Details)
  const [detailHistData, setDetailHistData] = useState(null);
  const [detailHistLoading, setDetailHistLoading] = useState(false);
  const [detailThumbNdwi, setDetailThumbNdwi] = useState(null);
  const [detailThumbRgb, setDetailThumbRgb] = useState(null);
  const [detailThumbLoading, setDetailThumbLoading] = useState(false);

  // Register
  const [formData, setFormData] = useState({ name: '', area_ha: '', latitude: '', longitude: '' });
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState('');
  const [suggestions, setSuggestions] = useState([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [foundLakeForMap, setFoundLakeForMap] = useState(null);
  const [regThumbNdwi, setRegThumbNdwi] = useState(null);
  const [regThumbRgb, setRegThumbRgb] = useState(null);
  const [regThumbLoading, setRegThumbLoading] = useState(false);

  const [renameLake, setRenameLake] = useState(null);
  const [newName, setNewName] = useState('');

  const [dashboardWeather, setDashboardWeather] = useState(null);
  const [lakeWeather, setLakeWeather] = useState(null);
  const [weatherLoading, setWeatherLoading] = useState(false);

  const fetchLakes = () => {
    axios.get(`${API_BASE}/lakes/`)
      .then(res => { setLakes(res.data); setLoading(false); })
      .catch(() => setLoading(false));
  };

  const fetchWeather = async (lat, lon) => {
    try {
      const url = `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&current=temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m&timezone=auto`;
      const res = await axios.get(url);
      return res.data.current;
    } catch {
      return null;
    }
  };

  const fetchExposureForLakes = async (lakesList) => {
    setExposureLoading(true);
    const results = {};
    const targets = lakesList.filter(l => {
      const r = calculateRisk(l.area_ha);
      return (r === 'High' || r === 'Medium') && l.latitude && l.longitude;
    }).slice(0, 12);

    for (const lake of targets) {
      try {
        const risk = calculateRisk(lake.area_ha);
        const res = await axios.get(`${API_BASE}/gee/exposure`, {
          params: { lat: lake.latitude, lon: lake.longitude, risk_level: risk }
        });
        results[lake.id] = res.data;
      } catch {
        results[lake.id] = { danger_population: 0, warning_population: 0, error: true };
      }
    }
    setExposureData(results);
    setExposureLoading(false);
  };

  const fetchThumbnails = async (lake, { setNdwi, setRgb, setLoading }) => {
    if (!lake?.latitude || !lake?.longitude) return;
    setLoading(true);
    setNdwi(null);
    setRgb(null);
    try {
      const [ndwiRes, rgbRes] = await Promise.all([
        axios.get(`${API_BASE}/gee/thumbnail`, {
          params: { lat: lake.latitude, lon: lake.longitude, mode: 'ndwi' }
        }),
        axios.get(`${API_BASE}/gee/thumbnail`, {
          params: { lat: lake.latitude, lon: lake.longitude, mode: 'rgb' }
        })
      ]);
      setNdwi(ndwiRes.data.url);
      setRgb(rgbRes.data.url);
    } catch {
      // optional satellite imagery
    } finally {
      setLoading(false);
    }
  };

  const fetchHistorical = async (lake) => {
    if (!lake?.latitude || !lake?.longitude) return;
    setHistLoading(true);
    setHistData(null);
    setThumbNdwi(null);
    setThumbRgb(null);
    try {
      const res = await axios.get(`${API_BASE}/gee/historical`, {
        params: { lat: lake.latitude, lon: lake.longitude }
      });
      setHistData(res.data);
    } catch {
      alert('Failed to load historical data from GEE');
    } finally {
      setHistLoading(false);
    }

    await fetchThumbnails(lake, {
      setNdwi: setThumbNdwi,
      setRgb: setThumbRgb,
      setLoading: setThumbLoading,
    });
  };

  const openLakeDetails = (lake) => {
    setSelectedLake(lake);
    setDetailHistData(null);
    setDetailThumbNdwi(null);
    setDetailThumbRgb(null);

    if (!lake?.latitude || !lake?.longitude) return;

    // Satellite images first (usually faster than full history series)
    fetchThumbnails(lake, {
      setNdwi: setDetailThumbNdwi,
      setRgb: setDetailThumbRgb,
      setLoading: setDetailThumbLoading,
    });

    // Area history for this lake (same GEE series as Historical tab)
    setDetailHistLoading(true);
    axios
      .get(`${API_BASE}/gee/historical`, {
        params: { lat: lake.latitude, lon: lake.longitude }
      })
      .then((res) => setDetailHistData(res.data))
      .catch(() => setDetailHistData(null))
      .finally(() => setDetailHistLoading(false));
  };

  const closeLakeDetails = () => {
    setSelectedLake(null);
    setDetailHistData(null);
    setDetailThumbNdwi(null);
    setDetailThumbRgb(null);
    setDetailHistLoading(false);
    setDetailThumbLoading(false);
  };

  useEffect(() => {
    if (enteredPortal) {
      fetchLakes();
      fetchWeather(35.92, 74.31).then(setDashboardWeather);
    }
  }, [enteredPortal]);

  // Auto-load basin satellite when selection changes
  useEffect(() => {
    if (activeTab !== 'basins' || !basinsData?.basins?.length || !selectedBasinId) return;
    const basin = basinsData.basins.find((b) => b.id === selectedBasinId);
    if (basin?.center) {
      loadBasinSatellite(basin);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, selectedBasinId, basinsData]);

  useEffect(() => {
    if (selectedLake?.latitude && selectedLake?.longitude) {
      setWeatherLoading(true);
      fetchWeather(selectedLake.latitude, selectedLake.longitude)
        .then(data => { setLakeWeather(data); setWeatherLoading(false); });
    } else {
      setLakeWeather(null);
    }
  }, [selectedLake]);

  if (!enteredPortal) {
    return <LandingPage onLaunch={() => setEnteredPortal(true)} />;
  }

  const highRisk = lakes.filter(l => calculateRisk(l.area_ha) === 'High').length;
  const mediumRisk = lakes.filter(l => calculateRisk(l.area_ha) === 'Medium').length;
  const lowRisk = lakes.filter(l => calculateRisk(l.area_ha) === 'Low').length;
  const totalLakes = lakes.length;
  const unknownCount = lakes.filter(l => isUnknownName(l.name)).length;

  const filteredLakes = lakes
    .filter(l => selectedRisk === 'All' || calculateRisk(l.area_ha) === selectedRisk)
    .filter(l => !searchQuery || l.name.toLowerCase().includes(searchQuery.toLowerCase()))
    .filter(l => !showUnknownOnly || isUnknownName(l.name));

  const riskPieData = [
    { name: 'High Risk', value: highRisk, color: '#f0433a' },
    { name: 'Medium Risk', value: mediumRisk, color: '#f5a524' },
    { name: 'Low Risk', value: lowRisk, color: '#2dd48e' },
  ].filter(d => d.value > 0);

  const riskBarData = [
    { name: 'High', count: highRisk, fill: '#f0433a' },
    { name: 'Medium', count: mediumRisk, fill: '#f5a524' },
    { name: 'Low', count: lowRisk, fill: '#2dd48e' },
  ];

  const topLakesData = [...lakes]
    .sort((a, b) => b.area_ha - a.area_ha)
    .slice(0, 5)
    .map(l => ({
      name: l.name.length > 14 ? l.name.slice(0, 12) + '…' : l.name,
      area: Number(l.area_ha),
      fill: RISK_COLOR[calculateRisk(l.area_ha)]
    }));

  const topExposureData = lakes
    .filter(l => exposureData[l.id])
    .sort((a, b) => (exposureData[b.id]?.danger_population || 0) - (exposureData[a.id]?.danger_population || 0))
    .slice(0, 5)
    .map(l => ({
      name: l.name.length > 14 ? l.name.slice(0, 12) + '…' : l.name,
      danger: exposureData[l.id]?.danger_population || 0,
      warning: exposureData[l.id]?.warning_population || 0,
    }));

  const totalDangerPop = Object.values(exposureData).reduce((s, e) => s + (e.danger_population || 0), 0);
  const totalWarningPop = Object.values(exposureData).reduce((s, e) => s + (e.warning_population || 0), 0);

  const histChartData = (histData?.years || [])
    .filter(y => y.area_ha != null)
    .map(y => ({ year: String(y.year), area: y.area_ha }));

  const selectedHistLake = lakes.find(l => String(l.id) === String(histLakeId));

  const handleNameChange = (e) => {
    const value = e.target.value;
    setFormData(prev => ({ ...prev, name: value }));
    setFoundLakeForMap(null);
    setRegThumbNdwi(null);
    setRegThumbRgb(null);
    if (value.trim().length < 2) {
      setSuggestions([]);
      setShowSuggestions(false);
      return;
    }
    const search = value.toLowerCase();
    const matches = [...FAMOUS_LAKES, ...lastGEEResults, ...lakes]
      .filter(l => l.name.toLowerCase().includes(search))
      .slice(0, 8);
    setSuggestions(matches);
    setShowSuggestions(matches.length > 0);
  };

  const applySuggestion = (lake) => {
    setFormData({
      name: lake.name,
      area_ha: lake.area_ha ?? '',
      latitude: lake.latitude ?? '',
      longitude: lake.longitude ?? ''
    });
    setFoundLakeForMap(lake);
    setShowSuggestions(false);
  };

  const handleFindLake = async () => {
    if (!formData.name.trim()) {
      alert('Please type a lake name first');
      return;
    }

    const search = formData.name.toLowerCase().trim();
    const found =
      FAMOUS_LAKES.find(l => l.name.toLowerCase().includes(search)) ||
      lastGEEResults.find(l => l.name.toLowerCase().includes(search)) ||
      lakes.find(l => l.name.toLowerCase().includes(search));

    if (!found) {
      alert('Lake not found. Try a more exact name (e.g. Attabad, Shisper, Ghamu Bar).');
      setFoundLakeForMap(null);
      setRegThumbNdwi(null);
      setRegThumbRgb(null);
      return;
    }

    setFormData({
      name: found.name,
      area_ha: found.area_ha ?? '',
      latitude: found.latitude ?? '',
      longitude: found.longitude ?? '',
    });
    setFoundLakeForMap(found);
    setShowSuggestions(false);

    if (found.latitude && found.longitude) {
      setRegThumbLoading(true);
      setRegThumbNdwi(null);
      setRegThumbRgb(null);
      try {
        const [ndwiRes, rgbRes] = await Promise.all([
          axios.get(`${API_BASE}/gee/thumbnail`, {
            params: { lat: found.latitude, lon: found.longitude, mode: 'ndwi' }
          }),
          axios.get(`${API_BASE}/gee/thumbnail`, {
            params: { lat: found.latitude, lon: found.longitude, mode: 'rgb' }
          })
        ]);
        setRegThumbNdwi(ndwiRes.data.url);
        setRegThumbRgb(rgbRes.data.url);
      } catch (err) {
        console.warn('Thumbnail load failed', err);
      } finally {
        setRegThumbLoading(false);
      }
    }
  };

  const handleDetectLakes = async () => {
    setDetecting(true);
    try {
      const res = await axios.get(`${API_BASE}/gee/detect-lakes`);
      const data = res.data.lakes || [];
      setDetectedLakes(data);
      setLastGEEResults(data);
    } catch {
      alert('Detection failed');
    } finally {
      setDetecting(false);
    }
  };

  const handleSaveDetectedLakes = async () => {
    if (!detectedLakes.length) return;
    if (!window.confirm(`Save all ${detectedLakes.length} detected lakes?`)) return;
    setSaving(true);
    try {
      const payload = detectedLakes.map(l => ({
        name: l.name,
        area_ha: l.area_ha,
        latitude: l.latitude,
        longitude: l.longitude
      }));
      const res = await axios.post(`${API_BASE}/lakes/bulk`, payload);
      alert(res.data.message);
      fetchLakes();
    } catch {
      alert('Failed to save lakes');
    } finally {
      setSaving(false);
    }
  };

  const handleMatchNames = async () => {
    if (!window.confirm('Auto-rename lakes close to known famous lakes?')) return;
    try {
      const res = await axios.post(`${API_BASE}/lakes/match-names`);
      alert(res.data.message);
      fetchLakes();
    } catch {
      alert('Name matching failed');
    }
  };

  const openRenameModal = (lake) => {
    setRenameLake(lake);
    setNewName(lake.name);
  };

  const handleRenameSubmit = async () => {
    if (!newName.trim()) return alert('Please enter a valid name');
    try {
      await axios.put(`${API_BASE}/lakes/${renameLake.id}/rename?new_name=${encodeURIComponent(newName.trim())}`);
      alert('Lake renamed successfully');
      setRenameLake(null);
      setNewName('');
      fetchLakes();
    } catch {
      alert('Rename failed');
    }
  };

  const exportToCSV = () => {
    if (!filteredLakes.length) return alert('No lakes');
    const headers = ['ID', 'Name', 'Area', 'Risk', 'Lat', 'Lon'];
    const rows = filteredLakes.map(l => [
      l.id, `"${l.name}"`, l.area_ha, calculateRisk(l.area_ha), l.latitude ?? '', l.longitude ?? ''
    ]);
    const csv = [headers.join(','), ...rows.map(r => r.join(','))].join('\n');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([csv]));
    a.download = `GLOF_Lakes.csv`;
    a.click();
  };

  const exportToPDF = () => {
    if (!filteredLakes.length) return alert('No lakes');
    const doc = new jsPDF();
    doc.text('GLOF Portal Report', 14, 20);
    autoTable(doc, {
      startY: 28,
      head: [['#', 'Name', 'Area', 'Risk', 'Lat', 'Lon']],
      body: filteredLakes.map((l, i) => [
        i + 1, l.name, l.area_ha, calculateRisk(l.area_ha), l.latitude ?? '—', l.longitude ?? '—'
      ]),
      headStyles: { fillColor: [2, 132, 199] }
    });
    doc.save('GLOF_Lakes.pdf');
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setMessage('');
    try {
      await axios.post(`${API_BASE}/lakes/`, {
        name: formData.name,
        area_ha: parseFloat(formData.area_ha),
        latitude: formData.latitude ? parseFloat(formData.latitude) : null,
        longitude: formData.longitude ? parseFloat(formData.longitude) : null
      });
      setMessage('Lake registered successfully');
      setFormData({ name: '', area_ha: '', latitude: '', longitude: '' });
      setFoundLakeForMap(null);
      setRegThumbNdwi(null);
      setRegThumbRgb(null);
      fetchLakes();
    } catch {
      setMessage('Failed to register');
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id, name) => {
    if (!window.confirm(`Delete "${name}"?`)) return;
    try {
      await axios.delete(`${API_BASE}/lakes/${id}`);
      fetchLakes();
    } catch {
      alert('Delete failed');
    }
  };

  const fetchEarlyWarningBoard = async (useGee = ewUseGee) => {
    setEwLoading(true);
    setEwSelected(null);
    try {
      // Fast path default — avoid GEE timeouts; boolean must be string for some proxies
      const res = await axios.get(`${API_BASE}/early-warning/monitor`, {
        params: { use_gee: useGee ? 'true' : 'false', limit: 46 },
        timeout: useGee ? 300000 : 90000,
      });
      if (!res.data?.lakes) {
        throw new Error('Invalid response from early-warning API');
      }
      setEwData(res.data);
    } catch (err) {
      console.error('Flood monitoring scan failed', err);
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail || err.message || 'Failed to load early warning scores';
      const msg = status === 404
        ? 'Early-warning API not found. Restart the backend (uvicorn) so /early-warning routes are loaded.'
        : status === 429
          ? 'Rate limited — wait a minute and try again.'
          : (typeof detail === 'string' ? detail : JSON.stringify(detail));
      alert(`Flood Monitoring failed: ${msg}`);
      setEwData(null);
    } finally {
      setEwLoading(false);
    }
  };

  const loadBasinSatellite = async (basin) => {
    const lat = basin?.center?.lat ?? basin?.center?.latitude;
    const lon = basin?.center?.lon ?? basin?.center?.longitude;
    if (lat == null || lon == null || Number.isNaN(Number(lat)) || Number.isNaN(Number(lon))) {
      console.warn('Basin has no center coordinates', basin);
      setBasinThumbNdwi(null);
      setBasinThumbRgb(null);
      setBasinThumbLoading(false);
      return;
    }
    setBasinThumbLoading(true);
    setBasinThumbNdwi(null);
    setBasinThumbRgb(null);
    // Use backend-proxied PNG endpoints (browser-safe). Wider buffer for basin overview.
    const qs = (mode) =>
      `${API_BASE}/gee/thumbnail-image?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}&mode=${mode}&buffer_m=12000&t=${Date.now()}`;
    // Prefetch so we only flip state when images actually resolve
    const loadImg = (src) =>
      new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(src);
        img.onerror = () => reject(new Error(`Failed to load ${src}`));
        img.src = src;
      });
    try {
      const [ndwiSrc, rgbSrc] = await Promise.all([
        loadImg(qs('ndwi')),
        loadImg(qs('rgb')),
      ]);
      setBasinThumbNdwi(ndwiSrc);
      setBasinThumbRgb(rgbSrc);
    } catch (err) {
      console.error('Basin satellite load failed', err);
      // Fallback: try JSON thumbnail URLs (older path)
      try {
        const [ndwiRes, rgbRes] = await Promise.all([
          axios.get(`${API_BASE}/gee/thumbnail`, {
            params: { lat, lon, mode: 'ndwi', buffer_m: 12000 },
            timeout: 120000,
          }),
          axios.get(`${API_BASE}/gee/thumbnail`, {
            params: { lat, lon, mode: 'rgb', buffer_m: 12000 },
            timeout: 120000,
          }),
        ]);
        setBasinThumbNdwi(ndwiRes.data?.url || null);
        setBasinThumbRgb(rgbRes.data?.url || null);
        if (!ndwiRes.data?.url && !rgbRes.data?.url) {
          alert(ndwiRes.data?.error || rgbRes.data?.error || 'Satellite images unavailable for this basin');
        }
      } catch (err2) {
        console.error('Basin satellite fallback failed', err2);
        setBasinThumbNdwi(null);
        setBasinThumbRgb(null);
        const detail = err2?.response?.data?.detail || err2.message;
        alert(`Basin satellite failed: ${typeof detail === 'string' ? detail : 'GEE thumbnail error'}`);
      }
    } finally {
      setBasinThumbLoading(false);
    }
  };

  const fetchBasins = async () => {
    setBasinsLoading(true);
    try {
      const res = await axios.get(`${API_BASE}/early-warning/basins`, { timeout: 60000 });
      setBasinsData(res.data);
      const list = res.data?.basins || [];
      const first = list[0];
      if (first) {
        const nextId = list.some((b) => b.id === selectedBasinId) ? selectedBasinId : first.id;
        setSelectedBasinId(nextId);
        const active = list.find((b) => b.id === nextId) || first;
        loadBasinSatellite(active);
      }
    } catch (err) {
      console.error('Basins load failed', err);
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail || err.message || 'Failed to load basins';
      const msg = status === 404
        ? 'Basins API not found. Restart the backend so /early-warning/basins is available.'
        : (typeof detail === 'string' ? detail : JSON.stringify(detail));
      alert(`GLOF Basins failed: ${msg}`);
      setBasinsData(null);
    } finally {
      setBasinsLoading(false);
    }
  };

  const getRiskExplanation = (lake) => {
    const area = Number(lake.area_ha) || 0;
    const risk = calculateRisk(area);
    if (risk === 'High') {
      return {
        title: 'High Outburst Risk',
        color: '#f0433a',
        bg: isDark ? 'rgba(240,67,58,0.12)' : '#fef2f2',
        reason: `Large surface area of ${area} ha. High potential for catastrophic flood.`,
      };
    }
    if (risk === 'Medium') {
      return {
        title: 'Medium Outburst Risk',
        color: '#f5a524',
        bg: isDark ? 'rgba(245,165,36,0.12)' : '#fff7ed',
        reason: `Moderate area of ${area} ha. Can still cause significant flooding.`,
      };
    }
    return {
      title: 'Low Outburst Risk',
      color: '#2dd48e',
      bg: isDark ? 'rgba(45,212,142,0.12)' : '#f0fdf4',
      reason: `Small area of ${area} ha. Limited flood potential.`,
    };
  };

  const pal = {
    appBg: isDark ? '#060b13' : '#eef2f6',
    panel: isDark ? '#0c1826' : '#ffffff',
    panelAlt: isDark ? '#101d2c' : '#f6f9fb',
    border: isDark ? 'rgba(103,232,249,0.14)' : '#dde5ec',
    hi: isDark ? '#eaf4f8' : '#0c1826',
    mid: isDark ? '#8ea3ba' : '#5b7690',
    lo: isDark ? '#4c6079' : '#94a7ba',
    accent: isDark ? '#5eead4' : '#0d9488',
    signal: isDark ? '#38bdf8' : '#0284c7',
  };

  const navItem = (tab, label, Icon) => {
    const active = activeTab === tab;
    return (
      <motion.div
        onClick={() => setActiveTab(tab)}
        className="btn-3d"
        whileHover={{ x: active ? 0 : 4 }}
        whileTap={{ scale: 0.98 }}
        transition={springSnappy}
        layout
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 14,
          padding: '13px 16px',
          borderRadius: 12,
          cursor: 'pointer',
          fontFamily: 'var(--font-display)',
          fontWeight: active ? 700 : 500,
          fontSize: 14.5,
          color: active ? '#06131a' : '#8ea3ba',
          background: active ? 'linear-gradient(135deg,#5eead4,#38bdf8)' : 'transparent',
          boxShadow: active ? '0 4px 18px rgba(94,234,212,0.22)' : 'none',
        }}
      >
        <Icon size={17} color={active ? '#06131a' : '#8ea3ba'} />
        {label}
      </motion.div>
    );
  };

  const mapDetected = detectedLakes.map((l, i) => ({
    ...l,
    id: 1000 + i,
    risk_level: calculateRisk(l.area_ha)
  }));

  const cardStyle = {
    background: pal.panel,
    border: `1px solid ${pal.border}`,
    borderRadius: 18,
    boxShadow: isDark ? '0 4px 24px rgba(0,0,0,0.35)' : '0 4px 20px rgba(15,23,42,0.05)',
  };

  const sectionTitle = {
    margin: 0,
    fontFamily: 'var(--font-display)',
    fontWeight: 700,
    fontSize: 18,
    color: pal.hi,
    letterSpacing: '-0.01em'
  };

  const eyebrow = {
    fontFamily: 'var(--font-mono)',
    fontSize: 10.5,
    letterSpacing: '0.09em',
    textTransform: 'uppercase',
    color: pal.accent,
    fontWeight: 600,
    marginBottom: 4
  };

  const chartTooltipStyle = {
    backgroundColor: isDark ? '#0c1826' : '#fff',
    border: `1px solid ${pal.border}`,
    borderRadius: 10,
    color: pal.hi,
    fontSize: 13
  };

  const trendColor = histData?.trend === 'growing' ? '#f0433a' : histData?.trend === 'shrinking' ? '#2dd48e' : pal.signal;

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.35 }}
      style={{ display: 'flex', minHeight: '100vh', background: pal.appBg, fontFamily: 'var(--font-body)' }}
    >
      {/* Sidebar */}
      <motion.aside
        variants={slideFromLeft}
        initial="hidden"
        animate="show"
        style={{
          width: 270,
          background: '#060b13',
          padding: '26px 16px',
          display: 'flex',
          flexDirection: 'column',
          position: 'sticky',
          top: 0,
          height: '100vh',
          borderRight: '1px solid rgba(103,232,249,0.1)'
        }}
      >
        <div style={{ marginBottom: 34, display: 'flex', alignItems: 'center', gap: 12, padding: '0 8px' }}>
          <motion.div
            whileHover={{ rotate: 10, scale: 1.08 }}
            transition={springSnappy}
            className="hud-frame"
            style={{
              width: 42, height: 42, borderRadius: 12,
              background: 'linear-gradient(135deg,#101d2c,#0c1826)',
              border: '1px solid rgba(103,232,249,0.2)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
          >
            <IconMountain size={20} color="#5eead4" />
          </motion.div>
          <div>
            <h1 style={{ margin: 0, fontFamily: 'var(--font-display)', fontSize: 17, color: '#f8fafc', fontWeight: 700 }}>GLOF Portal</h1>
            <p style={{ margin: 0, fontFamily: 'var(--font-mono)', fontSize: 9.5, letterSpacing: '0.08em', color: '#5eead4', fontWeight: 600 }}>GIS RISK INTELLIGENCE</p>
          </div>
        </div>

        <motion.nav
          variants={staggerFast}
          initial="hidden"
          animate="show"
          style={{ display: 'flex', flexDirection: 'column', gap: 6, flex: 1 }}
        >
          {navItem('dashboard', 'Dashboard', IconDashboard)}
          {navItem('analytics', 'Analytics', IconShield)}
          {navItem('population', 'Population', IconInfo)}
          {navItem('flood', 'Flood Monitoring', IconPulseDot)}
          {navItem('basins', 'GLOF Basins', IconGlobe)}
          {navItem('historical', 'Historical', IconSatellite)}
          {navItem('lakes', 'Lakes Inventory', IconLakes)}
          {navItem('add', 'Register New Lake', IconPlus)}
          {navItem('satellite', 'Satellite Detection', IconSatellite)}
        </motion.nav>

        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.35, duration: 0.4, ease: easeOut }}
          style={{ padding: 15, borderRadius: 12, background: '#0c1826', border: '1px solid rgba(103,232,249,0.14)' }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <motion.span
              animate={{ scale: [1, 1.25, 1], opacity: [1, 0.55, 1] }}
              transition={{ duration: 1.8, repeat: Infinity }}
              style={{ display: 'inline-flex' }}
            >
              <IconPulseDot color="#2dd48e" />
            </motion.span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, letterSpacing: '0.06em', color: '#2dd48e', fontWeight: 700 }}>SYSTEM ACTIVE</span>
          </div>
          <p style={{ margin: 0, fontSize: 13, color: '#f8fafc', fontWeight: 600 }}>Gilgit-Baltistan</p>
          <p style={{ margin: '2px 0 0', fontFamily: 'var(--font-mono)', fontSize: 10, color: '#4c6079' }}>35.92°N · 74.31°E</p>
        </motion.div>
      </motion.aside>

      {/* Main Content */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <header style={{
          padding: '18px 32px',
          background: pal.panel,
          borderBottom: `1px solid ${pal.border}`,
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center'
        }}>
          <div>
            <div style={eyebrow}>Command Overview</div>
            <AnimatePresence mode="wait">
              <motion.h2
                key={activeTab}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8 }}
                transition={{ duration: 0.22, ease: easeOut }}
                style={{ margin: 0, fontFamily: 'var(--font-display)', fontSize: 20, fontWeight: 700, color: pal.hi }}
              >
                {activeTab === 'dashboard' && 'Dashboard'}
                {activeTab === 'analytics' && 'Analytics'}
                {activeTab === 'population' && 'Population Exposure'}
                {activeTab === 'flood' && 'Flood Monitoring'}
                {activeTab === 'basins' && 'GLOF Basins'}
                {activeTab === 'historical' && 'Historical Analysis'}
                {activeTab === 'lakes' && 'Lakes Inventory'}
                {activeTab === 'add' && 'Register New Lake'}
                {activeTab === 'satellite' && 'Satellite Detection'}
              </motion.h2>
            </AnimatePresence>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <motion.button
              onClick={toggleTheme}
              whileHover={{ scale: 1.08, rotate: 12 }}
              whileTap={{ scale: 0.92, rotate: -8 }}
              style={{
                width: 40, height: 40, borderRadius: 11, cursor: 'pointer', fontSize: 17,
                border: `1px solid ${pal.border}`, background: pal.panelAlt,
                display: 'flex', alignItems: 'center', justifyContent: 'center'
              }}
            >
              {isDark ? '☀️' : '🌙'}
            </motion.button>
            <motion.div
              key={totalLakes}
              initial={{ scale: 0.9, opacity: 0.6 }}
              animate={{ scale: 1, opacity: 1 }}
              className="mono-label"
              style={{
                background: isDark ? 'rgba(94,234,212,0.1)' : '#f0fdfa',
                border: isDark ? '1px solid rgba(94,234,212,0.28)' : '1px solid #99f6e4',
                padding: '9px 16px', borderRadius: 11, color: pal.accent, fontWeight: 700, fontSize: 12
              }}
            >
              Total Lakes: {totalLakes}
            </motion.div>
          </div>
        </header>

        <main style={{ flex: 1, padding: '28px 32px', overflowY: 'auto' }}>
          <AnimatePresence mode="wait">
            <motion.div
              key={activeTab}
              variants={pageTransition}
              initial="initial"
              animate="animate"
              exit="exit"
            >

          {/* DASHBOARD */}
          {activeTab === 'dashboard' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
              <motion.div
                className="hud-frame"
                variants={cardItem}
                initial="hidden"
                animate="show"
                style={{ ...cardStyle, padding: '20px 24px' }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                  <div>
                    <div style={eyebrow}>Atmospheric Telemetry</div>
                    <h3 style={sectionTitle}>Gilgit-Baltistan Basin</h3>
                  </div>
                  <span className="mono-label" style={{ fontSize: 10.5, color: pal.lo }}>Live · Open-Meteo</span>
                </div>
                {dashboardWeather ? (
                  <motion.div
                    variants={staggerContainer}
                    initial="hidden"
                    animate="show"
                    style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16 }}
                  >
                    {[
                      { label: 'Temperature', value: `${dashboardWeather.temperature_2m}°C`, color: pal.signal },
                      { label: 'Precipitation', value: `${dashboardWeather.precipitation} mm`, color: pal.accent },
                      { label: 'Humidity', value: `${dashboardWeather.relative_humidity_2m}%`, color: pal.signal },
                      { label: 'Wind Speed', value: `${dashboardWeather.wind_speed_10m} km/h`, color: pal.mid },
                    ].map((item, i) => (
                      <motion.div
                        key={i}
                        variants={cardItem}
                        whileHover={{ y: -3, scale: 1.02 }}
                        style={{ textAlign: 'center', padding: '16px 10px', borderRadius: 12, background: pal.panelAlt, border: `1px solid ${pal.border}` }}
                      >
                        <div className="mono-label" style={{ fontSize: 10, color: pal.mid, marginBottom: 6 }}>{item.label}</div>
                        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 23, fontWeight: 700, color: item.color }}>{item.value}</div>
                      </motion.div>
                    ))}
                  </motion.div>
                ) : (
                  <div style={{ color: pal.mid, padding: 12 }}>Loading weather data...</div>
                )}
              </motion.div>

              <motion.div
                variants={staggerContainer}
                initial="hidden"
                animate="show"
                style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16 }}
              >
                {[
                  { label: 'Total Lakes', value: totalLakes, color: pal.signal, risk: 'All' },
                  { label: 'High Risk', value: highRisk, color: '#f0433a', risk: 'High' },
                  { label: 'Medium Risk', value: mediumRisk, color: '#f5a524', risk: 'Medium' },
                  { label: 'Low Risk', value: lowRisk, color: '#2dd48e', risk: 'Low' },
                ].map(c => (
                  <motion.div
                    key={c.risk}
                    variants={cardItem}
                    onClick={() => setSelectedRisk(c.risk)}
                    className="btn-3d hud-frame"
                    whileHover={{ y: -5, scale: 1.02, boxShadow: `0 10px 28px ${c.color}33` }}
                    whileTap={{ scale: 0.98 }}
                    style={{
                      ...cardStyle,
                      padding: '20px 22px',
                      cursor: 'pointer',
                      border: selectedRisk === c.risk ? `1.5px solid ${c.color}` : cardStyle.border
                    }}
                  >
                    <div className="mono-label" style={{ fontSize: 10.5, color: pal.mid, marginBottom: 8 }}>{c.label}</div>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 32, fontWeight: 700, color: c.color }}>{c.value}</div>
                  </motion.div>
                ))}
              </motion.div>

              <motion.div
                initial={{ opacity: 0, y: 16 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.2, duration: 0.4, ease: easeOut }}
                style={{ ...cardStyle, padding: 24 }}
              >
                <div style={eyebrow}>Geospatial Feed</div>
                <h3 style={{ ...sectionTitle, marginBottom: 16 }}>Interactive Risk Map</h3>
                <MapView
                  selectedRisk={selectedRisk}
                  setSelectedRisk={setSelectedRisk}
                  lakes={lakes}
                  setSelectedLake={openLakeDetails}
                  loading={loading}
                />
              </motion.div>
            </div>
          )}

          {/* ANALYTICS */}
{activeTab === 'analytics' && (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
    {/* KPI Cards */}
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16 }}>
      {[
        { label: 'Total Lakes', value: totalLakes, color: pal.signal },
        { label: 'High Risk', value: highRisk, color: '#f0433a' },
        { label: 'Medium Risk', value: mediumRisk, color: '#f5a524' },
        { label: 'Low Risk', value: lowRisk, color: '#2dd48e' },
      ].map(c => (
        <div key={c.label} style={{ ...cardStyle, padding: '18px 20px' }}>
          <div className="mono-label" style={{ fontSize: 10.5, color: pal.mid, marginBottom: 6 }}>{c.label}</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 28, fontWeight: 700, color: c.color }}>{c.value}</div>
        </div>
      ))}
    </div>

    {/* Pie + Bar */}
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
      <div style={{ ...cardStyle, padding: 24 }}>
        <div style={eyebrow}>Distribution</div>
        <h3 style={{ ...sectionTitle, marginBottom: 16 }}>Risk Distribution</h3>
        {totalLakes === 0 ? (
          <div style={{ height: 260, display: 'flex', alignItems: 'center', justifyContent: 'center', color: pal.mid }}>No data available</div>
        ) : (
          <ResponsiveContainer width="100%" height={260}>
            <PieChart>
              <Pie data={riskPieData} cx="50%" cy="50%" innerRadius={60} outerRadius={95} paddingAngle={3} dataKey="value">
                {riskPieData.map((entry, index) => <Cell key={`cell-${index}`} fill={entry.color} />)}
              </Pie>
              <Tooltip contentStyle={chartTooltipStyle} />
              <Legend verticalAlign="bottom" height={36} formatter={(value) => <span style={{ color: pal.hi, fontSize: 13 }}>{value}</span>} />
            </PieChart>
          </ResponsiveContainer>
        )}
      </div>

      <div style={{ ...cardStyle, padding: 24 }}>
        <div style={eyebrow}>Comparison</div>
        <h3 style={{ ...sectionTitle, marginBottom: 16 }}>Lakes by Risk Level</h3>
        <ResponsiveContainer width="100%" height={260}>
          <BarChart data={riskBarData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={pal.border} />
            <XAxis dataKey="name" tick={{ fill: pal.mid, fontSize: 12 }} />
            <YAxis tick={{ fill: pal.mid, fontSize: 12 }} allowDecimals={false} />
            <Tooltip contentStyle={chartTooltipStyle} />
            <Bar dataKey="count" radius={[6, 6, 0, 0]}>
              {riskBarData.map((entry, index) => <Cell key={`bar-${index}`} fill={entry.fill} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>

    {/* Top 5 Largest Lakes */}
    <div style={{ ...cardStyle, padding: 24 }}>
      <div style={eyebrow}>Ranking</div>
      <h3 style={{ ...sectionTitle, marginBottom: 16 }}>Top 5 Largest Lakes (by Area)</h3>
      {topLakesData.length === 0 ? (
        <div style={{ height: 280, display: 'flex', alignItems: 'center', justifyContent: 'center', color: pal.mid }}>No lakes data</div>
      ) : (
        <ResponsiveContainer width="100%" height={280}>
          <BarChart data={topLakesData} layout="vertical" margin={{ top: 5, right: 30, left: 20, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={pal.border} />
            <XAxis type="number" tick={{ fill: pal.mid, fontSize: 12 }} unit=" ha" />
            <YAxis type="category" dataKey="name" width={110} tick={{ fill: pal.hi, fontSize: 12 }} />
            <Tooltip contentStyle={chartTooltipStyle} formatter={(value) => [`${value} ha`, 'Area']} />
            <Bar dataKey="area" radius={[0, 6, 6, 0]} barSize={22}>
              {topLakesData.map((entry, index) => <Cell key={`top-${index}`} fill={entry.fill} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>

    {/* Population Graph  */}
    <div style={{ ...cardStyle, padding: 24 }}>
      <div style={eyebrow}>Population</div>
      <h3 style={{ ...sectionTitle, marginBottom: 16 }}>Top 5 Lakes by Danger Zone Population</h3>
      {topExposureData.length === 0 ? (
        <div style={{ height: 260, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: pal.mid, gap: 10 }}>
          <div style={{ fontSize: 14 }}>No exposure data yet</div>
          <div style={{ fontSize: 12, color: pal.lo }}>
            Go to the <strong style={{ color: pal.accent }}>Population</strong> tab and click “Calculate Population Exposure”
          </div>
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={280}>
          <BarChart data={topExposureData} layout="vertical" margin={{ top: 5, right: 30, left: 20, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={pal.border} />
            <XAxis type="number" tick={{ fill: pal.mid, fontSize: 12 }} />
            <YAxis type="category" dataKey="name" width={110} tick={{ fill: pal.hi, fontSize: 12 }} />
            <Tooltip
              contentStyle={chartTooltipStyle}
              formatter={(value, name) => [value.toLocaleString(), name === 'danger' ? 'Danger Zone' : 'Warning Zone']}
            />
            <Legend formatter={(value) => (
              <span style={{ color: pal.hi, fontSize: 12 }}>
                {value === 'danger' ? 'Danger Zone' : 'Warning Zone'}
              </span>
            )} />
            <Bar dataKey="danger" name="danger" fill="#f0433a" radius={[0, 6, 6, 0]} barSize={14} />
            <Bar dataKey="warning" name="warning" fill="#f5a524" radius={[0, 6, 6, 0]} barSize={14} />
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  </div>
)}
          {/* POPULATION */}
          {activeTab === 'population' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
                <div style={{ ...cardStyle, padding: '18px 20px' }}>
                  <div className="mono-label" style={{ fontSize: 10.5, color: pal.mid, marginBottom: 6 }}>Lakes Analysed</div>
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 28, fontWeight: 700, color: pal.signal }}>{Object.keys(exposureData).length}</div>
                </div>
                <div style={{ ...cardStyle, padding: '18px 20px' }}>
                  <div className="mono-label" style={{ fontSize: 10.5, color: pal.mid, marginBottom: 6 }}>People in Danger Zones</div>
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 28, fontWeight: 700, color: '#f0433a' }}>{totalDangerPop.toLocaleString()}</div>
                </div>
                <div style={{ ...cardStyle, padding: '18px 20px' }}>
                  <div className="mono-label" style={{ fontSize: 10.5, color: pal.mid, marginBottom: 6 }}>People in Warning Zones</div>
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 28, fontWeight: 700, color: '#f5a524' }}>{totalWarningPop.toLocaleString()}</div>
                </div>
              </div>

              <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                <button
                  onClick={() => fetchExposureForLakes(lakes)}
                  disabled={exposureLoading}
                  className="btn-3d"
                  style={{
                    padding: '10px 18px', borderRadius: 10, border: 'none',
                    background: exposureLoading ? '#94a3b8' : 'linear-gradient(135deg,#f0433a,#f5a524)',
                    color: '#fff', fontWeight: 800, cursor: 'pointer', fontSize: 13
                  }}
                >
                  {exposureLoading ? 'Calculating with WorldPop...' : 'Calculate Population Exposure'}
                </button>
                <span style={{ fontSize: 12, color: pal.mid }}>
                  Uses WorldPop via Google Earth Engine · Limited to High & Medium risk lakes (max 12)
                </span>
              </div>

              <div style={{ ...cardStyle, padding: 24 }}>
                <div style={eyebrow}>Exposure Map</div>
                <h3 style={{ ...sectionTitle, marginBottom: 16 }}>Danger & Warning Zones</h3>
                <MapView
                  selectedRisk={populationRisk}
                  setSelectedRisk={setPopulationRisk}
                  lakes={lakes}
                  setSelectedLake={openLakeDetails}
                  loading={loading}
                  showZones={true}
                />
              </div>
            </div>
          )}

          {/* HISTORICAL */}
         {activeTab === 'historical' && (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
    {/* Selection Card */}
    <div style={{ ...cardStyle, padding: 24 }}>
      <div style={eyebrow}>Time Series</div>
      <h3 style={{ ...sectionTitle, marginBottom: 16 }}>Lake Area History</h3>
      <p style={{ margin: '0 0 16px', color: pal.mid, fontSize: 13 }}>
        Data source: Google Earth Engine · Sentinel-2 NDWI summer composites
      </p>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        <select
          value={histLakeId}
          onChange={(e) => setHistLakeId(e.target.value)}
          style={{
            minWidth: 260,
            padding: '10px 14px',
            borderRadius: 10,
            border: `1px solid ${pal.border}`,
            background: pal.panelAlt,
            color: pal.hi,
            fontSize: 14
          }}
        >
          <option value="">Select a lake...</option>
          {lakes.filter(l => l.latitude && l.longitude).map(l => (
            <option key={l.id} value={l.id}>{l.name} ({l.area_ha} ha)</option>
          ))}
        </select>

        <button
          disabled={!histLakeId || histLoading}
          onClick={() => {
            const lake = lakes.find(l => String(l.id) === String(histLakeId));
            if (lake) fetchHistorical(lake);
          }}
          className="btn-3d"
          style={{
            padding: '10px 18px',
            borderRadius: 10,
            border: 'none',
            background: histLoading ? '#94a3b8' : 'linear-gradient(135deg,#5eead4,#38bdf8)',
            color: '#06131a',
            fontWeight: 800,
            cursor: 'pointer',
            fontSize: 13
          }}
        >
          {histLoading ? 'Loading from GEE (30–90s)...' : 'Load Historical Data'}
        </button>
      </div>
    </div>

    {histData && (
      <>
        {/* KPI Cards */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
          <div style={{ ...cardStyle, padding: '18px 20px' }}>
            <div className="mono-label" style={{ fontSize: 10.5, color: pal.mid, marginBottom: 6 }}>Selected Lake</div>
            <div style={{ fontWeight: 700, color: pal.hi, fontSize: 16 }}>{selectedHistLake?.name || '—'}</div>
          </div>
          <div style={{ ...cardStyle, padding: '18px 20px' }}>
            <div className="mono-label" style={{ fontSize: 10.5, color: pal.mid, marginBottom: 6 }}>Trend</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 22, fontWeight: 700, color: trendColor, textTransform: 'capitalize' }}>
              {histData.trend}
            </div>
          </div>
          <div style={{ ...cardStyle, padding: '18px 20px' }}>
            <div className="mono-label" style={{ fontSize: 10.5, color: pal.mid, marginBottom: 6 }}>Years Analysed</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 28, fontWeight: 700, color: pal.signal }}>
              {histChartData.length}
            </div>
          </div>
        </div>

        {/* Line Chart */}
        <div style={{ ...cardStyle, padding: 24 }}>
          <div style={eyebrow}>Area Change</div>
          <h3 style={{ ...sectionTitle, marginBottom: 16 }}>Surface Area Over Time (ha)</h3>
          {histChartData.length === 0 ? (
            <div style={{ height: 280, display: 'flex', alignItems: 'center', justifyContent: 'center', color: pal.mid }}>
              No valid year data
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={histChartData} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={pal.border} />
                <XAxis dataKey="year" tick={{ fill: pal.mid, fontSize: 12 }} />
                <YAxis tick={{ fill: pal.mid, fontSize: 12 }} unit=" ha" />
                <Tooltip contentStyle={chartTooltipStyle} formatter={(v) => [`${v} ha`, 'Area']} />
                <Line
                  type="monotone"
                  dataKey="area"
                  stroke="#38bdf8"
                  strokeWidth={3}
                  dot={{ r: 5, fill: '#5eead4' }}
                  activeDot={{ r: 7 }}
                />
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>

        {/* Satellite Images */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
          <div style={{ ...cardStyle, padding: 20 }}>
            <div style={eyebrow}>Live Satellite</div>
            <h3 style={{ ...sectionTitle, marginBottom: 12 }}>NDWI / Water Highlight</h3>
            {thumbLoading ? (
              <div style={{ height: 280, display: 'flex', alignItems: 'center', justifyContent: 'center', color: pal.mid }}>
                Loading image...
              </div>
            ) : thumbNdwi ? (
              <img src={thumbNdwi} alt="NDWI" style={{ width: '100%', borderRadius: 12, border: `1px solid ${pal.border}` }} />
            ) : (
              <div style={{ height: 280, display: 'flex', alignItems: 'center', justifyContent: 'center', color: pal.lo }}>
                Image not available
              </div>
            )}
          </div>

          <div style={{ ...cardStyle, padding: 20 }}>
            <div style={eyebrow}>Live Satellite</div>
            <h3 style={{ ...sectionTitle, marginBottom: 12 }}>True Color (RGB)</h3>
            {thumbLoading ? (
              <div style={{ height: 280, display: 'flex', alignItems: 'center', justifyContent: 'center', color: pal.mid }}>
                Loading image...
              </div>
            ) : thumbRgb ? (
              <img src={thumbRgb} alt="RGB" style={{ width: '100%', borderRadius: 12, border: `1px solid ${pal.border}` }} />
            ) : (
              <div style={{ height: 280, display: 'flex', alignItems: 'center', justifyContent: 'center', color: pal.lo }}>
                Image not available
              </div>
            )}
          </div>
        </div>

        {/* Year-by-Year Table (this was the missing part) */}
        <div style={{ ...cardStyle, padding: 24 }}>
          <div style={eyebrow}>Raw Series</div>
          <h3 style={{ ...sectionTitle, marginBottom: 16 }}>Year-by-Year Area</h3>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: pal.panelAlt }}>
                {['Year', 'Area (ha)', 'Source'].map(h => (
                  <th key={h} className="mono-label" style={{ padding: '12px 14px', fontSize: 10.5, color: pal.mid, textAlign: 'left' }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(histData.years || []).map((y) => (
                <tr key={y.year} style={{ borderBottom: `1px solid ${pal.border}` }}>
                  <td style={{ padding: '12px 14px', fontFamily: 'var(--font-mono)', color: pal.hi }}>
                    {y.year}
                  </td>
                  <td style={{ padding: '12px 14px', fontFamily: 'var(--font-mono)', fontWeight: 700, color: pal.signal }}>
                    {y.area_ha != null ? y.area_ha : '—'}
                  </td>
                  <td style={{ padding: '12px 14px', color: pal.mid, fontSize: 13 }}>
                    {y.source || histData.source}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </>
    )}

    {!histData && !histLoading && (
      <div style={{ ...cardStyle, padding: 40, textAlign: 'center', color: pal.mid }}>
        Select a lake and click <strong style={{ color: pal.accent }}>Load Historical Data</strong>
      </div>
    )}
  </div>
)}
          {/* LAKES INVENTORY */}
          {activeTab === 'lakes' && (
            <div style={{ ...cardStyle, padding: 24 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20, flexWrap: 'wrap', gap: 12 }}>
                <div>
                  <div style={eyebrow}>Registry</div>
                  <h3 style={sectionTitle}>{showUnknownOnly ? `Unknown Lakes (${unknownCount})` : 'Lakes Inventory'}</h3>
                </div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                  <button
                    onClick={() => setShowUnknownOnly(!showUnknownOnly)}
                    className="btn-3d"
                    style={{
                      padding: '8px 14px', borderRadius: 10,
                      border: showUnknownOnly ? '1px solid #f5a524' : `1px solid ${pal.border}`,
                      background: showUnknownOnly ? (isDark ? 'rgba(245,165,36,0.18)' : '#fff7ed') : (isDark ? 'rgba(245,165,36,0.08)' : '#fffbeb'),
                      color: '#f5a524', fontWeight: 700, fontSize: 13, cursor: 'pointer'
                    }}
                  >
                    {showUnknownOnly ? 'Show All Lakes' : `Unknown Lakes (${unknownCount})`}
                  </button>
                  <button
                    onClick={handleMatchNames}
                    className="btn-3d"
                    style={{
                      padding: '8px 14px', borderRadius: 10, border: '1px solid #99f6e4',
                      background: isDark ? 'rgba(45,212,142,0.1)' : '#f0fdf4',
                      color: '#16a34a', fontWeight: 700, fontSize: 13, cursor: 'pointer'
                    }}
                  >
                    Auto-Match Names
                  </button>
                  <button onClick={exportToCSV} className="btn-3d" style={{ padding: '8px 14px', borderRadius: 10, border: `1px solid ${pal.border}`, background: isDark ? 'rgba(56,189,248,0.1)' : '#f0f9ff', color: pal.signal, fontWeight: 700, fontSize: 13, cursor: 'pointer' }}>
                    CSV
                  </button>
                  <button onClick={exportToPDF} className="btn-3d" style={{ padding: '8px 14px', borderRadius: 10, border: '1px solid rgba(240,67,58,0.3)', background: isDark ? 'rgba(240,67,58,0.1)' : '#fef2f2', color: '#f0433a', fontWeight: 700, fontSize: 13, cursor: 'pointer' }}>
                    PDF
                  </button>
                </div>
              </div>

              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ background: pal.panelAlt }}>
                      {['#', 'Name', 'Area', 'Risk', 'Lat', 'Lon', 'Actions'].map(h => (
                        <th key={h} className="mono-label" style={{ padding: '12px 14px', fontSize: 10.5, color: pal.mid, textAlign: 'left' }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {filteredLakes.length === 0 ? (
                      <tr>
                        <td colSpan={7} style={{ padding: 40, textAlign: 'center', color: pal.mid }}>
                          {showUnknownOnly ? 'No unknown lakes found' : 'No lakes match the current filter'}
                        </td>
                      </tr>
                    ) : (
                      filteredLakes.map((lake, i) => {
                        const risk = calculateRisk(lake.area_ha);
                        const rc = RISK_COLOR[risk];
                        const unknown = isUnknownName(lake.name);
                        return (
                          <motion.tr
                            key={lake.id}
                            initial={{ opacity: 0, x: -8 }}
                            animate={{ opacity: 1, x: 0 }}
                            transition={{ delay: Math.min(i * 0.02, 0.4), duration: 0.28, ease: easeOut }}
                            whileHover={{ backgroundColor: isDark ? 'rgba(56,189,248,0.06)' : 'rgba(2,132,199,0.04)' }}
                            style={{ borderBottom: `1px solid ${pal.border}` }}
                          >
                            <td style={{ padding: '12px 14px', fontFamily: 'var(--font-mono)', color: pal.mid }}>{String(i + 1).padStart(2, '0')}</td>
                            <td style={{ padding: '12px 14px', fontWeight: 700, color: unknown ? '#f5a524' : pal.hi }}>{lake.name}</td>
                            <td style={{ padding: '12px 14px', fontFamily: 'var(--font-mono)', color: pal.mid }}>{lake.area_ha} ha</td>
                            <td style={{ padding: '12px 14px' }}>
                              <span style={{
                                display: 'inline-flex', alignItems: 'center', gap: 6,
                                padding: '3px 10px', borderRadius: 20, fontSize: 11.5, fontWeight: 700,
                                background: `${rc}18`, color: rc
                              }}>
                                <span style={{ width: 6, height: 6, borderRadius: '50%', background: rc }} />
                                {risk}
                              </span>
                            </td>
                            <td style={{ padding: '12px 14px', fontFamily: 'var(--font-mono)', fontSize: 12.5, color: pal.mid }}>{lake.latitude ?? '—'}</td>
                            <td style={{ padding: '12px 14px', fontFamily: 'var(--font-mono)', fontSize: 12.5, color: pal.mid }}>{lake.longitude ?? '—'}</td>
                            <td style={{ padding: '12px 14px' }}>
                              <motion.button whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.96 }} onClick={() => openLakeDetails(lake)} style={{ marginRight: 6, padding: '5px 10px', borderRadius: 8, border: `1px solid ${pal.border}`, background: isDark ? 'rgba(56,189,248,0.1)' : '#f0f9ff', color: pal.signal, cursor: 'pointer', fontSize: 12, fontWeight: 600 }}>
                                Details
                              </motion.button>
                              <motion.button whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.96 }} onClick={() => openRenameModal(lake)} style={{ marginRight: 6, padding: '5px 10px', borderRadius: 8, border: `1px solid ${pal.border}`, background: pal.panelAlt, color: pal.mid, cursor: 'pointer', fontSize: 12 }}>
                                Rename
                              </motion.button>
                              <motion.button whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.96 }} onClick={() => handleDelete(lake.id, lake.name)} style={{ padding: '5px 10px', borderRadius: 8, border: '1px solid rgba(240,67,58,0.3)', background: isDark ? 'rgba(240,67,58,0.1)' : '#fef2f2', color: '#f0433a', cursor: 'pointer', fontSize: 12 }}>
                                Delete
                              </motion.button>
                            </td>
                          </motion.tr>
                        );
                      })
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* REGISTER NEW LAKE */}
          {activeTab === 'add' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 24, maxWidth: 1100, margin: '0 auto' }}>
              <div style={{ ...cardStyle, padding: 32 }}>
                <div style={eyebrow}>Field Registration</div>
                <h3 style={{ ...sectionTitle, marginBottom: 6 }}>Register New Lake</h3>
                <p style={{ margin: '0 0 24px', color: pal.mid, fontSize: 13.5 }}>
                  Type a lake name → click <strong>Find Lake</strong> → coordinates & satellite images will fill automatically.
                </p>

                <form onSubmit={handleSubmit} style={{ display: 'grid', gap: 16 }}>
                  <div style={{ position: 'relative' }}>
                    <label style={{ display: 'block', marginBottom: 6, fontSize: 13, fontWeight: 600, color: pal.mid }}>
                      Lake Name
                    </label>
                    <div style={{ display: 'flex', gap: 10 }}>
                      <input
                        value={formData.name}
                        onChange={handleNameChange}
                        onFocus={() => formData.name.length >= 2 && setShowSuggestions(true)}
                        required
                        placeholder="e.g. Attabad Lake, Shisper, Ghamu Bar..."
                        className="input-3d"
                        style={{
                          flex: 1, padding: '12px 14px', borderRadius: 10,
                          border: `1px solid ${pal.border}`, background: pal.panelAlt, color: pal.hi, fontSize: 15
                        }}
                      />
                      <button
                        type="button"
                        onClick={handleFindLake}
                        className="btn-3d"
                        style={{
                          padding: '0 20px', borderRadius: 10, border: 'none',
                          background: 'linear-gradient(135deg,#5eead4,#38bdf8)',
                          color: '#06131a', fontWeight: 800, fontSize: 14, whiteSpace: 'nowrap', cursor: 'pointer'
                        }}
                      >
                        Find Lake
                      </button>
                    </div>

                    {showSuggestions && suggestions.length > 0 && (
                      <div style={{
                        position: 'absolute', top: '100%', left: 0, right: 80, zIndex: 50,
                        background: pal.panel, border: `1px solid ${pal.border}`, borderRadius: 12,
                        marginTop: 6, boxShadow: '0 12px 32px rgba(0,0,0,0.25)', maxHeight: 260, overflowY: 'auto'
                      }}>
                        {suggestions.map((lake, idx) => (
                          <div
                            key={idx}
                            onClick={() => applySuggestion(lake)}
                            style={{
                              padding: '11px 16px', cursor: 'pointer', borderBottom: `1px solid ${pal.border}`,
                              display: 'flex', justifyContent: 'space-between', alignItems: 'center'
                            }}
                            onMouseEnter={e => e.currentTarget.style.background = isDark ? 'rgba(94,234,212,0.08)' : '#f0fdfa'}
                            onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                          >
                            <span style={{ fontWeight: 600, color: pal.hi }}>{lake.name}</span>
                            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: pal.mid }}>{lake.area_ha} ha</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
                    <div>
                      <label style={{ display: 'block', marginBottom: 6, fontSize: 13, fontWeight: 600, color: pal.mid }}>Area (ha)</label>
                      <input
                        type="number"
                        value={formData.area_ha}
                        onChange={e => setFormData({ ...formData, area_ha: e.target.value })}
                        step="0.1"
                        placeholder="Auto-filled"
                        className="input-3d"
                        style={{ width: '100%', padding: 12, borderRadius: 10, border: `1px solid ${pal.border}`, background: pal.panelAlt, color: pal.hi }}
                      />
                    </div>
                    <div>
                      <label style={{ display: 'block', marginBottom: 6, fontSize: 13, fontWeight: 600, color: pal.mid }}>Latitude</label>
                      <input
                        type="number"
                        value={formData.latitude}
                        onChange={e => setFormData({ ...formData, latitude: e.target.value })}
                        step="any"
                        placeholder="Auto-filled"
                        className="input-3d"
                        style={{ width: '100%', padding: 12, borderRadius: 10, border: `1px solid ${pal.border}`, background: pal.panelAlt, color: pal.hi }}
                      />
                    </div>
                    <div>
                      <label style={{ display: 'block', marginBottom: 6, fontSize: 13, fontWeight: 600, color: pal.mid }}>Longitude</label>
                      <input
                        type="number"
                        value={formData.longitude}
                        onChange={e => setFormData({ ...formData, longitude: e.target.value })}
                        step="any"
                        placeholder="Auto-filled"
                        className="input-3d"
                        style={{ width: '100%', padding: 12, borderRadius: 10, border: `1px solid ${pal.border}`, background: pal.panelAlt, color: pal.hi }}
                      />
                    </div>
                  </div>

                  <button
                    type="submit"
                    disabled={submitting}
                    className="btn-3d"
                    style={{
                      padding: 14, borderRadius: 12, border: 'none',
                      background: submitting ? '#94a3b8' : 'linear-gradient(135deg,#5eead4,#38bdf8)',
                      color: '#06131a', fontWeight: 800, cursor: 'pointer', fontSize: 15
                    }}
                  >
                    {submitting ? 'Registering...' : 'Register Lake'}
                  </button>

                  {message && (
                    <div style={{ color: message.includes('success') ? '#2dd48e' : '#f0433a', fontWeight: 700 }}>
                      {message}
                    </div>
                  )}
                </form>
              </div>

              {(formData.latitude && formData.longitude) && (
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
                  <div style={{ ...cardStyle, padding: 20 }}>
                    <div style={eyebrow}>Live Satellite</div>
                    <h3 style={{ ...sectionTitle, marginBottom: 12 }}>NDWI / Water Highlight</h3>
                    {regThumbLoading ? (
                      <div style={{ height: 280, display: 'flex', alignItems: 'center', justifyContent: 'center', color: pal.mid }}>Loading NDWI image...</div>
                    ) : regThumbNdwi ? (
                      <img src={regThumbNdwi} alt="NDWI" style={{ width: '100%', borderRadius: 12, border: `1px solid ${pal.border}` }} />
                    ) : (
                      <div style={{ height: 280, display: 'flex', alignItems: 'center', justifyContent: 'center', color: pal.lo }}>Click Find Lake to load image</div>
                    )}
                  </div>
                  <div style={{ ...cardStyle, padding: 20 }}>
                    <div style={eyebrow}>Live Satellite</div>
                    <h3 style={{ ...sectionTitle, marginBottom: 12 }}>True Color (RGB)</h3>
                    {regThumbLoading ? (
                      <div style={{ height: 280, display: 'flex', alignItems: 'center', justifyContent: 'center', color: pal.mid }}>Loading RGB image...</div>
                    ) : regThumbRgb ? (
                      <img src={regThumbRgb} alt="RGB" style={{ width: '100%', borderRadius: 12, border: `1px solid ${pal.border}` }} />
                    ) : (
                      <div style={{ height: 280, display: 'flex', alignItems: 'center', justifyContent: 'center', color: pal.lo }}>Click Find Lake to load image</div>
                    )}
                  </div>
                </div>
              )}

              {foundLakeForMap && (
                <div style={{ ...cardStyle, padding: 20 }}>
                  <div style={eyebrow}>Location Preview</div>
                  <h3 style={{ ...sectionTitle, marginBottom: 12 }}>{foundLakeForMap.name}</h3>
                  <div style={{ height: 320, borderRadius: 12, overflow: 'hidden', border: `1px solid ${pal.border}` }}>
                    <MapView
                      selectedRisk="All"
                      setSelectedRisk={() => {}}
                      lakes={[{
                        id: 9999,
                        name: foundLakeForMap.name,
                        area_ha: foundLakeForMap.area_ha,
                        latitude: foundLakeForMap.latitude,
                        longitude: foundLakeForMap.longitude,
                        risk_level: calculateRisk(foundLakeForMap.area_ha)
                      }]}
                      setSelectedLake={() => {}}
                      loading={false}
                    />
                  </div>
                </div>
              )}
            </div>
          )}

          {/* FLOOD MONITORING / EARLY WARNING */}
          {activeTab === 'flood' && (() => {
            const LEVEL_COLOR = {
              Critical: '#f0433a',
              Warning: '#f5a524',
              Watch: '#38bdf8',
              Normal: '#2dd48e',
            };
            const counts = ewData?.counts || { Critical: 0, Warning: 0, Watch: 0, Normal: 0 };
            const board = (ewData?.lakes || []).filter(
              (row) => ewFilter === 'All' || row?.early_warning?.level === ewFilter
            );
            const selected = ewSelected || board[0] || null;
            const ew = selected?.early_warning;

            return (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
                <div style={{ ...cardStyle, padding: 24 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16, flexWrap: 'wrap' }}>
                    <div>
                      <div style={eyebrow}>GLOF Early Warning Score</div>
                      <h3 style={{ ...sectionTitle, marginBottom: 8 }}>Flood Monitoring Board</h3>
                      <p style={{ margin: 0, color: pal.mid, fontSize: 13.5, maxWidth: 720, lineHeight: 1.55 }}>
                        Composite score from <strong>lake area</strong>, <strong>3–5 year growth</strong>,{' '}
                        <strong>elevation</strong>, <strong>glacier proximity</strong>, and{' '}
                        <strong>downstream population</strong>. Lakes are classed as{' '}
                        <span style={{ color: '#2dd48e', fontWeight: 700 }}>Normal</span>,{' '}
                        <span style={{ color: '#38bdf8', fontWeight: 700 }}>Watch</span>,{' '}
                        <span style={{ color: '#f5a524', fontWeight: 700 }}>Warning</span>, or{' '}
                        <span style={{ color: '#f0433a', fontWeight: 700 }}>Critical</span>.
                      </p>
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 10, alignItems: 'flex-end' }}>
                      <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: pal.mid, cursor: 'pointer' }}>
                        <input
                          type="checkbox"
                          checked={ewUseGee}
                          onChange={(e) => setEwUseGee(e.target.checked)}
                        />
                        Include GEE growth + population (slower)
                      </label>
                      <motion.button
                        className="btn-3d"
                        whileHover={{ scale: 1.03 }}
                        whileTap={{ scale: 0.97 }}
                        disabled={ewLoading}
                        onClick={() => fetchEarlyWarningBoard(ewUseGee)}
                        style={{
                          padding: '11px 18px', borderRadius: 12, border: 'none',
                          background: ewLoading ? '#94a3b8' : 'linear-gradient(135deg,#5eead4,#38bdf8)',
                          color: '#06131a', fontWeight: 800, fontSize: 13, cursor: 'pointer'
                        }}
                      >
                        {ewLoading ? 'Scoring lakes…' : 'Run Early Warning Scan'}
                      </motion.button>
                    </div>
                  </div>
                </div>

                {/* Level KPI cards */}
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12 }}>
                  {['Critical', 'Warning', 'Watch', 'Normal'].map((lvl) => (
                    <motion.div
                      key={lvl}
                      whileHover={{ y: -3 }}
                      onClick={() => setEwFilter(ewFilter === lvl ? 'All' : lvl)}
                      style={{
                        ...cardStyle,
                        padding: '16px 18px',
                        cursor: 'pointer',
                        border: ewFilter === lvl ? `1.5px solid ${LEVEL_COLOR[lvl]}` : cardStyle.border,
                      }}
                    >
                      <div className="mono-label" style={{ fontSize: 10.5, color: pal.mid, marginBottom: 6 }}>{lvl}</div>
                      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 28, fontWeight: 700, color: LEVEL_COLOR[lvl] }}>
                        {counts[lvl] ?? 0}
                      </div>
                    </motion.div>
                  ))}
                </div>

                {!ewData && !ewLoading && (
                  <div style={{ ...cardStyle, padding: 48, textAlign: 'center', color: pal.mid }}>
                    Click <strong style={{ color: pal.accent }}>Run Early Warning Scan</strong> to score all registered lakes.
                    <div style={{ marginTop: 10, fontSize: 12.5 }}>
                      Fast mode always includes elevation, glacier proximity, temperature, flood impact & prediction.
                      Enable GEE for satellite growth + WorldPop population.
                    </div>
                  </div>
                )}

                {ewLoading && (
                  <div style={{ ...cardStyle, padding: 48, textAlign: 'center', color: pal.mid }}>
                    Computing Early Warning Scores{ewUseGee ? ' with GEE (may take several minutes)…' : '…'}
                  </div>
                )}

                {ewData && !ewLoading && (() => {
                  const PRED_COLOR = { High: '#f0433a', Likely: '#f5a524', Possible: '#38bdf8', Unlikely: '#2dd48e' };
                  const predCounts = ewData.prediction_counts || {};
                  const impact = selected?.flood_impact;
                  const pred = selected?.flood_prediction;
                  const temp = selected?.temperature;
                  return (
                  <>
                  {/* Prediction KPI strip */}
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: 12 }}>
                    {['High', 'Likely', 'Possible', 'Unlikely'].map((lvl) => (
                      <div key={lvl} style={{ ...cardStyle, padding: '14px 16px' }}>
                        <div className="mono-label" style={{ fontSize: 10, color: pal.mid, marginBottom: 4 }}>Flood pred. · {lvl}</div>
                        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 24, fontWeight: 700, color: PRED_COLOR[lvl] }}>
                          {predCounts[lvl] ?? 0}
                        </div>
                      </div>
                    ))}
                  </div>

                  <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1.15fr) minmax(300px, 0.85fr)', gap: 20 }}>
                    {/* Table */}
                    <div style={{ ...cardStyle, padding: 20, overflow: 'hidden' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14, flexWrap: 'wrap', gap: 8 }}>
                        <div>
                          <div style={eyebrow}>Priority Queue</div>
                          <h3 style={{ ...sectionTitle, fontSize: 16 }}>
                            {ewFilter === 'All' ? `All lakes (${board.length})` : `${ewFilter} (${board.length})`}
                          </h3>
                        </div>
                        <button
                          onClick={() => setEwFilter('All')}
                          style={{
                            padding: '6px 12px', borderRadius: 8, border: `1px solid ${pal.border}`,
                            background: pal.panelAlt, color: pal.mid, cursor: 'pointer', fontSize: 12, fontWeight: 600
                          }}
                        >
                          Clear filter
                        </button>
                      </div>
                      <div style={{ overflowX: 'auto', maxHeight: 520, overflowY: 'auto' }}>
                        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                          <thead>
                            <tr style={{ background: pal.panelAlt }}>
                              {['#', 'Lake', 'Score', 'Level', 'Flood pred.', 'People', 'Impact area', 'Temp'].map((h) => (
                                <th key={h} className="mono-label" style={{ padding: '10px 12px', fontSize: 10.5, color: pal.mid, textAlign: 'left' }}>{h}</th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {board.length === 0 ? (
                              <tr>
                                <td colSpan={8} style={{ padding: 28, textAlign: 'center', color: pal.mid }}>No lakes in this class</td>
                              </tr>
                            ) : board.map((row, i) => {
                              const level = row.early_warning?.level || 'Normal';
                              const color = LEVEL_COLOR[level] || pal.mid;
                              const pl = row.flood_prediction?.likelihood || '—';
                              const pc = PRED_COLOR[pl] || pal.mid;
                              const active = selected?.lake_id === row.lake_id;
                              const people = row.flood_impact?.warning_population ?? row.flood_impact?.danger_population;
                              const impactKm2 = row.flood_impact?.footprint?.warning_area_km2;
                              const t = row.temperature?.temperature_c;
                              return (
                                <tr
                                  key={row.lake_id || i}
                                  onClick={() => setEwSelected(row)}
                                  style={{
                                    borderBottom: `1px solid ${pal.border}`,
                                    cursor: 'pointer',
                                    background: active ? (isDark ? 'rgba(56,189,248,0.1)' : 'rgba(2,132,199,0.06)') : 'transparent',
                                  }}
                                >
                                  <td style={{ padding: '10px 12px', fontFamily: 'var(--font-mono)', color: pal.mid }}>{String(i + 1).padStart(2, '0')}</td>
                                  <td style={{ padding: '10px 12px', fontWeight: 700, color: pal.hi }}>{row.name}</td>
                                  <td style={{ padding: '10px 12px', fontFamily: 'var(--font-mono)', fontWeight: 700, color }}>
                                    {row.early_warning?.score ?? '—'}
                                  </td>
                                  <td style={{ padding: '10px 12px' }}>
                                    <span style={{
                                      display: 'inline-flex', alignItems: 'center', gap: 6,
                                      padding: '3px 10px', borderRadius: 20, fontSize: 11.5, fontWeight: 700,
                                      background: `${color}18`, color
                                    }}>
                                      <span style={{ width: 6, height: 6, borderRadius: '50%', background: color }} />
                                      {level}
                                    </span>
                                  </td>
                                  <td style={{ padding: '10px 12px' }}>
                                    <span style={{ fontWeight: 700, fontSize: 12, color: pc }}>{pl}</span>
                                  </td>
                                  <td style={{ padding: '10px 12px', fontFamily: 'var(--font-mono)', color: pal.mid, fontSize: 12.5 }}>
                                    {people != null ? Number(people).toLocaleString() : '—'}
                                  </td>
                                  <td style={{ padding: '10px 12px', fontFamily: 'var(--font-mono)', color: pal.mid, fontSize: 12.5 }}>
                                    {impactKm2 != null ? `${impactKm2} km²` : '—'}
                                  </td>
                                  <td style={{ padding: '10px 12px', fontFamily: 'var(--font-mono)', color: pal.mid, fontSize: 12.5 }}>
                                    {t != null ? `${t}°C` : '—'}
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    </div>

                    {/* Detail panel */}
                    <div style={{ ...cardStyle, padding: 20, maxHeight: 640, overflowY: 'auto' }}>
                      {!selected ? (
                        <div style={{ color: pal.mid, padding: 24, textAlign: 'center' }}>Select a lake for impact & prediction</div>
                      ) : (
                        <>
                          <div style={eyebrow}>Lake Profile</div>
                          <h3 style={{ ...sectionTitle, marginBottom: 4 }}>{selected.name}</h3>
                          <div style={{ fontSize: 12.5, color: pal.mid, marginBottom: 10 }}>
                            {selected.basin?.name || 'Basin unassigned'} · {selected.basin?.river || ''}
                          </div>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
                            <span style={{
                              fontFamily: 'var(--font-mono)', fontSize: 28, fontWeight: 800,
                              color: LEVEL_COLOR[ew?.level] || pal.signal
                            }}>
                              {ew?.score ?? '—'}
                            </span>
                            <span style={{
                              padding: '4px 12px', borderRadius: 20, fontWeight: 800, fontSize: 12,
                              background: `${LEVEL_COLOR[ew?.level] || pal.mid}22`,
                              color: LEVEL_COLOR[ew?.level] || pal.mid
                            }}>
                              {ew?.level || '—'}
                            </span>
                            {pred && (
                              <span style={{
                                padding: '4px 12px', borderRadius: 20, fontWeight: 800, fontSize: 12,
                                background: `${PRED_COLOR[pred.likelihood] || pal.mid}22`,
                                color: PRED_COLOR[pred.likelihood] || pal.mid
                              }}>
                                Flood: {pred.likelihood}
                              </span>
                            )}
                          </div>
                          <p style={{ margin: '0 0 14px', fontSize: 13.5, color: pal.mid, lineHeight: 1.5 }}>
                            {ew?.advice}
                          </p>

                          {/* Flood impact card */}
                          <div style={{
                            marginBottom: 14, padding: 14, borderRadius: 12,
                            border: `1px solid ${pal.border}`, background: pal.panelAlt
                          }}>
                            <div style={eyebrow}>If this lake floods — impact</div>
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 8 }}>
                              <div>
                                <div className="mono-label" style={{ fontSize: 10, color: pal.mid }}>People (danger zone)</div>
                                <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 800, color: '#f0433a', fontSize: 18 }}>
                                  {(impact?.danger_population ?? 0).toLocaleString()}
                                </div>
                              </div>
                              <div>
                                <div className="mono-label" style={{ fontSize: 10, color: pal.mid }}>People (warning zone)</div>
                                <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 800, color: '#f5a524', fontSize: 18 }}>
                                  {(impact?.warning_population ?? 0).toLocaleString()}
                                </div>
                              </div>
                              <div>
                                <div className="mono-label" style={{ fontSize: 10, color: pal.mid }}>Danger area</div>
                                <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color: pal.hi }}>
                                  {impact?.footprint?.danger_area_km2 ?? '—'} km²
                                </div>
                                <div style={{ fontSize: 11, color: pal.lo }}>r ≈ {impact?.footprint?.danger_radius_km ?? '—'} km</div>
                              </div>
                              <div>
                                <div className="mono-label" style={{ fontSize: 10, color: pal.mid }}>Warning area</div>
                                <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color: pal.hi }}>
                                  {impact?.footprint?.warning_area_km2 ?? '—'} km²
                                </div>
                                <div style={{ fontSize: 11, color: pal.lo }}>r ≈ {impact?.footprint?.warning_radius_km ?? '—'} km</div>
                              </div>
                              <div>
                                <div className="mono-label" style={{ fontSize: 10, color: pal.mid }}>Est. water volume</div>
                                <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color: pal.signal }}>
                                  {impact?.estimated_volume_million_m3 != null
                                    ? `${impact.estimated_volume_million_m3} M m³`
                                    : '—'}
                                </div>
                              </div>
                              <div>
                                <div className="mono-label" style={{ fontSize: 10, color: pal.mid }}>Temperature</div>
                                <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color: pal.hi }}>
                                  {temp?.temperature_c != null ? `${temp.temperature_c}°C` : '—'}
                                </div>
                                <div style={{ fontSize: 11, color: pal.lo }}>
                                  max 3d: {temp?.forecast_max_c != null ? `${temp.forecast_max_c}°C` : '—'}
                                </div>
                              </div>
                            </div>
                            {impact?.at_risk_settlements?.length > 0 && (
                              <div style={{ marginTop: 10, fontSize: 12, color: pal.mid }}>
                                <strong style={{ color: pal.hi }}>Settlements:</strong> {impact.at_risk_settlements.join(', ')}
                              </div>
                            )}
                            <div style={{ marginTop: 8, fontSize: 11, color: pal.lo }}>{impact?.notes}</div>
                          </div>

                          {/* Prediction */}
                          {pred && (
                            <div style={{
                              marginBottom: 14, padding: 14, borderRadius: 12,
                              border: `1px solid ${pal.border}`, borderLeft: `4px solid ${PRED_COLOR[pred.likelihood] || pal.mid}`,
                              background: pal.panelAlt
                            }}>
                              <div style={eyebrow}>Flood prediction</div>
                              <div style={{ fontWeight: 800, color: PRED_COLOR[pred.likelihood], marginTop: 4 }}>
                                {pred.likelihood} · score {pred.score}
                              </div>
                              <p style={{ margin: '6px 0 8px', fontSize: 13, color: pal.mid }}>{pred.advice}</p>
                              <div style={{ fontSize: 11.5, color: pal.lo }}>
                                Uses historical growth (if GEE), temperature / melt stress, glacier proximity, lake size, and early-warning score.
                              </div>
                            </div>
                          )}

                          <div style={eyebrow}>Indicator Breakdown</div>
                          <div style={{ display: 'flex', flexDirection: 'column', gap: 12, marginTop: 8 }}>
                            {(ew?.indicators || []).map((ind) => {
                              const pct = Math.min(100, (Number(ind.points) / Number(ind.max || 1)) * 100);
                              return (
                                <div key={ind.id}>
                                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4, gap: 8 }}>
                                    <span style={{ fontSize: 12.5, fontWeight: 600, color: pal.hi }}>{ind.label}</span>
                                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: pal.signal }}>
                                      {ind.points}/{ind.max}
                                    </span>
                                  </div>
                                  <div style={{ height: 8, borderRadius: 99, background: isDark ? '#0a1520' : '#e8eef3', overflow: 'hidden', border: `1px solid ${pal.border}` }}>
                                    <div style={{
                                      width: `${pct}%`, height: '100%', borderRadius: 99,
                                      background: 'linear-gradient(90deg,#5eead4,#38bdf8)'
                                    }} />
                                  </div>
                                  <div style={{ fontSize: 11.5, color: pal.lo, marginTop: 4 }}>{ind.detail}</div>
                                </div>
                              );
                            })}
                          </div>

                          <div style={{ marginTop: 18, paddingTop: 14, borderTop: `1px solid ${pal.border}` }}>
                            <div style={eyebrow}>Data Sources</div>
                            <div style={{ fontSize: 12, color: pal.mid, lineHeight: 1.6, marginTop: 6 }}>
                              <div>Elevation: {selected.data_sources?.elevation || '—'}</div>
                              <div>Growth: {selected.data_sources?.growth || '—'}</div>
                              <div>Population: {selected.data_sources?.population || '—'}</div>
                              <div>Temperature: {selected.data_sources?.temperature || '—'}</div>
                              <div>GEE ready: {selected.data_sources?.gee_ready ? 'yes' : 'no'}</div>
                            </div>
                          </div>

                          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 14 }}>
                            {selected.latitude && selected.longitude && (
                              <motion.button
                                className="btn-3d"
                                whileHover={{ scale: 1.02 }}
                                whileTap={{ scale: 0.98 }}
                                onClick={() => {
                                  setHistLakeId(String(selected.lake_id));
                                  setActiveTab('historical');
                                  const lake = lakes.find((l) => l.id === selected.lake_id);
                                  if (lake) fetchHistorical(lake);
                                }}
                                style={{
                                  width: '100%', padding: '10px 14px', borderRadius: 10, border: 'none',
                                  background: 'linear-gradient(135deg,#5eead4,#38bdf8)', color: '#06131a',
                                  fontWeight: 800, fontSize: 12, cursor: 'pointer'
                                }}
                              >
                                Open Historical Analysis
                              </motion.button>
                            )}
                            {selected.basin?.id && (
                              <button
                                onClick={() => {
                                  setSelectedBasinId(selected.basin.id);
                                  setActiveTab('basins');
                                  if (!basinsData) fetchBasins();
                                }}
                                style={{
                                  width: '100%', padding: '10px 14px', borderRadius: 10,
                                  border: `1px solid ${pal.border}`, background: pal.panelAlt,
                                  color: pal.signal, fontWeight: 700, fontSize: 12, cursor: 'pointer'
                                }}
                              >
                                View GLOF Basin
                              </button>
                            )}
                          </div>
                        </>
                      )}
                    </div>
                  </div>
                  </>
                  );
                })()}

                {/* Legend */}
                <div style={{ ...cardStyle, padding: 20 }}>
                  <div style={eyebrow}>Score Guide</div>
                  <h3 style={{ ...sectionTitle, marginBottom: 12, fontSize: 15 }}>Alert Classification</h3>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12 }}>
                    {[
                      { level: 'Normal', range: '0–24', color: '#2dd48e', text: 'Baseline — standard surveillance' },
                      { level: 'Watch', range: '25–49', color: '#38bdf8', text: 'Developing signals — enhanced watch' },
                      { level: 'Warning', range: '50–74', color: '#f5a524', text: 'Elevated risk — intensify monitoring' },
                      { level: 'Critical', range: '75–100', color: '#f0433a', text: 'High GLOF potential — immediate action' },
                    ].map((item) => (
                      <div key={item.level} style={{
                        padding: 14, borderRadius: 12, border: `1px solid ${pal.border}`,
                        background: pal.panelAlt, borderLeft: `4px solid ${item.color}`
                      }}>
                        <div style={{ fontWeight: 800, color: item.color, marginBottom: 4 }}>{item.level}</div>
                        <div className="mono-label" style={{ fontSize: 10.5, color: pal.mid, marginBottom: 6 }}>{item.range}</div>
                        <div style={{ fontSize: 12.5, color: pal.mid }}>{item.text}</div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            );
          })()}

          {/* GLOF BASINS */}
          {activeTab === 'basins' && (() => {
            const PRIO_COLOR = { Critical: '#f0433a', Warning: '#f5a524', Watch: '#38bdf8', Normal: '#2dd48e' };
            const basins = basinsData?.basins || [];
            const selected = basins.find((b) => b.id === selectedBasinId) || basins[0] || null;

            return (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
                <div style={{ ...cardStyle, padding: 24 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16, flexWrap: 'wrap' }}>
                    <div>
                      <div style={eyebrow}>Drainage & Storage Corridors</div>
                      <h3 style={{ ...sectionTitle, marginBottom: 8 }}>GLOF Basins</h3>
                      <p style={{ margin: 0, color: pal.mid, fontSize: 13.5, maxWidth: 760, lineHeight: 1.55 }}>
                        Where outburst water is likely to <strong>route and temporarily store</strong> after a GLOF —
                        valleys, gorges, plains, and confluences — plus lakes assigned to each catchment,
                        estimated water volume, cascade notes, and settlements at risk.
                      </p>
                    </div>
                    <motion.button
                      className="btn-3d"
                      whileHover={{ scale: 1.03 }}
                      whileTap={{ scale: 0.97 }}
                      disabled={basinsLoading}
                      onClick={async () => {
                        await fetchBasins();
                      }}
                      style={{
                        padding: '11px 18px', borderRadius: 12, border: 'none',
                        background: basinsLoading ? '#94a3b8' : 'linear-gradient(135deg,#5eead4,#38bdf8)',
                        color: '#06131a', fontWeight: 800, fontSize: 13, cursor: 'pointer'
                      }}
                    >
                      {basinsLoading ? 'Loading basins…' : basinsData ? 'Refresh Basins' : 'Load GLOF Basins'}
                    </motion.button>
                  </div>
                </div>

                {!basinsData && !basinsLoading && (
                  <div style={{ ...cardStyle, padding: 48, textAlign: 'center', color: pal.mid }}>
                    Click <strong style={{ color: pal.accent }}>Load GLOF Basins</strong> to map lakes into drainage corridors and storage nodes.
                  </div>
                )}

                {basinsLoading && (
                  <div style={{ ...cardStyle, padding: 40, textAlign: 'center', color: pal.mid }}>Loading basin catalogue…</div>
                )}

                {basinsData && !basinsLoading && (
                  <>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12 }}>
                      <div style={{ ...cardStyle, padding: '16px 18px' }}>
                        <div className="mono-label" style={{ fontSize: 10, color: pal.mid }}>Basins</div>
                        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 26, fontWeight: 700, color: pal.signal }}>{basinsData.total_basins}</div>
                      </div>
                      <div style={{ ...cardStyle, padding: '16px 18px' }}>
                        <div className="mono-label" style={{ fontSize: 10, color: pal.mid }}>Lakes assigned</div>
                        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 26, fontWeight: 700, color: pal.hi }}>
                          {basins.reduce((s, b) => s + (b.lake_count || 0), 0)}
                        </div>
                      </div>
                      <div style={{ ...cardStyle, padding: '16px 18px' }}>
                        <div className="mono-label" style={{ fontSize: 10, color: pal.mid }}>Critical basins</div>
                        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 26, fontWeight: 700, color: '#f0433a' }}>
                          {basins.filter((b) => b.monitoring_priority === 'Critical').length}
                        </div>
                      </div>
                      <div style={{ ...cardStyle, padding: '16px 18px' }}>
                        <div className="mono-label" style={{ fontSize: 10, color: pal.mid }}>Est. total volume</div>
                        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 22, fontWeight: 700, color: pal.accent }}>
                          {basins.reduce((s, b) => s + (b.total_estimated_outburst_volume_million_m3 || 0), 0).toFixed(1)} M m³
                        </div>
                      </div>
                    </div>

                    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(260px, 0.9fr) minmax(0, 1.1fr)', gap: 20 }}>
                      {/* Basin list */}
                      <div style={{ ...cardStyle, padding: 16, maxHeight: 640, overflowY: 'auto' }}>
                        <div style={eyebrow}>Catchments</div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 10 }}>
                          {basins.map((b) => {
                            const active = selected?.id === b.id;
                            const pc = PRIO_COLOR[b.monitoring_priority] || pal.mid;
                            return (
                              <button
                                key={b.id}
                                onClick={() => {
                                  setSelectedBasinId(b.id);
                                  loadBasinSatellite(b);
                                }}
                                style={{
                                  textAlign: 'left', padding: '12px 14px', borderRadius: 12, cursor: 'pointer',
                                  border: active ? `1.5px solid ${pc}` : `1px solid ${pal.border}`,
                                  background: active ? (isDark ? 'rgba(56,189,248,0.1)' : 'rgba(2,132,199,0.06)') : pal.panelAlt,
                                  color: pal.hi,
                                }}
                              >
                                <div style={{ fontWeight: 700, fontSize: 14 }}>{b.name}</div>
                                <div style={{ fontSize: 11.5, color: pal.mid, marginTop: 4 }}>{b.river}</div>
                                <div style={{ display: 'flex', gap: 10, marginTop: 8, fontFamily: 'var(--font-mono)', fontSize: 11.5 }}>
                                  <span style={{ color: pc, fontWeight: 700 }}>{b.monitoring_priority}</span>
                                  <span style={{ color: pal.lo }}>{b.lake_count} lakes</span>
                                  <span style={{ color: pal.lo }}>{b.total_estimated_outburst_volume_million_m3 || 0} M m³</span>
                                </div>
                              </button>
                            );
                          })}
                        </div>
                      </div>

                      {/* Basin detail */}
                      <div style={{ ...cardStyle, padding: 22 }}>
                        {!selected ? (
                          <div style={{ color: pal.mid, textAlign: 'center', padding: 40 }}>Select a basin</div>
                        ) : (
                          <>
                            <div style={eyebrow}>Basin dossier</div>
                            <h3 style={{ ...sectionTitle, marginBottom: 6 }}>{selected.name}</h3>
                            <div style={{ fontSize: 13, color: pal.mid, marginBottom: 12 }}>{selected.river}</div>
                            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 16 }}>
                              <span style={{
                                padding: '4px 12px', borderRadius: 20, fontWeight: 800, fontSize: 12,
                                background: `${PRIO_COLOR[selected.monitoring_priority]}22`,
                                color: PRIO_COLOR[selected.monitoring_priority]
                              }}>
                                Priority: {selected.monitoring_priority}
                              </span>
                              <span style={{
                                padding: '4px 12px', borderRadius: 20, fontWeight: 700, fontSize: 12,
                                background: pal.panelAlt, color: pal.hi, border: `1px solid ${pal.border}`
                              }}>
                                {selected.lake_count} lakes
                              </span>
                              {selected.area_km2_approx && (
                                <span style={{
                                  padding: '4px 12px', borderRadius: 20, fontWeight: 700, fontSize: 12,
                                  background: pal.panelAlt, color: pal.mid, border: `1px solid ${pal.border}`
                                }}>
                                  ~{selected.area_km2_approx.toLocaleString()} km²
                                </span>
                              )}
                            </div>

                            <div style={{
                              display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 16
                            }}>
                              <div style={{ padding: 12, borderRadius: 12, background: pal.panelAlt, border: `1px solid ${pal.border}` }}>
                                <div className="mono-label" style={{ fontSize: 10, color: pal.mid }}>Lake surface area</div>
                                <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 800, color: pal.signal, fontSize: 18 }}>
                                  {selected.total_lake_area_ha ?? 0} ha
                                </div>
                              </div>
                              <div style={{ padding: 12, borderRadius: 12, background: pal.panelAlt, border: `1px solid ${pal.border}` }}>
                                <div className="mono-label" style={{ fontSize: 10, color: pal.mid }}>Est. outburst volume</div>
                                <div style={{ fontFamily: 'var(--font-mono)', fontWeight: 800, color: '#f5a524', fontSize: 18 }}>
                                  {selected.total_estimated_outburst_volume_million_m3 ?? 0} M m³
                                </div>
                              </div>
                            </div>

                            <div style={eyebrow}>Where water stores / routes</div>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, margin: '8px 0 16px' }}>
                              {(selected.downstream_storage || []).map((node, i) => (
                                <div key={i} style={{
                                  padding: '10px 12px', borderRadius: 10, border: `1px solid ${pal.border}`,
                                  background: isDark ? 'rgba(94,234,212,0.06)' : '#f0fdfa'
                                }}>
                                  <div style={{ fontWeight: 700, color: pal.hi, fontSize: 13.5 }}>{node.name}</div>
                                  <div style={{ fontSize: 11.5, color: pal.mid, marginTop: 2 }}>
                                    Type: {node.type?.replace(/_/g, ' ')}
                                    {node.lat != null && ` · ${node.lat.toFixed(2)}°N, ${node.lon.toFixed(2)}°E`}
                                  </div>
                                </div>
                              ))}
                            </div>

                            <div style={eyebrow}>Settlements at risk</div>
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, margin: '8px 0 16px' }}>
                              {(selected.at_risk_settlements || []).map((s) => (
                                <span key={s} style={{
                                  padding: '4px 10px', borderRadius: 999, fontSize: 12, fontWeight: 600,
                                  background: isDark ? 'rgba(240,67,58,0.12)' : '#fef2f2', color: '#f0433a',
                                  border: '1px solid rgba(240,67,58,0.25)'
                                }}>
                                  {s}
                                </span>
                              ))}
                            </div>

                            <div style={eyebrow}>Cascade notes</div>
                            <p style={{ margin: '6px 0 16px', fontSize: 13.5, color: pal.mid, lineHeight: 1.55 }}>
                              {selected.cascade_notes}
                            </p>

                            <div style={eyebrow}>Lakes in this basin</div>
                            <div style={{ overflowX: 'auto', marginTop: 8 }}>
                              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                                <thead>
                                  <tr style={{ background: pal.panelAlt }}>
                                    {['Name', 'Area', 'Est. volume'].map((h) => (
                                      <th key={h} className="mono-label" style={{ padding: '8px 10px', fontSize: 10, color: pal.mid, textAlign: 'left' }}>{h}</th>
                                    ))}
                                  </tr>
                                </thead>
                                <tbody>
                                  {(selected.lakes || []).length === 0 ? (
                                    <tr><td colSpan={3} style={{ padding: 16, color: pal.mid }}>No inventory lakes in this bbox</td></tr>
                                  ) : selected.lakes.map((l) => (
                                    <tr key={l.id} style={{ borderBottom: `1px solid ${pal.border}` }}>
                                      <td style={{ padding: '8px 10px', fontWeight: 600, color: pal.hi }}>{l.name}</td>
                                      <td style={{ padding: '8px 10px', fontFamily: 'var(--font-mono)', color: pal.mid, fontSize: 12 }}>
                                        {l.area_ha != null ? `${l.area_ha} ha` : '—'}
                                      </td>
                                      <td style={{ padding: '8px 10px', fontFamily: 'var(--font-mono)', color: pal.signal, fontSize: 12 }}>
                                        {l.estimated_volume_m3 != null
                                          ? `${(l.estimated_volume_m3 / 1e6).toFixed(2)} M m³`
                                          : '—'}
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>

                            {/* Live satellite — same sources as Historical tab */}
                            <div style={{ marginTop: 18 }}>
                              <div style={eyebrow}>Live Satellite</div>
                              <h3 style={{ ...sectionTitle, fontSize: 15, marginBottom: 10 }}>
                                Basin center · NDWI & True Color
                              </h3>
                              <p style={{ margin: '0 0 12px', fontSize: 12, color: pal.lo }}>
                                Sentinel-2 via GEE at basin center
                                {selected.center ? ` (${selected.center.lat}°N, ${selected.center.lon}°E)` : ''}
                              </p>
                              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 14 }}>
                                <div style={{ background: pal.panelAlt, borderRadius: 12, padding: 12, border: `1px solid ${pal.border}` }}>
                                  <div style={{ fontSize: 13, fontWeight: 700, color: pal.hi, marginBottom: 8 }}>NDWI / Water Highlight</div>
                                  {basinThumbLoading ? (
                                    <div style={{ height: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', color: pal.mid }}>
                                      Loading NDWI…
                                    </div>
                                  ) : basinThumbNdwi ? (
                                    <img
                                      src={basinThumbNdwi}
                                      alt={`${selected.name} NDWI`}
                                      style={{ width: '100%', borderRadius: 10, border: `1px solid ${pal.border}`, display: 'block' }}
                                    />
                                  ) : (
                                    <div style={{ height: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', color: pal.lo, fontSize: 12.5, textAlign: 'center', padding: 12 }}>
                                      Image not available (needs GEE auth on server)
                                    </div>
                                  )}
                                </div>
                                <div style={{ background: pal.panelAlt, borderRadius: 12, padding: 12, border: `1px solid ${pal.border}` }}>
                                  <div style={{ fontSize: 13, fontWeight: 700, color: pal.hi, marginBottom: 8 }}>True Color (RGB)</div>
                                  {basinThumbLoading ? (
                                    <div style={{ height: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', color: pal.mid }}>
                                      Loading RGB…
                                    </div>
                                  ) : basinThumbRgb ? (
                                    <img
                                      src={basinThumbRgb}
                                      alt={`${selected.name} RGB`}
                                      style={{ width: '100%', borderRadius: 10, border: `1px solid ${pal.border}`, display: 'block' }}
                                    />
                                  ) : (
                                    <div style={{ height: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', color: pal.lo, fontSize: 12.5, textAlign: 'center', padding: 12 }}>
                                      Image not available (needs GEE auth on server)
                                    </div>
                                  )}
                                </div>
                              </div>
                              <button
                                onClick={() => loadBasinSatellite(selected)}
                                disabled={basinThumbLoading}
                                style={{
                                  marginTop: 10, padding: '8px 12px', borderRadius: 8, border: `1px solid ${pal.border}`,
                                  background: pal.panelAlt, color: pal.signal, fontWeight: 700, fontSize: 12, cursor: 'pointer'
                                }}
                              >
                                {basinThumbLoading ? 'Loading…' : 'Reload satellite images'}
                              </button>
                            </div>
                          </>
                        )}
                      </div>
                    </div>

                    {(basinsData.notes || []).length > 0 && (
                      <div style={{ ...cardStyle, padding: 18 }}>
                        <div style={eyebrow}>Method notes</div>
                        <ul style={{ margin: '8px 0 0', paddingLeft: 18, color: pal.mid, fontSize: 13, lineHeight: 1.55 }}>
                          {basinsData.notes.map((n, i) => <li key={i}>{n}</li>)}
                        </ul>
                      </div>
                    )}
                  </>
                )}
              </div>
            );
          })()}

          {/* SATELLITE DETECTION */}
          {activeTab === 'satellite' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
              <div style={{ ...cardStyle, padding: 28 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24, flexWrap: 'wrap', gap: 16 }}>
                  <div>
                    <div style={eyebrow}>Remote Sensing</div>
                    <h3 style={sectionTitle}>Satellite Lake Detection</h3>
                  </div>
                  <button
                    onClick={handleDetectLakes}
                    disabled={detecting}
                    className="btn-3d"
                    style={{
                      display: 'flex', alignItems: 'center', gap: 10,
                      padding: '11px 20px', borderRadius: 12, border: 'none',
                      background: detecting ? '#94a3b8' : 'linear-gradient(135deg,#5eead4,#38bdf8)',
                      color: '#06131a', fontWeight: 800, fontSize: 14, cursor: 'pointer'
                    }}
                  >
                    <IconSatellite size={18} color="#06131a" />
                    {detecting ? 'Detecting...' : 'Run Satellite Detection'}
                  </button>
                </div>

                {detectedLakes.length === 0 ? (
                  <div style={{ textAlign: 'center', padding: 60, color: pal.mid }}>
                    No detection results yet. Click “Run Satellite Detection”.
                  </div>
                ) : (
                  <>
                    <div style={{ display: 'flex', gap: 12, marginBottom: 20, flexWrap: 'wrap' }}>
                      <button
                        onClick={handleSaveDetectedLakes}
                        disabled={saving}
                        className="btn-3d"
                        style={{
                          padding: '11px 20px', borderRadius: 12, border: 'none',
                          background: saving ? '#94a3b8' : '#2dd48e',
                          color: '#06131a', fontWeight: 800, cursor: 'pointer'
                        }}
                      >
                        {saving ? 'Saving...' : `Save ${detectedLakes.length} Lakes`}
                      </button>
                      <span style={{ fontSize: 13, color: pal.mid, alignSelf: 'center' }}>
                        {detectedLakes.length} lakes detected · Sorted by area (largest first)
                      </span>
                    </div>

                    <div className="hud-frame" style={{ height: 420, borderRadius: 14, overflow: 'hidden', border: `1px solid ${pal.border}`, marginBottom: 24 }}>
                      <MapView
                        selectedRisk={satelliteRisk}
                        setSelectedRisk={setSatelliteRisk}
                        lakes={mapDetected}
                        setSelectedLake={setSelectedLake}
                        loading={false}
                      />
                    </div>

                    <div>
                      <div style={eyebrow}>Detection Results</div>
                      <h3 style={{ ...sectionTitle, marginBottom: 16 }}>Detected Lakes List</h3>
                      <div style={{ overflowX: 'auto' }}>
                        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                          <thead>
                            <tr style={{ background: pal.panelAlt }}>
                              {['#', 'Name', 'Area (ha)', 'Risk', 'Latitude', 'Longitude'].map(h => (
                                <th key={h} className="mono-label" style={{ padding: '12px 14px', fontSize: 10.5, color: pal.mid, textAlign: 'left' }}>
                                  {h}
                                </th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {detectedLakes.map((lake, i) => {
                              const risk = calculateRisk(lake.area_ha);
                              const rc = RISK_COLOR[risk];
                              return (
                                <tr key={i} style={{ borderBottom: `1px solid ${pal.border}` }}>
                                  <td style={{ padding: '12px 14px', fontFamily: 'var(--font-mono)', color: pal.mid }}>
                                    {String(i + 1).padStart(2, '0')}
                                  </td>
                                  <td style={{ padding: '12px 14px', fontWeight: 700, color: pal.hi }}>
                                    {lake.name}
                                  </td>
                                  <td style={{ padding: '12px 14px', fontFamily: 'var(--font-mono)', color: pal.signal, fontWeight: 700 }}>
                                    {lake.area_ha}
                                  </td>
                                  <td style={{ padding: '12px 14px' }}>
                                    <span style={{
                                      display: 'inline-flex', alignItems: 'center', gap: 6,
                                      padding: '3px 10px', borderRadius: 20, fontSize: 11.5, fontWeight: 700,
                                      background: `${rc}18`, color: rc
                                    }}>
                                      <span style={{ width: 6, height: 6, borderRadius: '50%', background: rc }} />
                                      {risk}
                                    </span>
                                  </td>
                                  <td style={{ padding: '12px 14px', fontFamily: 'var(--font-mono)', fontSize: 12.5, color: pal.mid }}>
                                    {lake.latitude?.toFixed(5) ?? '—'}
                                  </td>
                                  <td style={{ padding: '12px 14px', fontFamily: 'var(--font-mono)', fontSize: 12.5, color: pal.mid }}>
                                    {lake.longitude?.toFixed(5) ?? '—'}
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  </>
                )}
              </div>
            </div>
          )}

            </motion.div>
          </AnimatePresence>
        </main>
      </div>

      {/* Lake Details Modal — inventory / map specific view with satellite imagery */}
      <AnimatePresence>
      {selectedLake && (() => {
        const exp = getRiskExplanation(selectedLake);
        const risk = calculateRisk(selectedLake.area_ha);
        const rc = RISK_COLOR[risk];
        const detailChartData = (detailHistData?.years || [])
          .filter(y => y.area_ha != null)
          .map(y => ({ year: String(y.year), area: y.area_ha }));
        const detailTrendColor =
          detailHistData?.trend === 'growing' ? '#f0433a'
            : detailHistData?.trend === 'shrinking' ? '#2dd48e'
              : pal.signal;
        const imgBox = {
          height: 240,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: pal.mid,
          background: pal.panelAlt,
          borderRadius: 12,
          border: `1px solid ${pal.border}`,
        };

        return (
          <motion.div
            key="lake-details-backdrop"
            variants={modalBackdrop}
            initial="hidden"
            animate="show"
            exit="exit"
            style={{
              position: 'fixed', inset: 0, background: 'rgba(6,11,19,0.72)',
              backdropFilter: 'blur(4px)', display: 'flex', alignItems: 'center',
              justifyContent: 'center', zIndex: 1000, padding: 20
            }}
            onClick={closeLakeDetails}
          >
            <motion.div
              className="hud-frame"
              variants={modalPanel}
              initial="hidden"
              animate="show"
              exit="exit"
              style={{
                background: pal.panel, borderRadius: 20, border: `1px solid ${pal.border}`,
                width: '100%', maxWidth: 980, maxHeight: '92vh', overflow: 'auto'
              }}
              onClick={e => e.stopPropagation()}
            >
              {/* Header */}
              <div style={{
                padding: '18px 24px', borderBottom: `1px solid ${pal.border}`,
                display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap'
              }}>
                <div>
                  <div style={{ ...eyebrow, marginBottom: 4 }}>Lake Profile</div>
                  <h2 style={{ margin: 0, fontFamily: 'var(--font-display)', fontSize: 22, fontWeight: 700, color: pal.hi }}>
                    {selectedLake.name}
                  </h2>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{
                    display: 'inline-flex', alignItems: 'center', gap: 6,
                    padding: '5px 12px', borderRadius: 20, fontSize: 12, fontWeight: 700,
                    background: `${rc}18`, color: rc
                  }}>
                    <span style={{ width: 7, height: 7, borderRadius: '50%', background: rc }} />
                    {risk} Risk
                  </span>
                  <button
                    onClick={closeLakeDetails}
                    style={{
                      background: pal.panelAlt, border: 'none', width: 34, height: 34,
                      borderRadius: 10, fontSize: 18, cursor: 'pointer', color: pal.hi
                    }}
                  >
                    ×
                  </button>
                </div>
              </div>

              <div style={{ padding: 24, display: 'flex', flexDirection: 'column', gap: 20 }}>
                {/* KPI strip */}
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12 }}>
                  <div style={{ background: pal.panelAlt, borderRadius: 12, padding: '14px 16px', border: `1px solid ${pal.border}` }}>
                    <div className="mono-label" style={{ fontSize: 10, color: pal.mid, marginBottom: 6 }}>Area</div>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 20, fontWeight: 700, color: pal.signal }}>
                      {selectedLake.area_ha ?? '—'} <span style={{ fontSize: 12, color: pal.mid }}>ha</span>
                    </div>
                  </div>
                  <div style={{ background: pal.panelAlt, borderRadius: 12, padding: '14px 16px', border: `1px solid ${pal.border}` }}>
                    <div className="mono-label" style={{ fontSize: 10, color: pal.mid, marginBottom: 6 }}>Latitude</div>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 16, fontWeight: 700, color: pal.hi }}>
                      {selectedLake.latitude ?? '—'}
                    </div>
                  </div>
                  <div style={{ background: pal.panelAlt, borderRadius: 12, padding: '14px 16px', border: `1px solid ${pal.border}` }}>
                    <div className="mono-label" style={{ fontSize: 10, color: pal.mid, marginBottom: 6 }}>Longitude</div>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 16, fontWeight: 700, color: pal.hi }}>
                      {selectedLake.longitude ?? '—'}
                    </div>
                  </div>
                  <div style={{ background: pal.panelAlt, borderRadius: 12, padding: '14px 16px', border: `1px solid ${pal.border}` }}>
                    <div className="mono-label" style={{ fontSize: 10, color: pal.mid, marginBottom: 6 }}>Area Trend</div>
                    <div style={{
                      fontFamily: 'var(--font-mono)', fontSize: 16, fontWeight: 700,
                      color: detailTrendColor, textTransform: 'capitalize'
                    }}>
                      {detailHistLoading ? 'Loading…' : (detailHistData?.trend || '—')}
                    </div>
                  </div>
                  <div style={{ background: pal.panelAlt, borderRadius: 12, padding: '14px 16px', border: `1px solid ${pal.border}` }}>
                    <div className="mono-label" style={{ fontSize: 10, color: pal.mid, marginBottom: 6 }}>Local Weather</div>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 16, fontWeight: 700, color: pal.hi }}>
                      {weatherLoading ? '…' : lakeWeather ? `${lakeWeather.temperature_2m}°C` : '—'}
                    </div>
                    {lakeWeather && (
                      <div style={{ fontSize: 11, color: pal.mid, marginTop: 4 }}>
                        Wind {lakeWeather.wind_speed_10m} km/h · RH {lakeWeather.relative_humidity_2m}%
                      </div>
                    )}
                  </div>
                </div>

                {/* Risk banner */}
                <div style={{ background: exp.bg, borderRadius: 14, padding: 16, borderLeft: `4px solid ${exp.color}` }}>
                  <h4 style={{ margin: '0 0 8px', color: exp.color, fontWeight: 800 }}>{exp.title}</h4>
                  <p style={{ margin: 0, fontSize: 14, color: pal.mid }}>{exp.reason}</p>
                </div>

                {/* Satellite imagery — same sources as Historical tab */}
                <div>
                  <div style={eyebrow}>Live Satellite</div>
                  <h3 style={{ ...sectionTitle, marginBottom: 12, fontSize: 16 }}>
                    Sentinel Imagery · NDWI & True Color
                  </h3>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 16 }}>
                    <div style={{ background: pal.panelAlt, borderRadius: 14, padding: 14, border: `1px solid ${pal.border}` }}>
                      <div style={{ fontSize: 13, fontWeight: 700, color: pal.hi, marginBottom: 10 }}>
                        NDWI / Water Highlight
                      </div>
                      {detailThumbLoading ? (
                        <div style={imgBox}>Loading NDWI image…</div>
                      ) : detailThumbNdwi ? (
                        <img
                          src={detailThumbNdwi}
                          alt={`${selectedLake.name} NDWI`}
                          style={{ width: '100%', borderRadius: 12, border: `1px solid ${pal.border}`, display: 'block' }}
                        />
                      ) : (
                        <div style={imgBox}>
                          {selectedLake.latitude ? 'Image not available' : 'No coordinates for this lake'}
                        </div>
                      )}
                    </div>
                    <div style={{ background: pal.panelAlt, borderRadius: 14, padding: 14, border: `1px solid ${pal.border}` }}>
                      <div style={{ fontSize: 13, fontWeight: 700, color: pal.hi, marginBottom: 10 }}>
                        True Color (RGB)
                      </div>
                      {detailThumbLoading ? (
                        <div style={imgBox}>Loading RGB image…</div>
                      ) : detailThumbRgb ? (
                        <img
                          src={detailThumbRgb}
                          alt={`${selectedLake.name} RGB`}
                          style={{ width: '100%', borderRadius: 12, border: `1px solid ${pal.border}`, display: 'block' }}
                        />
                      ) : (
                        <div style={imgBox}>
                          {selectedLake.latitude ? 'Image not available' : 'No coordinates for this lake'}
                        </div>
                      )}
                    </div>
                  </div>
                  <p style={{ margin: '10px 0 0', fontSize: 12, color: pal.lo }}>
                    Data source: Google Earth Engine · Sentinel-2 composites (same as Historical Analysis)
                  </p>
                </div>

                {/* Area history chart */}
                <div>
                  <div style={eyebrow}>Time Series</div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
                    <h3 style={{ ...sectionTitle, fontSize: 16, margin: 0 }}>Surface Area Over Time</h3>
                    <button
                      className="btn-3d"
                      onClick={() => {
                        setHistLakeId(String(selectedLake.id));
                        closeLakeDetails();
                        setActiveTab('historical');
                        fetchHistorical(selectedLake);
                      }}
                      style={{
                        padding: '8px 14px', borderRadius: 10, border: 'none',
                        background: 'linear-gradient(135deg,#5eead4,#38bdf8)',
                        color: '#06131a', fontWeight: 800, fontSize: 12, cursor: 'pointer'
                      }}
                    >
                      Open Full Historical Analysis
                    </button>
                  </div>

                  {detailHistLoading ? (
                    <div style={{ ...imgBox, height: 220 }}>
                      Loading historical series from GEE (30–90s)…
                    </div>
                  ) : detailChartData.length > 0 ? (
                    <div style={{ background: pal.panelAlt, borderRadius: 14, padding: 16, border: `1px solid ${pal.border}` }}>
                      <ResponsiveContainer width="100%" height={240}>
                        <LineChart data={detailChartData} margin={{ top: 10, right: 16, left: 0, bottom: 0 }}>
                          <CartesianGrid strokeDasharray="3 3" stroke={pal.border} />
                          <XAxis dataKey="year" tick={{ fill: pal.mid, fontSize: 11 }} />
                          <YAxis tick={{ fill: pal.mid, fontSize: 11 }} unit=" ha" />
                          <Tooltip contentStyle={chartTooltipStyle} formatter={(v) => [`${v} ha`, 'Area']} />
                          <Line
                            type="monotone"
                            dataKey="area"
                            stroke="#38bdf8"
                            strokeWidth={3}
                            dot={{ r: 4, fill: '#5eead4' }}
                            activeDot={{ r: 6 }}
                          />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  ) : (
                    <div style={{ ...imgBox, height: 160 }}>
                      {selectedLake.latitude
                        ? 'Historical area series unavailable for this location'
                        : 'No coordinates — cannot load area history'}
                    </div>
                  )}

                  {/* Compact year table when data exists */}
                  {detailChartData.length > 0 && (
                    <div style={{ marginTop: 12, overflowX: 'auto' }}>
                      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                        <thead>
                          <tr style={{ background: pal.panelAlt }}>
                            {['Year', 'Area (ha)'].map(h => (
                              <th key={h} className="mono-label" style={{ padding: '10px 12px', fontSize: 10.5, color: pal.mid, textAlign: 'left' }}>
                                {h}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {(detailHistData?.years || []).map((y) => (
                            <tr key={y.year} style={{ borderBottom: `1px solid ${pal.border}` }}>
                              <td style={{ padding: '10px 12px', fontFamily: 'var(--font-mono)', color: pal.hi }}>{y.year}</td>
                              <td style={{ padding: '10px 12px', fontFamily: 'var(--font-mono)', fontWeight: 700, color: pal.signal }}>
                                {y.area_ha != null ? y.area_ha : '—'}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              </div>
            </motion.div>
          </motion.div>
        );
      })()}
      </AnimatePresence>

      {/* Rename Modal */}
      <AnimatePresence>
      {renameLake && (
        <motion.div
          key="rename-backdrop"
          variants={modalBackdrop}
          initial="hidden"
          animate="show"
          exit="exit"
          style={{
            position: 'fixed', inset: 0, background: 'rgba(6,11,19,0.72)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1100, padding: 20
          }}
          onClick={() => setRenameLake(null)}
        >
          <motion.div
            variants={modalPanel}
            initial="hidden"
            animate="show"
            exit="exit"
            style={{
              background: pal.panel, borderRadius: 18, border: `1px solid ${pal.border}`,
              width: '100%', maxWidth: 400, padding: 26
            }}
            onClick={e => e.stopPropagation()}
          >
            <h3 style={{ margin: '0 0 12px', color: pal.hi }}>Rename Lake</h3>
            <input
              type="text"
              value={newName}
              onChange={e => setNewName(e.target.value)}
              autoFocus
              className="input-3d"
              style={{
                width: '100%', padding: '11px 14px', borderRadius: 10,
                border: `1px solid ${pal.border}`, marginBottom: 18,
                background: pal.panelAlt, color: pal.hi
              }}
              onKeyDown={e => e.key === 'Enter' && handleRenameSubmit()}
            />
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
              <motion.button
                onClick={() => setRenameLake(null)}
                className="btn-3d"
                whileHover={{ scale: 1.03 }}
                whileTap={{ scale: 0.97 }}
                style={{
                  padding: '9px 18px', borderRadius: 10, border: `1px solid ${pal.border}`,
                  background: pal.panelAlt, color: pal.mid, cursor: 'pointer'
                }}
              >
                Cancel
              </motion.button>
              <motion.button
                onClick={handleRenameSubmit}
                className="btn-3d"
                whileHover={{ scale: 1.04 }}
                whileTap={{ scale: 0.97 }}
                style={{
                  padding: '9px 20px', borderRadius: 10, border: 'none',
                  background: 'linear-gradient(135deg,#5eead4,#38bdf8)',
                  color: '#06131a', fontWeight: 800, cursor: 'pointer'
                }}
              >
                Save Name
              </motion.button>
            </div>
          </motion.div>
        </motion.div>
      )}
      </AnimatePresence>
    </motion.div>
  );
}

export default function App() {
  return (
    <ThemeProvider>
      <AppContent />
    </ThemeProvider>
  );
}
