import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Area,
  AreaChart,
  CartesianGrid,
  ComposedChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { fetchBreachSimulation, isAbortError } from '../api';

const DAM_TYPES = [
  { value: 'moraine', label: 'Moraine' },
  { value: 'ice', label: 'Ice' },
  { value: 'landslide', label: 'Landslide' },
];

const SPEED_OPTIONS = [1, 2, 4];

const SEVERITY_COLORS = {
  Critical: '#f0433a',
  Warning: '#f5a524',
  Watch: '#38bdf8',
  Low: '#2dd48e',
};

function interpolateHydrograph(hydrograph, timeMin) {
  if (!Array.isArray(hydrograph) || hydrograph.length === 0) return 0;
  const clamped = Math.max(0, Math.min(240, timeMin));
  if (clamped <= hydrograph[0].time_min) return Number(hydrograph[0].discharge_m3s || 0);
  const last = hydrograph[hydrograph.length - 1];
  if (clamped >= last.time_min) return Number(last.discharge_m3s || 0);

  for (let i = 0; i < hydrograph.length - 1; i += 1) {
    const p0 = hydrograph[i];
    const p1 = hydrograph[i + 1];
    if (clamped >= p0.time_min && clamped <= p1.time_min) {
      const span = p1.time_min - p0.time_min;
      if (span <= 0) return Number(p0.discharge_m3s || 0);
      const alpha = (clamped - p0.time_min) / span;
      return Number(p0.discharge_m3s || 0) + alpha * (Number(p1.discharge_m3s || 0) - Number(p0.discharge_m3s || 0));
    }
  }
  return 0;
}

function interpolateWavefrontDistance(simData, corridorData, timeMin) {
  const nodes = simData?.downstream_nodes || [];
  if (nodes.length === 0) {
    const corridorKm = Number(corridorData?.lake?.total_corridor_km || 0);
    return corridorKm > 0 ? Math.min(corridorKm, (timeMin / 240) * corridorKm) : 0;
  }

  const breachDuration = Number(simData?.params?.breach_duration_min ?? 45);
  const tDepart = 0.25 * breachDuration;
  const times = [tDepart];
  const dists = [0];

  for (let i = 0; i < nodes.length; i += 1) {
    const prevT = times[times.length - 1];
    const rawEta = Number(nodes[i]?.eta_min ?? prevT + 15);
    times.push(Math.max(prevT + 0.01, rawEta));
    dists.push(Number(nodes[i]?.distance_km ?? dists[dists.length - 1]));
  }

  if (timeMin <= times[0]) return 0;
  if (timeMin >= times[times.length - 1]) return dists[dists.length - 1];

  for (let i = 0; i < times.length - 1; i += 1) {
    if (timeMin >= times[i] && timeMin <= times[i + 1]) {
      const span = times[i + 1] - times[i];
      const alpha = span > 0 ? (timeMin - times[i]) / span : 0;
      return dists[i] + alpha * (dists[i + 1] - dists[i]);
    }
  }
  return 0;
}

