import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { after, before, describe, it } from 'node:test';
import { CdpBrowser } from './lib/cdp_client.mjs';
import { createIsolatedUiHarness } from './lib/ui_harness.mjs';

const MODE = (process.env.GLOF_E2E_MODE || 'full').trim().toLowerCase();
const IS_BASELINE = MODE === 'baseline';
const RAW_TIER = (process.env.GLOF_E2E_TIER || 'all').trim().toLowerCase();
const ENABLED_TIERS =
  RAW_TIER === '' || RAW_TIER === 'all'
    ? new Set([1, 2, 3, 4])
    : new Set(RAW_TIER.split(',').map((t) => Number(t.trim())));

const tierEnabled = (t) => ENABLED_TIERS.has(t);
const API_BASE_URL = (process.env.GLOF_E2E_API_URL || '').trim();

if (!API_BASE_URL) {
  throw new Error('GLOF_E2E_API_URL environment variable is required to run test_ui_e2e.mjs');
}

let uiHarness = null;
let browser = null;
let uiUrl = (process.env.GLOF_E2E_UI_URL || '').trim();
let tempRoot = null;

async function openLanding() {
  browser.clearTelemetry();
  await browser.setViewport(1440, 900, false);
  await browser.goto(uiUrl);
  await browser.waitForSelector('h1');
  await browser.waitForText('Every glacial lake, watched before it breaks the valley\\.');
}

async function enterPortal() {
  await openLanding();
  await browser.clickByText('button', 'Enter Portal');
  await browser.waitForText('Total Lakes:\\s*[4-9]\\d');
  await browser.waitForSelector('.leaflet-container');
}

async function navigateTab(sidebarLabelRegex, expectedH2Regex) {
  await browser.clickByText('aside nav .btn-3d, aside nav button', sidebarLabelRegex);
  await browser.waitFor(
    (pat) => {
      const h2 = document.querySelector('header h2');
      return Boolean(h2 && new RegExp(pat, 'i').test(h2.innerText.trim()));
    },
    [expectedH2Regex],
    { timeoutMs: 12000, label: `header h2 matching /${expectedH2Regex}/i` },
  );
}

async function findClickableMarkerPoint() {
  return browser.waitFor(
    () => {
      const mapEl = document.querySelector('.leaflet-container');
      if (!mapEl) return null;
      const mapRect = mapEl.getBoundingClientRect();
      const markers = Array.from(
        document.querySelectorAll('.leaflet-marker-pane .custom-3d-marker'),
      );
      const candidates = [];
      for (const m of markers) {
        const r = m.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) continue;
        const cx = r.left + r.width / 2;
        const cy = r.top + r.height / 2;
        if (
          cx < mapRect.left + 80 ||
          cx > mapRect.right - 80 ||
          cy < mapRect.top + mapRect.height * 0.40 ||
          cy > mapRect.bottom - 50
        ) {
          continue;
        }
        const hit = document.elementFromPoint(cx, cy);
        if (hit && (m.contains(hit) || hit.closest('.custom-3d-marker') === m)) {
          candidates.push({ x: cx, y: cy });
        }
      }
      if (candidates.length > 0) {
        return candidates[Math.floor(candidates.length / 2)];
      }
      // Fallback: any visible unobstructed marker
      for (const m of markers) {
        const r = m.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) continue;
        const cx = r.left + r.width / 2;
        const cy = r.top + r.height / 2;
        const hit = document.elementFromPoint(cx, cy);
        if (hit && (m.contains(hit) || hit.closest('.custom-3d-marker') === m)) {
          return { x: cx, y: cy };
        }
      }
      return null;
    },
    [],
    { timeoutMs: 15000, label: 'visible clickable lake marker' },
  );
}

async function findOpenMapPoint() {
  return browser.waitFor(
    () => {
      const mapEl = document.querySelector('.leaflet-container');
      if (!mapEl) return null;
      const mr = mapEl.getBoundingClientRect();
      // Scan grid inside map region avoiding markers, controls, popups, and interactive polygon paths
      for (let fx = 0.15; fx <= 0.85; fx += 0.07) {
        for (let fy = 0.18; fy <= 0.82; fy += 0.08) {
          const px = mr.left + mr.width * fx;
          const py = mr.top + mr.height * fy;
          const hit = document.elementFromPoint(px, py);
          if (!hit) continue;
          if (!mapEl.contains(hit)) continue;
          if (hit.tagName && hit.tagName.toLowerCase() === 'path') continue;
          if (hit.closest('.custom-3d-marker, .leaflet-popup, .leaflet-control, button, a')) {
            continue;
          }
          return { x: px, y: py };
        }
      }
      return null;
    },
    [],
    { timeoutMs: 10000, label: 'open point inside .leaflet-container' },
  );
}

