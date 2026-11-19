import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import os from 'node:os';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

export const REPO_ROOT = path.resolve(__dirname, '..', '..', '..');
export const FRONTEND_DIR = path.join(REPO_ROOT, 'frontend');

const API_PREFIXES = [
  '/lakes',
  '/gee',
  '/risk',
  '/reports',
  '/analytics',
  '/auth',
  '/early-warning',
  '/health',
  '/api',
  '/weather',
  '/docs',
  '/openapi.json',
  '/redoc',
];

const MIME_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.mjs': 'application/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.geojson': 'application/geo+json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.txt': 'text/plain; charset=utf-8',
};

function isApiPath(pathname) {
  return API_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}

/**
 * Builds frontend/ into an isolated temporary directory with VITE_API_URL=""
 * so no repository files or frontend/dist/ are mutated.
 */
export async function buildTempFrontend(outDir) {
  fs.mkdirSync(outDir, { recursive: true });

  await new Promise((resolve, reject) => {
    const isWin = process.platform === 'win32';
    const cmd = isWin ? 'cmd.exe' : 'npx';
    const args = isWin
      ? ['/c', 'npx', 'vite', 'build', '--outDir', outDir, '--emptyOutDir']
      : ['vite', 'build', '--outDir', outDir, '--emptyOutDir'];

    const env = { ...process.env, VITE_API_URL: '' };
    const child = spawn(cmd, args, {
      cwd: FRONTEND_DIR,
      env,
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    });

    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr.on('data', (chunk) => {
      stderr += chunk.toString();
    });
    child.on('error', reject);
    child.on('close', (code) => {
      if (code === 0) {
        resolve();
      } else {
        reject(
          new Error(
            `vite build failed with code ${code}\nSTDOUT:\n${stdout}\nSTDERR:\n${stderr}`,
          ),
        );
      }
    });
  });

  const indexHtml = path.join(outDir, 'index.html');
  if (!fs.existsSync(indexHtml)) {
    throw new Error(`Expected built index.html at ${indexHtml}`);
  }

  // Verify zero baked-in local backend URLs in generated JS chunks
  const assetsDir = path.join(outDir, 'assets');
  if (fs.existsSync(assetsDir)) {
    for (const entry of fs.readdirSync(assetsDir)) {
      if (entry.endsWith('.js')) {
        const jsText = fs.readFileSync(path.join(assetsDir, entry), 'utf-8');
        if (jsText.includes('127.0.0.1:8000')) {
          throw new Error(
            `Temp Vite build chunk ${entry} unexpectedly contains 127.0.0.1:8000`,
          );
        }
      }
    }
  }
  return outDir;
}

/**
 * Starts an ephemeral Node HTTP server serving distDir and proxying backend prefixes to apiBaseUrl.
 */
export async function startUiProxyServer({ distDir, apiBaseUrl }) {
  const apiTarget = new URL(apiBaseUrl);
  const resolvedDist = path.resolve(distDir);

  const server = http.createServer((req, res) => {
    const reqUrl = new URL(req.url || '/', 'http://127.0.0.1');
    const pathname = decodeURIComponent(reqUrl.pathname);

    if (isApiPath(pathname)) {
      const headers = { ...req.headers, host: apiTarget.host };
      const proxyReq = http.request(
        {
          hostname: apiTarget.hostname,
          port: apiTarget.port,
          path: reqUrl.pathname + reqUrl.search,
          method: req.method,
          headers,
        },
        (proxyRes) => {
          res.writeHead(proxyRes.statusCode || 502, proxyRes.headers);
          proxyRes.pipe(res, { end: true });
        },
      );
      proxyReq.on('error', (err) => {
        if (!res.headersSent) {
          res.writeHead(502, { 'Content-Type': 'application/json' });
        }
        res.end(JSON.stringify({ detail: `Proxy error: ${err.message}` }));
      });
      req.pipe(proxyReq, { end: true });
      return;
    }

    const relPath = pathname === '/' ? 'index.html' : pathname.replace(/^\/+/, '');
    const candidate = path.resolve(resolvedDist, relPath);

    // Path traversal guard
    if (!candidate.startsWith(resolvedDist)) {
      res.writeHead(403, { 'Content-Type': 'text/plain' });
      res.end('Forbidden');
      return;
    }

    let targetFile = candidate;
    if (!fs.existsSync(targetFile) || fs.statSync(targetFile).isDirectory()) {
      targetFile = path.join(resolvedDist, 'index.html');
    }

    const ext = path.extname(targetFile).toLowerCase();
    const contentType = MIME_TYPES[ext] || 'application/octet-stream';
    res.writeHead(200, { 'Content-Type': contentType });
    fs.createReadStream(targetFile).pipe(res);
  });

  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });

  const addr = server.address();
  const port = typeof addr === 'object' && addr ? addr.port : 0;
  const uiUrl = `http://127.0.0.1:${port}`;

  return {
    server,
    port,
    uiUrl,
    async close() {
      await new Promise((resolve) => server.close(() => resolve()));
    },
  };
}

/**
 * Convenience helper that builds into a temp dir and starts the UI proxy server.
 */
export async function createIsolatedUiHarness({ apiBaseUrl, tempRoot = null }) {
  const ownsTemp = !tempRoot;
  const rootDir = tempRoot || fs.mkdtempSync(path.join(os.tmpdir(), 'glof_e2e_ui_'));
  const distDir = path.join(rootDir, 'ui_dist');
  await buildTempFrontend(distDir);
  const proxy = await startUiProxyServer({ distDir, apiBaseUrl });

  return {
    ...proxy,
    distDir,
    rootDir,
    async cleanup() {
      await proxy.close();
      if (ownsTemp) {
        fs.rmSync(rootDir, { recursive: true, force: true });
      }
    },
  };
}
