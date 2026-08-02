import { motion } from 'framer-motion';
import { IconMountain, IconSatellite, IconGlobe, IconPulseDot, IconShield, IconInfo } from './Icons';
import {
  easeOut,
  fadeUp,
  staggerContainer,
  cardItem,
  springSoft,
  springSnappy,
} from '../motion';

const CONTOUR_PATHS = [
  'M -50,620 C 120,560 260,600 380,520 C 520,430 600,300 720,260 C 860,214 980,240 1100,180 C 1220,124 1300,60 1450,20',
  'M -50,660 C 140,610 300,650 420,570 C 560,478 650,340 780,300 C 920,256 1020,286 1140,222 C 1260,164 1340,96 1450,58',
  'M -50,700 C 160,660 340,700 460,620 C 610,524 700,384 840,344 C 980,300 1060,332 1180,266 C 1300,206 1370,132 1450,96',
  'M -50,740 C 180,708 380,748 500,672 C 660,570 750,428 900,388 C 1040,346 1100,378 1220,312 C 1330,250 1400,172 1450,134',
  'M -50,780 C 200,756 420,794 540,724 C 710,616 800,472 960,432 C 1100,392 1140,424 1260,358 C 1360,296 1420,214 1450,172',
];

const RIDGE_PATH = 'M -50,560 C 100,500 240,520 360,440 C 500,346 590,224 710,190 C 850,148 960,182 1080,116 C 1200,54 1300,-10 1450,-40';

function ContourField({ opacity = 0.5 }) {
  return (
    <svg
      viewBox="0 0 1400 800"
      preserveAspectRatio="none"
      style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', opacity }}
    >
      <defs>
        <linearGradient id="contourFade" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#5eead4" stopOpacity="0.9" />
          <stop offset="100%" stopColor="#38bdf8" stopOpacity="0.15" />
        </linearGradient>
      </defs>
      <g style={{ animation: 'contour-drift 40s linear infinite alternate' }}>
        <path d={RIDGE_PATH} fill="none" stroke="url(#contourFade)" strokeWidth="1.2" opacity="0.55" />
        {CONTOUR_PATHS.map((d, i) => (
          <path key={i} d={d} fill="none" stroke="url(#contourFade)" strokeWidth="1" opacity={0.75 - i * 0.11} />
        ))}
      </g>
    </svg>
  );
}

function StatTile({ value, label, sub, accent }) {
  return (
    <motion.div
      variants={cardItem}
      whileHover={{ y: -2 }}
      style={{ textAlign: 'left', padding: '4px 0' }}
    >
      <div className="mono-label" style={{ fontSize: 10.5, color: '#5b7690', marginBottom: 8 }}>{label}</div>
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 28, fontWeight: 700, color: accent, lineHeight: 1, marginBottom: 6 }}>
        {value}
      </div>
      <div style={{ fontSize: 12.5, color: '#8ea3ba' }}>{sub}</div>
    </motion.div>
  );
}

function FeatureCard({ icon: Icon, title, desc }) {
  return (
    <motion.div
      variants={cardItem}
      whileHover={{
        y: -6,
        borderColor: 'rgba(94,234,212,0.35)',
        boxShadow: '0 12px 32px rgba(56,189,248,0.12)',
      }}
      transition={springSoft}
      style={{
        padding: '18px 16px',
        borderRadius: 14,
        background: 'rgba(12,24,38,0.55)',
        border: '1px solid rgba(103,232,249,0.12)',
        cursor: 'default',
      }}
    >
      <motion.div
        whileHover={{ rotate: [0, -6, 6, 0], scale: 1.06 }}
        transition={{ duration: 0.45 }}
        style={{
          width: 36, height: 36, borderRadius: 10, marginBottom: 12,
          background: 'rgba(94,234,212,0.1)', border: '1px solid rgba(94,234,212,0.2)',
          display: 'flex', alignItems: 'center', justifyContent: 'center'
        }}
      >
        <Icon size={17} color="#5eead4" />
      </motion.div>
      <div style={{ fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: 14, color: '#eaf4f8', marginBottom: 6 }}>
        {title}
      </div>
      <div style={{ fontSize: 12.5, color: '#8ea3ba', lineHeight: 1.5 }}>
        {desc}
      </div>
    </motion.div>
  );
}