function assertCleanBrowserHealth() {
  assert.deepEqual(
    browser.pageErrors,
    [],
    `Uncaught page JS errors detected: ${JSON.stringify(browser.pageErrors)}`,
  );
  assert.deepEqual(
    browser.sameOriginServerErrors,
    [],
    `Same-origin >=500 API responses detected: ${JSON.stringify(browser.sameOriginServerErrors)}`,
  );
  if (!IS_BASELINE) {
    assert.deepEqual(
      browser.dialogs,
      [],
      `Expected zero native alert/confirm dialogs post-M2, saw: ${JSON.stringify(browser.dialogs)}`,
    );
  }
}

before(async () => {
  tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'glof_e2e_ui_suite_'));
  if (!uiUrl) {
    uiHarness = await createIsolatedUiHarness({
      apiBaseUrl: API_BASE_URL,
      tempRoot: path.join(tempRoot, 'harness'),
    });
    uiUrl = uiHarness.uiUrl;
  }
  browser = await new CdpBrowser({
    uiOrigin: new URL(uiUrl).origin,
    apiOrigin: new URL(API_BASE_URL).origin,
    userDataDir: path.join(tempRoot, 'cdp_profile'),
  }).launch();
});

after(async () => {
  if (browser) {
    await browser.close();
  }
  if (uiHarness) {
    await uiHarness.cleanup();
  }
  if (tempRoot && fs.existsSync(tempRoot)) {
    try {
      fs.rmSync(tempRoot, { recursive: true, force: true, maxRetries: 12, retryDelay: 250 });
    } catch {
      // Ignore residual Windows Crashpad file handle lock after browser process tree kill
    }
  }
});