export function BreachSimulatorPanel({
  lake = null,
  lakeId,
  lakeName,
  corridorData = null,
  simTimeMin,
  onTimeChange,
  isPlaying,
  onPlayingChange,
  onSimulationData,
  onSimulationUpdate,
  onClose,
}) {
  const resolvedLakeId = lake?.id ?? lakeId ?? corridorData?.lake?.id;
  const resolvedLakeName =
    lake?.name ?? lakeName ?? corridorData?.lake?.name ?? 'Selected Glacial Lake';

  const [damType, setDamType] = useState('moraine');
  const [breachFraction, setBreachFraction] = useState(0.75);
  const [breachDurationMin, setBreachDurationMin] = useState(45);

  const [simData, setSimData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [retryTick, setRetryTick] = useState(0);

  const [internalTimeMin, setInternalTimeMin] = useState(0);
  const [internalPlaying, setInternalPlaying] = useState(false);
  const [playbackSpeed, setPlaybackSpeed] = useState(1);

  const isTimeControlled = simTimeMin !== undefined && simTimeMin !== null;
  const isPlayingControlled = isPlaying !== undefined && isPlaying !== null;
  const activeTimeMin = isTimeControlled ? Number(simTimeMin) : internalTimeMin;
  const activePlaying = isPlayingControlled ? Boolean(isPlaying) : internalPlaying;

  const activeTimeRef = useRef(activeTimeMin);
  activeTimeRef.current = activeTimeMin;

  const onTimeChangeRef = useRef(onTimeChange);
  onTimeChangeRef.current = onTimeChange;
  const onPlayingChangeRef = useRef(onPlayingChange);
  onPlayingChangeRef.current = onPlayingChange;
  const onSimDataRef = useRef(onSimulationData || onSimulationUpdate);
  onSimDataRef.current = onSimulationData || onSimulationUpdate;

  const setTime = useCallback(
    (nextVal) => {
      const clamped = Math.max(0, Math.min(240, Number(nextVal)));
      if (!isTimeControlled) {
        setInternalTimeMin(clamped);
      }
      onTimeChangeRef.current?.(clamped);
    },
    [isTimeControlled],
  );

  const setPlaying = useCallback(
    (nextBool) => {
      const val = Boolean(nextBool);
      if (!isPlayingControlled) {
        setInternalPlaying(val);
      }
      onPlayingChangeRef.current?.(val);
    },
    [isPlayingControlled],
  );

  // Reset timeline when lake changes
  useEffect(() => {
    setTime(0);
    setPlaying(false);
    setError(null);
  }, [resolvedLakeId, setPlaying, setTime]);

  // Fetch simulation from M1 backend with AbortController
  useEffect(() => {
    if (!resolvedLakeId) return undefined;

    const controller = new AbortController();
    setLoading(true);
    setError(null);

    fetchBreachSimulation(
      resolvedLakeId,
      {
        dam_type: damType,
        breach_fraction: breachFraction,
        breach_duration_min: breachDurationMin,
      },
      { signal: controller.signal },
    )
      .then((data) => {
        setSimData(data);
        onSimDataRef.current?.(data);
      })
      .catch((err) => {
        if (isAbortError(err)) return;
        setError(err?.detail || err?.message || 'Failed to compute dam-breach simulation');
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      });

    return () => controller.abort();
  }, [resolvedLakeId, damType, breachFraction, breachDurationMin, retryTick]);

  // Playback loop via requestAnimationFrame
  useEffect(() => {
    if (!activePlaying) return undefined;

    let rafId = 0;
    let lastTimestamp = null;

    const tick = (now) => {
      if (lastTimestamp !== null) {
        const dtSec = Math.min(0.1, (now - lastTimestamp) / 1000);
        const nextTime = activeTimeRef.current + dtSec * playbackSpeed * 6;
        if (nextTime >= 240) {
          setTime(240);
          setPlaying(false);
          return;
        }
        setTime(nextTime);
      }
      lastTimestamp = now;
      rafId = window.requestAnimationFrame(tick);
    };

    rafId = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(rafId);
  }, [activePlaying, playbackSpeed, setPlaying, setTime]);

  const currentDischargeM3s = useMemo(
    () => interpolateHydrograph(simData?.hydrograph, activeTimeMin),
    [simData, activeTimeMin],
  );

  const wavefrontDistanceKm = useMemo(
    () => interpolateWavefrontDistance(simData, corridorData, activeTimeMin),
    [simData, corridorData, activeTimeMin],
  );

  const profileData = useMemo(() => {
    const profile = simData?.elevation_profile || [];
    const nodes = simData?.downstream_nodes || [];
    return profile.map((pt, idx) => {
      const node = idx === 0 ? null : nodes[idx - 1];
      const stageRise = node ? Number(node.stage_rise_m || 0) : 0;
      return {
        distance_km: Number(pt.distance_km || 0),
        elevation_m: Number(pt.elevation_m || 0),
        flood_surface_m: Number((Number(pt.elevation_m || 0) + stageRise).toFixed(2)),
        stage_rise_m: stageRise,
        label: pt.label || (idx === 0 ? resolvedLakeName : `Node ${idx}`),
      };
    });
  }, [simData, resolvedLakeName]);

  if (!resolvedLakeId) {
    return null;
  }

  const provenance = simData?.provenance || {};
  const peak = simData?.peak_discharge || {};
  const volumeMillionM3 = simData?.volume_m3 ? (simData.volume_m3 / 1e6).toFixed(2) : '—';

  return (
    <section
      aria-label="GLOF Breach and Flood-Wave Simulator"
      className="hud-frame"
      style={{
        marginTop: 18,
        background: 'rgba(10, 20, 33, 0.94)',
        border: '1px solid rgba(56, 189, 248, 0.25)',
        borderRadius: 16,
        padding: '18px 20px',
        color: '#eaf4f8',
        boxShadow: '0 14px 38px rgba(4, 10, 18, 0.35)',
      }}
    >
      {/* Header */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          gap: 12,
          flexWrap: 'wrap',
          marginBottom: 16,
        }}
      >
        <div>
          <div
            className="mono-label"
            style={{ fontSize: 10.5, color: '#f472b6', fontWeight: 700, marginBottom: 4 }}
          >
            HYDRAULIC DAM-BREACH &amp; FLOOD-WAVE DIGITAL TWIN
          </div>
          <h3
            style={{
              margin: 0,
              fontSize: 18,
              fontWeight: 700,
              fontFamily: 'var(--font-display)',
              color: '#eaf4f8',
            }}
          >
            GLOF Breach &amp; Flood-Wave Simulator — {simData?.lake_name || resolvedLakeName}
          </h3>
          <div style={{ fontSize: 12.5, color: '#8ea3ba', marginTop: 3 }}>
            {simData?.basin_name || corridorData?.lake?.basin_name || 'Northern Pakistan Basin'}
            {loading ? ' · Updating hydraulic routing...' : ''}
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          {simData && (
            <span
              className="mono-label"
              style={{
                fontSize: 10,
                fontWeight: 700,
                padding: '5px 10px',
                borderRadius: 999,
                border: provenance.fallback_used
                  ? '1px solid rgba(245, 165, 36, 0.45)'
                  : '1px solid rgba(94, 234, 212, 0.35)',
                background: provenance.fallback_used
                  ? 'rgba(245, 165, 36, 0.14)'
                  : 'rgba(94, 234, 212, 0.12)',
                color: provenance.fallback_used ? '#fbbf24' : '#5eead4',
              }}
            >
              DEM: {provenance.elevation_source || 'local_gradient'} ·{' '}
              {provenance.fallback_used ? 'Fallback' : 'Live/Cached'}
            </span>
          )}
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              aria-label="Close Breach Simulator"
              style={{
                background: 'rgba(255, 255, 255, 0.06)',
                border: '1px solid rgba(103, 232, 249, 0.2)',
                color: '#cbd5e1',
                borderRadius: 8,
                padding: '4px 10px',
                fontSize: 16,
                lineHeight: 1.2,
                cursor: 'pointer',
              }}
            >
              ×
            </button>
          )}
        </div>
      </div>

      {/* Inline Error Banner */}
      {error && (
        <div
          role="alert"
          style={{
            background: 'rgba(240, 67, 58, 0.12)',
            border: '1px solid rgba(240, 67, 58, 0.4)',
            borderRadius: 10,
            padding: '10px 14px',
            marginBottom: 14,
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            gap: 12,
            flexWrap: 'wrap',
            fontSize: 13,
            color: '#fca5a5',
          }}
        >
          <span>Simulation unavailable: {error}</span>
          <button
            type="button"
            onClick={() => setRetryTick((c) => c + 1)}
            style={{
              padding: '5px 12px',
              borderRadius: 8,
              border: '1px solid rgba(240, 67, 58, 0.5)',
              background: 'rgba(240, 67, 58, 0.2)',
              color: '#fff',
              fontSize: 12,
              fontWeight: 700,
              cursor: 'pointer',
            }}
          >
            Retry
          </button>
        </div>
      )}

      {/* Parameter Controls */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
          gap: 14,
          padding: '12px 14px',
          borderRadius: 12,
          background: 'rgba(255, 255, 255, 0.03)',
          border: '1px solid rgba(103, 232, 249, 0.14)',
          marginBottom: 16,
        }}
      >
        <div>
          <div className="mono-label" style={{ fontSize: 10, color: '#8ea3ba', marginBottom: 8 }}>
            Dam Material
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {DAM_TYPES.map((item) => {
              const active = damType === item.value;
              return (
                <button
                  key={item.value}
                  type="button"
                  aria-pressed={active}
                  onClick={() => setDamType(item.value)}
                  style={{
                    flex: 1,
                    minWidth: 74,
                    padding: '7px 10px',
                    borderRadius: 8,
                    border: active
                      ? '1px solid rgba(94, 234, 212, 0.6)'
                      : '1px solid rgba(103, 232, 249, 0.16)',
                    background: active
                      ? 'linear-gradient(135deg, rgba(94,234,212,0.22), rgba(56,189,248,0.22))'
                      : 'transparent',
                    color: active ? '#5eead4' : '#9fb4c8',
                    fontSize: 12.5,
                    fontWeight: 700,
                    cursor: 'pointer',
                  }}
                >
                  {item.label}
                </button>
              );
            })}
          </div>
        </div>

        <div>
          <label
            htmlFor={`breach-frac-${resolvedLakeId}`}
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              fontSize: 12,
              marginBottom: 6,
              color: '#cbd5e1',
            }}
          >
            <span className="mono-label" style={{ fontSize: 10, color: '#8ea3ba' }}>
              Breach Fraction
            </span>
            <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color: '#5eead4' }}>
              {breachFraction.toFixed(2)} ({Math.round(breachFraction * 100)}%)
            </span>
          </label>
          <input
            id={`breach-frac-${resolvedLakeId}`}
            type="range"
            min={0.1}
            max={1.0}
            step={0.05}
            value={breachFraction}
            onChange={(e) => setBreachFraction(Number(e.target.value))}
            aria-label="Breach fraction"
            style={{ width: '100%', accentColor: '#5eead4', cursor: 'pointer' }}
          />
        </div>

        <div>
          <label
            htmlFor={`breach-dur-${resolvedLakeId}`}
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              fontSize: 12,
              marginBottom: 6,
              color: '#cbd5e1',
            }}
          >
            <span className="mono-label" style={{ fontSize: 10, color: '#8ea3ba' }}>
              Breach Duration
            </span>
            <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color: '#38bdf8' }}>
              {breachDurationMin} min
            </span>
          </label>
          <input
            id={`breach-dur-${resolvedLakeId}`}
            type="range"
            min={10}
            max={240}
            step={5}
            value={breachDurationMin}
            onChange={(e) => setBreachDurationMin(Number(e.target.value))}
            aria-label="Breach duration in minutes"
            style={{ width: '100%', accentColor: '#38bdf8', cursor: 'pointer' }}
          />
        </div>
      </div>

      {/* Peak Discharge Ensemble KPIs */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
          gap: 10,
          marginBottom: 16,
        }}
      >
        {[
          { label: 'Froehlich (2008)', val: peak.froehlich_m3s, color: '#38bdf8' },
          { label: 'Costa (1985)', val: peak.costa_m3s, color: '#a78bfa' },
          { label: "Walder & O'Connor", val: peak.walder_oconnor_m3s, color: '#fbbf24' },
          { label: 'Ensemble Peak (Qp)', val: peak.ensemble_m3s, color: '#f472b6' },
        ].map((kpi) => (
          <div
            key={kpi.label}
            style={{
              padding: '10px 12px',
              borderRadius: 10,
              background: 'rgba(255, 255, 255, 0.03)',
              border: '1px solid rgba(103, 232, 249, 0.14)',
            }}
          >
            <div className="mono-label" style={{ fontSize: 9.5, color: '#8ea3ba', marginBottom: 4 }}>
              {kpi.label}
            </div>
            <div
              style={{
                fontFamily: 'var(--font-mono)',
                fontSize: 16,
                fontWeight: 700,
                color: kpi.color,
              }}
            >
              {kpi.val !== undefined ? `${Number(kpi.val).toLocaleString()} m³/s` : '—'}
            </div>
          </div>
        ))}
        <div
          style={{
            padding: '10px 12px',
            borderRadius: 10,
            background: 'rgba(255, 255, 255, 0.03)',
            border: '1px solid rgba(103, 232, 249, 0.14)',
          }}
        >
          <div className="mono-label" style={{ fontSize: 9.5, color: '#8ea3ba', marginBottom: 4 }}>
            Outburst Volume
          </div>
          <div
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 16,
              fontWeight: 700,
              color: '#5eead4',
            }}
          >
            {volumeMillionM3 !== '—' ? `${volumeMillionM3} M m³` : '—'}
          </div>
        </div>
      </div>

      {/* Playback Bar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          flexWrap: 'wrap',
          padding: '10px 14px',
          borderRadius: 12,
          background: 'rgba(6, 16, 26, 0.85)',
          border: '1px solid rgba(94, 234, 212, 0.2)',
          marginBottom: 16,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <button
            type="button"
            onClick={() => {
              if (!activePlaying && activeTimeMin >= 240) {
                setTime(0);
              }
              setPlaying(!activePlaying);
            }}
            style={{
              padding: '7px 14px',
              borderRadius: 8,
              border: 'none',
              background: activePlaying
                ? 'linear-gradient(135deg, #f5a524, #d97706)'
                : 'linear-gradient(135deg, #5eead4, #38bdf8)',
              color: '#06131a',
              fontSize: 12.5,
              fontWeight: 800,
              cursor: 'pointer',
            }}
          >
            {activePlaying ? 'Pause' : 'Play'}
          </button>
          <button
            type="button"
            onClick={() => {
              setPlaying(false);
              setTime(0);
            }}
            style={{
              padding: '7px 11px',
              borderRadius: 8,
              border: '1px solid rgba(103, 232, 249, 0.22)',
              background: 'transparent',
              color: '#cbd5e1',
              fontSize: 12,
              fontWeight: 700,
              cursor: 'pointer',
            }}
          >
            Reset
          </button>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          {SPEED_OPTIONS.map((spd) => (
            <button
              key={spd}
              type="button"
              aria-pressed={playbackSpeed === spd}
              onClick={() => setPlaybackSpeed(spd)}
              style={{
                padding: '5px 9px',
                borderRadius: 7,
                border:
                  playbackSpeed === spd
                    ? '1px solid rgba(56, 189, 248, 0.6)'
                    : '1px solid rgba(103, 232, 249, 0.16)',
                background: playbackSpeed === spd ? 'rgba(56, 189, 248, 0.2)' : 'transparent',
                color: playbackSpeed === spd ? '#38bdf8' : '#8ea3ba',
                fontSize: 11.5,
                fontWeight: 700,
                cursor: 'pointer',
              }}
            >
              {spd}x
            </button>
          ))}
        </div>

        <div style={{ flex: 1, minWidth: 160, display: 'flex', alignItems: 'center', gap: 10 }}>
          <input
            type="range"
            min={0}
            max={240}
            step={1}
            value={Math.round(activeTimeMin)}
            onChange={(e) => {
              setPlaying(false);
              setTime(Number(e.target.value));
            }}
            aria-label="Simulation timeline scrubber in minutes"
            style={{ flex: 1, accentColor: '#f472b6', cursor: 'pointer' }}
          />
          <span
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 12,
              fontWeight: 700,
              color: '#eaf4f8',
              whiteSpace: 'nowrap',
            }}
          >
            T+{Math.round(activeTimeMin)}m · {Math.round(currentDischargeM3s).toLocaleString()} m³/s
          </span>
        </div>
      </div>

      {/* Charts */}
      {simData && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
            gap: 14,
            marginBottom: 16,
          }}
        >
          {/* Outburst Hydrograph Q(t) */}
          <div
            style={{
              padding: '12px 14px',
              borderRadius: 12,
              background: 'rgba(255, 255, 255, 0.025)',
              border: '1px solid rgba(103, 232, 249, 0.14)',
              minWidth: 0,
            }}
          >
            <div className="mono-label" style={{ fontSize: 10, color: '#f472b6', marginBottom: 8 }}>
              Outburst Hydrograph Q(t) · 0–240 min
            </div>
            <ResponsiveContainer width="100%" height={195} minWidth={0}>
              <AreaChart data={simData.hydrograph || []} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="breachHydroGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#ec4899" stopOpacity={0.55} />
                    <stop offset="95%" stopColor="#ec4899" stopOpacity={0.04} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(103,232,249,0.12)" />
                <XAxis
                  dataKey="time_min"
                  type="number"
                  domain={[0, 240]}
                  tickCount={7}
                  tick={{ fill: '#8ea3ba', fontSize: 11 }}
                  unit="m"
                />
                <YAxis tick={{ fill: '#8ea3ba', fontSize: 11 }} width={56} />
                <Tooltip
                  contentStyle={{
                    background: '#0c1826',
                    border: '1px solid rgba(103,232,249,0.25)',
                    borderRadius: 10,
                    color: '#eaf4f8',
                    fontSize: 12,
                  }}
                  formatter={(value) => [`${Number(value).toLocaleString()} m³/s`, 'Discharge']}
                  labelFormatter={(label) => `T+${label} min`}
                />
                <Area
                  type="monotone"
                  dataKey="discharge_m3s"
                  name="Discharge (m³/s)"
                  stroke="#ec4899"
                  strokeWidth={2.4}
                  fill="url(#breachHydroGrad)"
                  isAnimationActive={false}
                />
                <ReferenceLine
                  x={activeTimeMin}
                  stroke="#5eead4"
                  strokeWidth={2}
                  strokeDasharray="4 4"
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>

          {/* Longitudinal Elevation & Stage Profile */}
          <div
            style={{
              padding: '12px 14px',
              borderRadius: 12,
              background: 'rgba(255, 255, 255, 0.025)',
              border: '1px solid rgba(103, 232, 249, 0.14)',
              minWidth: 0,
            }}
          >
            <div className="mono-label" style={{ fontSize: 10, color: '#38bdf8', marginBottom: 8 }}>
              Longitudinal Elevation &amp; Flood Stage Profile
            </div>
            <ResponsiveContainer width="100%" height={195} minWidth={0}>
              <ComposedChart data={profileData} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(103,232,249,0.12)" />
                <XAxis
                  dataKey="distance_km"
                  type="number"
                  domain={[0, 'dataMax']}
                  tick={{ fill: '#8ea3ba', fontSize: 11 }}
                  unit=" km"
                />
                <YAxis domain={['auto', 'auto']} tick={{ fill: '#8ea3ba', fontSize: 11 }} width={52} />
                <Tooltip
                  contentStyle={{
                    background: '#0c1826',
                    border: '1px solid rgba(103,232,249,0.25)',
                    borderRadius: 10,
                    color: '#eaf4f8',
                    fontSize: 12,
                  }}
                  labelFormatter={(label, payload) => {
                    const ptName = payload?.[0]?.payload?.label;
                    return ptName ? `${ptName} (${label} km)` : `${label} km`;
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="flood_surface_m"
                  name="Flood Crest (m)"
                  stroke="#38bdf8"
                  strokeWidth={2}
                  fill="rgba(56, 189, 248, 0.22)"
                  isAnimationActive={false}
                />
                <Area
                  type="monotone"
                  dataKey="elevation_m"
                  name="Valley Bed (m)"
                  stroke="#5eead4"
                  strokeWidth={2}
                  fill="rgba(15, 23, 42, 0.72)"
                  isAnimationActive={false}
                />
                <ReferenceLine
                  x={wavefrontDistanceKm}
                  stroke="#f472b6"
                  strokeWidth={2}
                  strokeDasharray="4 4"
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* Downstream Nodes ETA Table */}
      {simData?.downstream_nodes?.length > 0 && (
        <div
          style={{
            borderRadius: 12,
            border: '1px solid rgba(103, 232, 249, 0.14)',
            background: 'rgba(255, 255, 255, 0.02)',
            overflowX: 'auto',
          }}
        >
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid rgba(103, 232, 249, 0.14)', color: '#8ea3ba' }}>
                <th style={{ textAlign: 'left', padding: '9px 12px' }}>Downstream Node</th>
                <th style={{ textAlign: 'right', padding: '9px 10px' }}>Dist</th>
                <th style={{ textAlign: 'right', padding: '9px 10px' }}>ETA</th>
                <th style={{ textAlign: 'right', padding: '9px 10px' }}>Recession</th>
                <th style={{ textAlign: 'right', padding: '9px 10px' }}>Atten. Q</th>
                <th style={{ textAlign: 'right', padding: '9px 10px' }}>Stage Rise</th>
                <th style={{ textAlign: 'center', padding: '9px 10px' }}>Severity</th>
                <th style={{ textAlign: 'center', padding: '9px 12px' }}>Wave Status</th>
              </tr>
            </thead>
            <tbody>
              {simData.downstream_nodes.map((node) => {
                const isInundating =
                  activeTimeMin >= Number(node.eta_min) &&
                  activeTimeMin <= Number(node.recession_min);
                const isReceded = activeTimeMin > Number(node.recession_min);
                const sevColor = SEVERITY_COLORS[node.severity] || '#38bdf8';

                return (
                  <tr
                    key={`${node.name}-${node.distance_km}`}
                    style={{
                      borderBottom: '1px solid rgba(255, 255, 255, 0.05)',
                      background: isInundating ? 'rgba(240, 67, 58, 0.12)' : 'transparent',
                    }}
                  >
                    <td style={{ padding: '9px 12px', fontWeight: 700, color: '#eaf4f8' }}>
                      {node.name}
                    </td>
                    <td style={{ textAlign: 'right', padding: '9px 10px', fontFamily: 'var(--font-mono)' }}>
                      {node.distance_km} km
                    </td>
                    <td style={{ textAlign: 'right', padding: '9px 10px', fontFamily: 'var(--font-mono)', color: '#5eead4' }}>
                      {node.eta_min} min
                    </td>
                    <td style={{ textAlign: 'right', padding: '9px 10px', fontFamily: 'var(--font-mono)', color: '#8ea3ba' }}>
                      {node.recession_min} min
                    </td>
                    <td style={{ textAlign: 'right', padding: '9px 10px', fontFamily: 'var(--font-mono)' }}>
                      {Number(node.attenuated_q_m3s).toLocaleString()} m³/s
                    </td>
                    <td style={{ textAlign: 'right', padding: '9px 10px', fontFamily: 'var(--font-mono)', color: '#38bdf8' }}>
                      +{node.stage_rise_m} m
                    </td>
                    <td style={{ textAlign: 'center', padding: '9px 10px' }}>
                      <span
                        style={{
                          padding: '2px 8px',
                          borderRadius: 999,
                          fontSize: 11,
                          fontWeight: 700,
                          color: sevColor,
                          background: `${sevColor}20`,
                          border: `1px solid ${sevColor}50`,
                        }}
                      >
                        {node.severity}
                      </span>
                    </td>
                    <td style={{ textAlign: 'center', padding: '9px 12px' }}>
                      <span
                        className="mono-label"
                        style={{
                          fontSize: 10,
                          fontWeight: 700,
                          padding: '3px 8px',
                          borderRadius: 999,
                          color: isInundating ? '#fff' : isReceded ? '#2dd48e' : '#8ea3ba',
                          background: isInundating
                            ? '#f0433a'
                            : isReceded
                              ? 'rgba(45,212,142,0.16)'
                              : 'rgba(142,163,186,0.14)',
                        }}
                      >
                        {isInundating ? 'INUNDATING' : isReceded ? 'Receded' : 'Pending'}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default BreachSimulatorPanel;