export default function LandingPage({ onLaunch }) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.5 }}
      style={{
        minHeight: '100vh',
        background: 'var(--void, #060b13)',
        color: 'var(--text-hi, #eaf4f8)',
        display: 'flex',
        flexDirection: 'column',
        fontFamily: 'var(--font-body)',
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      {/* Ambient radial glow */}
      <motion.div
        animate={{
          opacity: [0.75, 1, 0.75],
          scale: [1, 1.03, 1],
        }}
        transition={{ duration: 8, repeat: Infinity, ease: 'easeInOut' }}
        style={{
          position: 'absolute', inset: 0, pointerEvents: 'none',
          background: 'radial-gradient(ellipse 60% 45% at 85% 8%, rgba(56,189,248,0.14) 0%, transparent 60%), radial-gradient(ellipse 50% 40% at 8% 92%, rgba(94,234,212,0.10) 0%, transparent 60%)',
        }}
      />

      <ContourField opacity={0.55} />

      {/* Fine grid overlay */}
      <div style={{
        position: 'absolute', inset: 0, pointerEvents: 'none', opacity: 0.35,
        backgroundImage: 'linear-gradient(rgba(103,232,249,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(103,232,249,0.05) 1px, transparent 1px)',
        backgroundSize: '46px 46px',
        maskImage: 'radial-gradient(ellipse 70% 60% at 50% 20%, black 20%, transparent 75%)',
      }} />

      {/* Telemetry ticker */}
      <motion.div
        initial={{ y: -20, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ duration: 0.45, ease: easeOut }}
        className="mono-label"
        style={{
          position: 'relative', zIndex: 10, display: 'flex', alignItems: 'center', gap: 10,
          padding: '9px 56px', fontSize: 10.5, color: '#5b7690',
          borderBottom: '1px solid var(--line, rgba(103,232,249,0.12))',
          background: 'rgba(6,11,19,0.6)',
          flexWrap: 'wrap',
        }}
      >
        <motion.span
          animate={{ scale: [1, 1.2, 1], opacity: [1, 0.7, 1] }}
          transition={{ duration: 1.8, repeat: Infinity }}
          style={{ display: 'inline-flex' }}
        >
          <IconPulseDot color="#2dd48e" />
        </motion.span>
        <span style={{ color: '#2dd48e' }}>LIVE</span>
        <span style={{ opacity: 0.4 }}>·</span>
        <span>GLOF RISK INTELLIGENCE</span>
        <span style={{ opacity: 0.4 }}>·</span>
        <span>SENTINEL-2 · WORLDPOP · NDWI</span>
        <span style={{ opacity: 0.4 }}>·</span>
        <span>GILGIT-BALTISTAN</span>
      </motion.div>

      {/* Navbar */}
      <motion.header
        initial={{ y: -16, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ duration: 0.5, delay: 0.08, ease: easeOut }}
        style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          padding: '20px 56px', position: 'relative', zIndex: 10,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <motion.div
            whileHover={{ rotate: 8, scale: 1.06 }}
            transition={springSnappy}
            className="hud-frame"
            style={{
              width: 44, height: 44, borderRadius: 12,
              background: 'linear-gradient(135deg, #101d2c, #0c1826)',
              border: '1px solid var(--line, rgba(103,232,249,0.14))',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
          >
            <IconMountain size={21} color="#5eead4" />
          </motion.div>
          <div>
            <div style={{ fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: 17, letterSpacing: '-0.01em' }}>GLOF Portal</div>
            <div className="mono-label" style={{ fontSize: 10, color: '#5eead4' }}>PNDDT · Disaster Digital Twin</div>
          </div>
        </div>

        <motion.button
          onClick={onLaunch}
          className="btn-3d"
          whileHover={{ scale: 1.04, borderColor: 'rgba(94,234,212,0.45)' }}
          whileTap={{ scale: 0.97 }}
          style={{
            background: 'transparent', color: '#eaf4f8',
            border: '1px solid var(--line, rgba(103,232,249,0.14))',
            padding: '11px 26px', borderRadius: 999, fontWeight: 600, fontSize: 13.5,
            cursor: 'pointer', fontFamily: 'var(--font-display)',
          }}
        >
          Enter Portal →
        </motion.button>
      </motion.header>

      {/* Hero */}
      <main style={{
        flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center',
        padding: '20px 56px 48px', maxWidth: 1180, margin: '0 auto', width: '100%',
        position: 'relative', zIndex: 10,
      }}>
        <motion.div
          variants={staggerContainer}
          initial="hidden"
          animate="show"
        >
          <motion.div
            variants={fadeUp}
            className="mono-label"
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 8,
              color: '#5eead4', fontSize: 11.5, marginBottom: 28, width: 'fit-content',
            }}
          >
            <IconGlobe size={13} color="#5eead4" />
            Gilgit-Baltistan Operational Coverage
          </motion.div>

          <motion.h1
            variants={fadeUp}
            style={{
              fontFamily: 'var(--font-display)', fontSize: 'clamp(36px, 5.4vw, 60px)',
              fontWeight: 700, lineHeight: 1.08, margin: '0 0 22px', letterSpacing: '-0.025em',
              maxWidth: 820,
            }}
          >
            Every glacial lake,<br />
            watched before it{' '}
            <span style={{
              background: 'linear-gradient(90deg, #5eead4, #38bdf8)',
              WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent',
            }}>
              breaks the valley.
            </span>
          </motion.h1>

          <motion.p
            variants={fadeUp}
            style={{
              fontSize: 16.5, color: '#8ea3ba', maxWidth: 560, lineHeight: 1.7, marginBottom: 36,
            }}
          >
            Operational GLOF intelligence for Northern Pakistan — live satellite detection,
            population exposure, multi-year lake change, danger zones, and field-ready reporting
            in one command surface.
          </motion.p>

          <motion.div
            variants={fadeUp}
            style={{ display: 'flex', gap: 16, alignItems: 'center', marginBottom: 48, flexWrap: 'wrap' }}
          >
            <motion.button
              onClick={onLaunch}
              className="btn-3d"
              whileHover={{
                scale: 1.04,
                boxShadow: '0 12px 40px rgba(94,234,212,0.4)',
              }}
              whileTap={{ scale: 0.97 }}
              style={{
                background: 'linear-gradient(135deg, #5eead4, #38bdf8)', color: '#06131a',
                border: 'none', padding: '15px 32px', borderRadius: 12, fontWeight: 700,
                fontSize: 15, cursor: 'pointer', fontFamily: 'var(--font-display)',
                boxShadow: '0 8px 32px rgba(94,234,212,0.28)',
              }}
            >
              Launch Command Dashboard →
            </motion.button>
            <div style={{ display: 'flex', alignItems: 'center', gap: 9, color: '#5b7690', fontSize: 13 }}>
              <IconSatellite size={16} color="#5b7690" />
              GEE · Sentinel-2 · WorldPop connected
            </div>
          </motion.div>

          {/* Core capabilities */}
          <motion.div
            variants={staggerContainer}
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
              gap: 12,
              marginBottom: 36,
              maxWidth: 960,
            }}
          >
            <FeatureCard
              icon={IconSatellite}
              title="Satellite Detection"
              desc="Sentinel-2 NDWI lake detection via Google Earth Engine"
            />
            <FeatureCard
              icon={IconInfo}
              title="Population Exposure"
              desc="WorldPop-based danger & warning zone estimates"
            />
            <FeatureCard
              icon={IconShield}
              title="Historical Analysis"
              desc="2015–2025 area trends and live satellite thumbnails"
            />
            <FeatureCard
              icon={IconGlobe}
              title="Risk Intelligence"
              desc="Auto risk scoring, naming, analytics & export tools"
            />
          </motion.div>

          {/* Telemetry panel */}
          <motion.div
            variants={fadeUp}
            className="hud-frame"
            whileHover={{ borderColor: 'rgba(94,234,212,0.28)' }}
            style={{
              display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 24,
              padding: '28px 32px', background: 'rgba(12,24,38,0.7)', backdropFilter: 'blur(14px)',
              border: '1px solid var(--line, rgba(103,232,249,0.14))', borderRadius: 16, maxWidth: 960,
            }}
          >
            <StatTile value="GEE" label="Earth Engine" sub="Live NDWI + WorldPop" accent="#5eead4" />
            <StatTile value="10yr" label="History Window" sub="2015–2025 composites" accent="#38bdf8" />
            <StatTile value="Zones" label="Exposure Layers" sub="Danger + Warning buffers" accent="#5eead4" />
            <StatTile value="GB" label="Coverage" sub="Gilgit-Baltistan focus" accent="#38bdf8" />
          </motion.div>
        </motion.div>
      </main>

      {/* Footer */}
      <motion.footer
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.6, duration: 0.5 }}
        style={{
          padding: '16px 56px', borderTop: '1px solid var(--line, rgba(103,232,249,0.12))',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          fontSize: 12.5, color: '#5b7690', position: 'relative', zIndex: 10, flexWrap: 'wrap', gap: 10,
        }}
      >
        <div className="mono-label" style={{ fontWeight: 600 }}>GLOF PORTAL · PNDDT / IST</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <motion.span
            animate={{ opacity: [1, 0.4, 1] }}
            transition={{ duration: 2, repeat: Infinity }}
            style={{ display: 'inline-flex' }}
          >
            <IconPulseDot color="#2dd48e" />
          </motion.span>
          System Operational
        </div>
      </motion.footer>
    </motion.div>
  );
}