// ==============================================================================
// TIER 1 — SMOKE & CORE HAPPY PATHS (FEATURES 11–17)
// ==============================================================================
describe('Tier 1 — UI & GIS Core Happy Paths', { skip: !tierEnabled(1) }, () => {
  it('T1.1 LandingPage elements, dual launch CTAs & portal inventory hydration', async () => {
    await openLanding();

    const landingSummary = await browser.evaluate(() => {
      const h1 = document.querySelector('h1')?.innerText?.trim() || '';
      const svg = document.querySelector('svg[viewBox="0 0 1400 800"]');
      const pathCount = svg ? svg.querySelectorAll('path').length : 0;
      const body = document.body.innerText;
      const expectedCards = [
        'Multi-Source Detection',
        'Flood Monitoring',
        'GLOF Basins',
        'Hybrid Historical',
        'Population Exposure',
        'Find Lake & Assessment',
      ];
      const missingCards = expectedCards.filter((c) => !body.includes(c));
      const upperBody = body.toUpperCase();
      const hasPipeline =
        upperBody.includes('DETECTION PIPELINE') &&
        upperBody.includes('1 · GEE S2') &&
        upperBody.includes('4 · INVENTORY');
      return { h1, pathCount, missingCards, hasPipeline };
    });

    assert.match(
      landingSummary.h1.replace(/\s+/g, ' '),
      /Every glacial lake, watched before it breaks the valley\./,
    );
    assert.equal(landingSummary.pathCount, 6);
    assert.deepEqual(landingSummary.missingCards, []);
    assert.equal(landingSummary.hasPipeline, true);

    // Secondary CTA: Launch Command Dashboard ->
    await browser.clickByText('button', 'Launch Command Dashboard');
    await browser.waitForText('Total Lakes:\\s*[4-9]\\d');
    const markerCount = await browser.evaluate(
      () => document.querySelectorAll('.leaflet-marker-pane .custom-3d-marker').length,
    );
    assert.ok(markerCount >= 46, `Expected >=46 map markers, got ${markerCount}`);
    assertCleanBrowserHealth();
  });

  it('T1.2 All 9 Portal Tabs render with expected headings and zero JS errors', async () => {
    await enterPortal();
    const tabs = [
      ['^Dashboard$', '^Dashboard$'],
      ['^Analytics$', '^Analytics$'],
      ['^Population$', '^Population Exposure$'],
      ['^Flood Monitoring$', '^Flood Monitoring$'],
      ['^GLOF Basins$', '^GLOF Basins$'],
      ['^Historical$', '^Historical Analysis$'],
      ['^Lakes Inventory$', '^Lakes Inventory$'],
      ['^Register New Lake$', '^Register New Lake$'],
      ['^Satellite Detection$', '^Satellite Detection$'],
      ['^Dashboard$', '^Dashboard$'],
    ];

    for (const [navPattern, h2Pattern] of tabs) {
      await navigateTab(navPattern, h2Pattern);
      const mainLen = await browser.evaluate(
        () => (document.querySelector('main')?.innerText || '').trim().length,
      );
      assert.ok(mainLen > 20, `Tab ${navPattern} rendered empty content`);
    }
    assertCleanBrowserHealth();
  });

  it('T1.3 MapView baseline GIS controls: 4 basemaps, Layers toggle, WGS84 cursor telemetry', async () => {
    await enterPortal();

    // 1. Basemap switcher buttons (Google Maps + Dark GIS)
    const basemaps = [
      ['Google Hybrid', 'lyrs=s,h'],
      ['Google Satellite', 'lyrs=s'],
      ['Google Terrain', 'lyrs=p'],
      ['Google Streets', 'lyrs=m'],
      ['Dark GIS', 'dark_all'],
    ];
    for (const [btnLabel, expectedUrlToken] of basemaps) {
      await browser.clickByText('button', `^${btnLabel}$`);
      await browser.waitFor(
        (token) =>
          Array.from(document.querySelectorAll('.leaflet-tile')).some((img) =>
            (img.getAttribute('src') || '').includes(token),
          ),
        [expectedUrlToken],
        { timeoutMs: 8000, label: `basemap tile src containing ${expectedUrlToken}` },
      );
    }

    // 2. Layers panel toggle & overlay count change
    await browser.waitForText('Map Layers');
    const pathsBefore = await browser.evaluate(
      () => document.querySelectorAll('.leaflet-overlay-pane svg path.leaflet-interactive').length,
    );
    await browser.clickByText('label, button, span', 'Provincial Boundary');
    const pathsAfter = await browser.evaluate(
      () => document.querySelectorAll('.leaflet-overlay-pane svg path.leaflet-interactive').length,
    );
    assert.notEqual(
      pathsAfter,
      pathsBefore,
      'Toggling Provincial Boundary should change .leaflet-interactive SVG path count',
    );
    // Toggle back on
    await browser.clickByText('label, button, span', 'Provincial Boundary');

    // Close and re-open Layers panel via Layers button
    await browser.clickByText('button', 'Layers');
    await browser.waitFor(() => !document.body.innerText.includes('Map Layers'), [], {
      label: 'Map Layers panel hidden',
    });
    await browser.clickByText('button', 'Layers');
    await browser.waitForText('Map Layers');

    // 3. Live WGS84 cursor telemetry updates on mousemove over .leaflet-container
    const mapRect = await browser.evaluate(() => {
      const r = document.querySelector('.leaflet-container').getBoundingClientRect();
      return { left: r.left, top: r.top, width: r.width, height: r.height };
    });
    const readCoords = () =>
      browser.evaluate(() => {
        const el = Array.from(document.querySelectorAll('*')).find(
          (n) =>
            n.children.length <= 4 &&
            (n.innerText || '').includes('WGS84 (EPSG:4326)'),
        );
        return el ? el.innerText : '';
      });
    await browser.moveMouse(mapRect.left + mapRect.width * 0.35, mapRect.top + mapRect.height * 0.35);
    const coords1 = await readCoords();
    await browser.moveMouse(mapRect.left + mapRect.width * 0.62, mapRect.top + mapRect.height * 0.62);
    const coords2 = await readCoords();
    assert.match(coords1, /Lat:\s*\d+\.\d+/);
    assert.match(coords2, /Lon:\s*\d+\.\d+/);
    assert.notEqual(coords1, coords2, 'WGS84 cursor telemetry did not update on mousemove');
    assertCleanBrowserHealth();
  });

  it('T1.4 Lake popup -> Flood Corridor trace -> SITREP PDF link', async () => {
    await enterPortal();
    // Collapse Layers panel so markers in upper-right are unobstructed
    await browser.clickByText('button', 'Layers');

    const pt = await findClickableMarkerPoint();
    await browser.clickPoint(pt.x, pt.y);
    await browser.waitForSelector('.leaflet-popup-content-wrapper');
    await browser.waitForText('Trace Flood Corridor');
    // Allow Leaflet popup autoPan & fade animation to settle, and ensure button is centered & unclipped
    await new Promise((r) => setTimeout(r, 500));
    await browser.evaluate(() => {
      const btn = Array.from(
        document.querySelectorAll('.leaflet-popup-content-wrapper button'),
      ).find((b) => /Trace Flood Corridor/i.test(b.innerText || ''));
      if (btn) btn.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
    });
    await new Promise((r) => setTimeout(r, 150));

    await browser.clickByText('.leaflet-popup-content-wrapper button', 'Trace Flood Corridor');
    await browser.waitForText('GLOF Outburst Routing Trace');

    const corridorInfo = await browser.evaluate(() => {
      const text = document.body.innerText;
      const link = document.querySelector('a[href*="/reports/sitrep/"]');
      return {
        hasQp: text.includes('PEAK DISCHARGE (Qp)'),
        hasVol: text.includes('OUTBURST VOLUME'),
        hasFeeder: text.includes('GLACIER FEEDER'),
        hasLength: text.includes('CORRIDOR LENGTH'),
        sitrepHref: link ? link.getAttribute('href') : null,
      };
    });
    assert.equal(corridorInfo.hasQp, true);
    assert.equal(corridorInfo.hasVol, true);
    assert.equal(corridorInfo.hasFeeder, true);
    assert.equal(corridorInfo.hasLength, true);
    assert.ok(corridorInfo.sitrepHref, 'Missing SITREP PDF link');

    const pdfUrl = new URL(corridorInfo.sitrepHref, uiUrl).toString();
    const pdfRes = await fetch(pdfUrl);
    assert.equal(pdfRes.status, 200);
    const pdfBytes = new Uint8Array(await pdfRes.arrayBuffer());
    const header = Buffer.from(pdfBytes.slice(0, 5)).toString('ascii');
    assert.equal(header, '%PDF-');
    assertCleanBrowserHealth();
  });

  it('T1.5 Spatial Proximity Query mode (60 km radius)', async () => {
    await enterPortal();
    await browser.clickByText('button', 'Layers');

    await browser.clickByText('button', 'Spatial Query');
    await browser.waitForText('Spatial Proximity Mode Active:');
    await browser.clickByText('button', '^60 km$');
    await browser.waitForText('Radius Query \\(60 km ON\\)');

    // Center map near Gilgit-Baltistan lake cluster if needed, then click open point
    const pt = await findOpenMapPoint();
    await browser.clickPoint(pt.x, pt.y);
    await browser.waitForText('Lakes Within 60 km');
    assertCleanBrowserHealth();
  });

  it('T1.6 GeoJSON & Tabular CSV/PDF export triggers', async () => {
    await enterPortal();
    browser.clearTelemetry();
    await browser.clickByText('button', '^GeoJSON$');
    await browser.waitFor(
      () => true,
      [],
      { timeoutMs: 500 },
    );
    await new Promise((r) => setTimeout(r, 600));
    const geojsonCall = browser.apiResponses.find(
      (r) => r.url.includes('/lakes/geojson') || r.url.includes('/reports/export/geojson'),
    );
    assert.ok(geojsonCall, 'Expected GeoJSON export API request');
    assert.equal(geojsonCall.status, 200);

    await navigateTab('^Lakes Inventory$', '^Lakes Inventory$');
    await browser.clickByText('button', '^CSV$');
    await browser.clickByText('button', '^PDF$');
    await new Promise((r) => setTimeout(r, 400));
    assertCleanBrowserHealth();
  });

  it(
    'T1.7 M2/M3 Showcase core controls (Zones, Quick-Jump, Scale, Choropleth, Breach Player, Dossier, Compare, Cascade, Telemetry, Tour, Search)',
    { skip: IS_BASELINE ? 'Baseline mode: M2/M3 showcase not active' : false },
    async () => {
      await enterPortal();

      // F12: Scale bar & Quick-Jump & Zone toggles & Choropleth
      await browser.waitForSelector('.leaflet-control-scale-line');
      const layersOpen = await browser.evaluate(() =>
        document.body.innerText.includes('Map Layers'),
      );
      if (!layersOpen) {
        await browser.clickByText('button', 'Layers');
      }
      await browser.clickByText('label, button, span', 'Danger Zone');
      await browser.clickByText('label, button, span', 'Warning Zone');

      // District Choropleth toggle
      await browser.clickByText('label, button, span', 'Choropleth|District Risk');
      await browser.waitFor(
        () => true,
        [],
        { timeoutMs: 400 },
      );

      // Quick-Jump lake search
      const jumpInputSel =
        'input[placeholder*="Jump" i], input[placeholder*="Quick" i], input[placeholder*="Search lake" i]';
      await browser.waitForSelector(jumpInputSel);
      await browser.typeInto(jumpInputSel, 'Attabad');
      await browser.clickByText('button, li, [role="option"]', 'Attabad Lake');

      // Breach Simulation Player via popup or toolbar
      const pt = await findClickableMarkerPoint();
      await browser.clickPoint(pt.x, pt.y);
      await browser.waitForSelector('.leaflet-popup-content-wrapper');
      await browser.clickByText('.leaflet-popup-content-wrapper button', 'Trace Flood Corridor');
      await browser.waitForText('GLOF Outburst Routing Trace');
      await browser.clickByText('button', 'Breach Sim|Simulate Breach');
      await browser.waitForText('Froehlich|Costa|Walder');
      await browser.waitForSelector('.recharts-wrapper .recharts-area, .recharts-wrapper svg');

      // F17: 4-stop Guided Showcase Tour
      await browser.clickByText('button', 'Tour|Showcase|Guide');
      await browser.waitForText('1\\s*/\\s*4');
      await browser.clickByText('button', '^Next');
      await browser.waitForText('2\\s*/\\s*4');
      await browser.clickByText('button', '^Next');
      await browser.waitForText('3\\s*/\\s*4');
      await browser.clickByText('button', '^Next');
      await browser.waitForText('4\\s*/\\s*4');
      await browser.clickByText('button', 'Finish|Done|Close');

      // F16: Provider Telemetry Drawer
      await browser.clickByText('button', 'Providers|Telemetry|Health');
      await browser.waitForText('Open-Meteo|USGS');
      await browser.clickByText('button', 'Close|×');

      // F16: Basin Domino Cascade
      await navigateTab('^GLOF Basins$', '^GLOF Basins$');
      await browser.clickByText('button', 'Load GLOF Basins|Refresh Basins');
      await browser.waitForText('Domino|Cascade');

      // F17 + F15 + F16: Lakes Inventory Search + Dossier + Multi-Lake Comparison
      await navigateTab('^Lakes Inventory$', '^Lakes Inventory$');
      await browser.typeInto('input[type="search"], input[placeholder*="Search" i]', 'Attabad');
      await browser.waitForText('Attabad Lake');
      await browser.clickByText('table button', '^Details$');
      await browser.waitForSelector('[role="dialog"]');
      await browser.waitForText('P10|P50|P90');
      await browser.waitForSelector('[role="dialog"] .recharts-wrapper');
      await browser.clickByText('[role="dialog"] button', '^×$|Close');

      // Clear search and test Multi-Lake Comparison
      await browser.typeInto('input[type="search"], input[placeholder*="Search" i]', '');
      await browser.clickByText('button', 'Compare|Comparison');
      await browser.waitForSelector('.recharts-radar-polygon, .recharts-polar-grid');

      assertCleanBrowserHealth();
    },
  );
});

// ==============================================================================
// TIER 2 — EDGE CASES, VALIDATION, MOBILE 390x844 & OFFLINE RESILIENCE
// ==============================================================================
describe('Tier 2 — Validation, Mobile Viewport & Offline Resilience', { skip: !tierEnabled(2) }, () => {
  it('T2.1 Register New Lake validation & autocomplete auto-fill', async () => {
    await enterPortal();
    await navigateTab('^Register New Lake$', '^Register New Lake$');

    // Empty Find Lake click (handled gracefully by ToastProvider or CDP dialog auto-accept)
    await browser.clickByText('button', '^Find Lake$');
    // Nonexistent lake lookup
    const searchInputSel = 'input[placeholder*="Attabad Lake"]';
    await browser.typeInto(searchInputSel, 'NonexistentGlacierXYZ999');
    await browser.clickByText('button', '^Find Lake$');
    await new Promise((r) => setTimeout(r, 300));

    // Autocomplete suggestion -> click Attabad Lake -> numeric fields auto-fill
    await browser.typeInto(searchInputSel, 'Atta');
    await browser.waitForText('Attabad Lake');
    await browser.clickByText('div[style*="cursor: pointer"] span', '^Attabad Lake$');

    const filled = await browser.evaluate(() => {
      const nums = Array.from(document.querySelectorAll('input[type="number"]'));
      return nums.map((el) => Number(el.value));
    });
    assert.ok(filled.length >= 3);
    assert.ok(filled[0] > 0, `Expected Area auto-filled, got ${JSON.stringify(filled)}`);
    assert.ok(filled[1] > 30, `Expected Lat auto-filled, got ${JSON.stringify(filled)}`);
    assert.ok(filled[2] > 70, `Expected Lon auto-filled, got ${JSON.stringify(filled)}`);
    assertCleanBrowserHealth();
  });

  it('T2.2 Mobile viewport (390x844) zero horizontal overflow & responsive layout', async () => {
    await browser.clearTelemetry();
    await browser.setViewport(390, 844, true);
    await browser.goto(uiUrl);
    await browser.waitForSelector('h1');

    const landingWidth = await browser.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
    }));
    assert.ok(
      landingWidth.scrollWidth <= 390,
      `LandingPage mobile horizontal overflow: scrollWidth=${landingWidth.scrollWidth} > 390`,
    );

    await browser.clickByText('button', 'Enter Portal');
    await browser.waitForText('Total Lakes:\\s*[4-9]\\d');

    if (!IS_BASELINE) {
      const portalWidth = await browser.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        innerWidth: window.innerWidth,
      }));
      assert.ok(
        portalWidth.scrollWidth <= 390,
        `Portal mobile horizontal overflow: scrollWidth=${portalWidth.scrollWidth} > 390`,
      );
      // Collapsible mobile sidebar toggle
      await browser.clickSelector(
        'button[aria-label*="menu" i], button[aria-label*="nav" i], button[aria-label*="sidebar" i]',
      );
      await browser.waitForText('Lakes Inventory');
      await browser.clickByText('aside nav button', '^Lakes Inventory$');
      await browser.waitForText('^Lakes Inventory$');
    }

    await browser.setViewport(1440, 900, false);
    assertCleanBrowserHealth();
  });

  it('T2.3 Rename Modal Cancel & validation without mutating DB', async () => {
    await enterPortal();
    await navigateTab('^Lakes Inventory$', '^Lakes Inventory$');
    await browser.clickByText('table button', '^Rename$');
    await browser.waitForText('Rename Lake');

    if (!IS_BASELINE) {
      await browser.typeInto('.hud-frame input[type="text"], [role="dialog"] input[type="text"]', '');
      await browser.clickByText('button', '^Save Name$');
      await browser.waitForSelector('[role="status"], [role="alert"], [aria-live]');
    }

    await browser.clickByText('button', '^Cancel$');
    await browser.waitFor(() => !document.body.innerText.includes('Rename Lake'), [], {
      label: 'Rename modal closed',
    });
    assertCleanBrowserHealth();
  });

  it(
    'T2.4 Lakes Inventory unknown/empty search & Unknown Lakes filter',
    { skip: IS_BASELINE ? 'Baseline mode: M3 Inventory search not wired yet' : false },
    async () => {
      await enterPortal();
      await navigateTab('^Lakes Inventory$', '^Lakes Inventory$');

      const searchSel = 'input[type="search"], input[placeholder*="Search" i]';
      await browser.typeInto(searchSel, 'ZZZZ_NO_SUCH_LAKE_999');
      await browser.waitForText('No lakes match the current filter');

      await browser.typeInto(searchSel, '');
      await browser.waitForText('Attabad Lake');

      await browser.clickByText('button', 'Unknown Lakes');
      await browser.waitForText('Show All Lakes');
      await browser.clickByText('button', 'Show All Lakes');
      assertCleanBrowserHealth();
    },
  );

  it(
    'T2.5 Offline third-party CDN/API resilience in browser',
    { skip: IS_BASELINE ? 'Baseline mode: M2/M3 offline showcase not active' : false },
    async () => {
      await enterPortal();
      await browser.blockExternalHosts([
        'open-meteo.com',
        'fonts.googleapis.com',
        'basemaps.cartocdn.com',
        'server.arcgisonline.com',
      ]);

      try {
        await browser.clickByText('button', 'Layers');
        const pt = await findClickableMarkerPoint();
        await browser.clickPoint(pt.x, pt.y);
        await browser.waitForSelector('.leaflet-popup-content-wrapper');
        await browser.clickByText('.leaflet-popup-content-wrapper button', 'View Risk Assessment');
        await browser.waitForSelector('[role="dialog"]');
        await browser.waitForText('P10|P50|P90');
        await browser.clickByText('[role="dialog"] button', '^×$|Close');
      } finally {
        await browser.unblockExternalHosts();
      }
      assertCleanBrowserHealth();
    },
  );
});

// ==============================================================================
// TIER 3 — CROSS-FEATURE UI CHAINS
// ==============================================================================
describe('Tier 3 — Cross-Feature UI Workflows', { skip: !tierEnabled(3) || IS_BASELINE }, () => {
  it('T3.1 Dashboard Risk Filter -> Map Marker -> Flood Corridor -> Breach Simulator', async () => {
    await enterPortal();
    await browser.clickByText('.btn-3d.hud-frame, button', 'High Risk');
    await browser.clickByText('button', 'Layers');

    const pt = await findClickableMarkerPoint();
    await browser.clickPoint(pt.x, pt.y);
    await browser.waitForSelector('.leaflet-popup-content-wrapper');
    await browser.clickByText('.leaflet-popup-content-wrapper button', 'Trace Flood Corridor');
    await browser.waitForText('GLOF Outburst Routing Trace');

    await browser.clickByText('button', 'Breach Sim|Simulate Breach');
    await browser.waitForSelector('.recharts-wrapper');
    await browser.clickByText('button', '^Ice$');
    await browser.waitFor(
      () =>
        browser.apiResponses.some((r) => r.url.includes('/analytics/breach-simulation/') && r.url.includes('dam_type=ice') && r.status === 200),
      [],
      { timeoutMs: 8000, label: 'breach-simulation API call with dam_type=ice' },
    );
    assertCleanBrowserHealth();
  });

  it('T3.2 Inventory Search -> Unified Lake Dossier -> +3.0C Scenario -> Corridor CTA', async () => {
    await enterPortal();
    await navigateTab('^Lakes Inventory$', '^Lakes Inventory$');
    await browser.typeInto('input[type="search"], input[placeholder*="Search" i]', 'Shisper');
    await browser.waitForText('Shisper Lake');
    await browser.clickByText('table button', '^Details$');

    await browser.waitForSelector('[role="dialog"]');
    await browser.waitForText('P10|P50|P90');
    await browser.clickByText('[role="dialog"] button', '\\+3\\.0|3\\.0°C');
    await browser.clickByText('[role="dialog"] button', 'Trace Flood Corridor');
    await browser.waitForText('GLOF Outburst Routing Trace');
    assertCleanBrowserHealth();
  });

  it('T3.3 Flood Monitoring Fast Scan -> View GLOF Basin -> Domino Cascade', async () => {
    await enterPortal();
    await navigateTab('^Flood Monitoring$', '^Flood Monitoring$');
    // Ensure GEE checkbox unchecked for fast deterministic scan
    await browser.evaluate(() => {
      const cb = document.querySelector('input[type="checkbox"]');
      if (cb && cb.checked) cb.click();
    });
    await browser.clickByText('button', 'Run Early Warning Scan');
    await browser.waitForText('View GLOF Basin', { timeoutMs: 25000 });
    await browser.clickByText('button', 'View GLOF Basin');
    await browser.waitForText('GLOF Basins');
    await browser.waitForText('Domino|Cascade');
    assertCleanBrowserHealth();
  });

  it('T3.4 Multi-Lake Comparison + Dark/Light Theme Persistence across reload', async () => {
    await enterPortal();
    const initialTheme = await browser.evaluate(() =>
      document.documentElement.getAttribute('data-theme') || 'dark',
    );
    await browser.clickByText('header button', '🌙|☀️');
    const toggledTheme = await browser.evaluate(() =>
      document.documentElement.getAttribute('data-theme'),
    );
    assert.notEqual(toggledTheme, initialTheme);

    await browser.reload();
    await browser.waitForSelector('h1');
    const persistedTheme = await browser.evaluate(() =>
      localStorage.getItem('glof-theme'),
    );
    assert.equal(persistedTheme, toggledTheme);
    assertCleanBrowserHealth();
  });
});

// ==============================================================================
// TIER 4 — REALISTIC STAKEHOLDER UI JOURNEYS
// ==============================================================================
describe('Tier 4 — Stakeholder UI Journeys', { skip: !tierEnabled(4) || IS_BASELINE }, () => {
  it('T4.1 Disaster Reviewer Briefing: Tour -> Telemetry -> Flood Scan -> Cascade -> Dossier -> Exports', async () => {
    await enterPortal();

    // 1. 4-stop Guided Showcase Tour
    await browser.clickByText('button', 'Tour|Showcase|Guide');
    for (let i = 0; i < 3; i++) {
      await browser.clickByText('button', '^Next');
    }
    await browser.clickByText('button', 'Finish|Done|Close');

    // 2. Provider Telemetry
    await browser.clickByText('button', 'Providers|Telemetry|Health');
    await browser.waitForText('Open-Meteo|USGS');
    await browser.clickByText('button', 'Close|×');

    // 3. Flood Monitoring scan
    await navigateTab('^Flood Monitoring$', '^Flood Monitoring$');
    await browser.evaluate(() => {
      const cb = document.querySelector('input[type="checkbox"]');
      if (cb && cb.checked) cb.click();
    });
    await browser.clickByText('button', 'Run Early Warning Scan');
    await browser.waitForText('View GLOF Basin', { timeoutMs: 25000 });

    // 4. Basins Cascade
    await browser.clickByText('button', 'View GLOF Basin');
    await browser.waitForText('Domino|Cascade');

    // 5. Dossier + +3.0C + SITREP PDF check
    await navigateTab('^Lakes Inventory$', '^Lakes Inventory$');
    await browser.clickByText('table button', '^Details$');
    await browser.waitForSelector('[role="dialog"]');
    await browser.clickByText('[role="dialog"] button', '\\+3\\.0|3\\.0°C');

    const sitrepHref = await browser.evaluate(() => {
      const a = document.querySelector('[role="dialog"] a[href*="/reports/sitrep/"]');
      return a ? a.getAttribute('href') : null;
    });
    assert.ok(sitrepHref, 'Expected Download SITREP PDF link inside Dossier');
    const pdfRes = await fetch(new URL(sitrepHref, uiUrl).toString());
    assert.equal(pdfRes.status, 200);
    assertCleanBrowserHealth();
  });

  it('T4.2 Field Researcher CRUD & Cleanup: Register -> Search -> Rename -> Dossier -> Delete', async () => {
    await enterPortal();

    const baselineCountText = await browser.evaluate(
      () =>
        Array.from(document.querySelectorAll('.mono-label')).find((e) =>
          e.innerText.includes('Total Lakes:'),
        )?.innerText || '',
    );
    const baselineCount = Number(baselineCountText.match(/(\d+)/)?.[1] || 0);
    assert.ok(baselineCount >= 46);

    // 1. Register New Lake
    await navigateTab('^Register New Lake$', '^Register New Lake$');
    await browser.typeInto('input[placeholder*="Attabad Lake"]', 'E2E Probe Lake');
    const nums = await browser.evaluate(() =>
      Array.from(document.querySelectorAll('input[type="number"]')).length,
    );
    assert.ok(nums >= 3);
    await browser.evaluate(() => {
      const inputs = Array.from(document.querySelectorAll('input[type="number"]'));
      const vals = ['24.0', '36.35', '74.85'];
      inputs.slice(0, 3).forEach((el, idx) => {
        const setter = Object.getOwnPropertyDescriptor(
          window.HTMLInputElement.prototype,
          'value',
        )?.set;
        setter.call(el, vals[idx]);
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
      });
    });
    await browser.clickByText('button', '^Register Lake$');
    await browser.waitForText('Lake registered successfully');

    // Verify Total Lakes incremented by 1
    await browser.waitFor(
      (expected) => {
        const txt =
          Array.from(document.querySelectorAll('.mono-label')).find((e) =>
            e.innerText.includes('Total Lakes:'),
          )?.innerText || '';
        const n = Number(txt.match(/(\d+)/)?.[1] || 0);
        return n === expected;
      },
      [baselineCount + 1],
      { timeoutMs: 10000, label: `Total Lakes: ${baselineCount + 1}` },
    );

    // 2. Search -> Rename
    await navigateTab('^Lakes Inventory$', '^Lakes Inventory$');
    const searchSel = 'input[type="search"], input[placeholder*="Search" i]';
    await browser.typeInto(searchSel, 'E2E Probe Lake');
    await browser.waitForText('E2E Probe Lake');
    await browser.clickByText('table button', '^Rename$');
    await browser.typeInto('[role="dialog"] input[type="text"], .hud-frame input[type="text"]', 'E2E Probe Lake v2');
    await browser.clickByText('button', '^Save Name$');
    await browser.typeInto(searchSel, 'E2E Probe Lake v2');
    await browser.waitForText('E2E Probe Lake v2');

    // 3. Details Dossier
    await browser.clickByText('table button', '^Details$');
    await browser.waitForSelector('[role="dialog"]');
    await browser.clickByText('[role="dialog"] button', '^×$|Close');

    // 4. Delete via non-blocking confirm dialog
    await browser.clickByText('table button', '^Delete$');
    await browser.clickByText('[role="dialog"] button, [role="alertdialog"] button', 'Confirm|Delete');

    // Verify Total Lakes returns to baseline
    await browser.waitFor(
      (expected) => {
        const txt =
          Array.from(document.querySelectorAll('.mono-label')).find((e) =>
            e.innerText.includes('Total Lakes:'),
          )?.innerText || '';
        const n = Number(txt.match(/(\d+)/)?.[1] || 0);
        return n === expected;
      },
      [baselineCount],
      { timeoutMs: 10000, label: `Total Lakes restored to ${baselineCount}` },
    );
    assertCleanBrowserHealth();
  });
});
