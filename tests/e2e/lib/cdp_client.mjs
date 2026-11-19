import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync, spawn } from 'node:child_process';

const IGNORED_THIRD_PARTY_HOSTS = [
  'unpkg.com',
  'fonts.googleapis.com',
  'fonts.gstatic.com',
  'basemaps.cartocdn.com',
  'server.arcgisonline.com',
  'cdnjs.cloudflare.com',
];

export function findBrowserExecutable() {
  const localAppData = process.env.LOCALAPPDATA || '';
  const candidates = [
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
    path.join(localAppData, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    path.join(localAppData, 'Google', 'Chrome', 'Application', 'chrome.exe'),
  ];
  for (const candidate of candidates) {
    if (candidate && fs.existsSync(candidate)) {
      return candidate;
    }
  }
  throw new Error('Could not locate Microsoft Edge or Google Chrome executable.');
}

function killProcessTreeWindows(pid) {
  if (!pid) return;
  if (process.platform === 'win32') {
    try {
      execFileSync('taskkill', ['/PID', String(pid), '/T', '/F'], {
        stdio: 'ignore',
        windowsHide: true,
      });
    } catch {
      // Ignore already exited processes
    }
  } else {
    try {
      process.kill(pid, 'SIGKILL');
    } catch {
      // Ignore
    }
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export class CdpBrowser {
  constructor({ uiOrigin, apiOrigin = null, downloadDir = null, userDataDir = null } = {}) {
    this.uiOrigin = uiOrigin || '';
    this.apiOrigin = apiOrigin || '';
    this.ownsUserDataDir = !userDataDir;
    this.userDataDir =
      userDataDir || fs.mkdtempSync(path.join(os.tmpdir(), 'glof_e2e_cdp_'));
    this.downloadDir = downloadDir || path.join(this.userDataDir, 'downloads');
    fs.mkdirSync(this.downloadDir, { recursive: true });

    this.proc = null;
    this.ws = null;
    this.sessionId = null;
    this.targetId = null;
    this.nextId = 1;
    this.pending = new Map();
    this.closed = false;

    this.dialogs = [];
    this.pageErrors = [];
    this.consoleErrors = [];
    this.sameOriginServerErrors = [];
    this.apiResponses = [];
    this._blockedPatterns = [];
    this._loadWaiters = [];
  }

  async launch() {
    const exe = findBrowserExecutable();
    const args = [
      '--headless=new',
      '--remote-debugging-port=0',
      `--user-data-dir=${this.userDataDir}`,
      '--no-first-run',
      '--no-default-browser-check',
      '--disable-gpu',
      '--disable-extensions',
      '--disable-background-networking',
      '--window-size=1440,900',
      'about:blank',
    ];

    this.proc = spawn(exe, args, {
      stdio: ['ignore', 'ignore', 'pipe'],
      windowsHide: true,
    });

    const wsUrl = await this._waitForWsUrl(20000);
    await this._connectWs(wsUrl);

    const { targetId } = await this.sendBrowser('Target.createTarget', {
      url: 'about:blank',
    });
    this.targetId = targetId;

    const { sessionId } = await this.sendBrowser('Target.attachToTarget', {
      targetId,
      flatten: true,
    });
    this.sessionId = sessionId;

    await this.send('Page.enable');
    await this.send('Runtime.enable');
    await this.send('Network.enable');
    await this.send('DOM.enable');

    try {
      await this.sendBrowser('Browser.setDownloadBehavior', {
        behavior: 'allow',
        downloadPath: this.downloadDir,
        eventsEnabled: true,
      });
    } catch {
      // Older fallback ignored if unsupported
    }

    // Reduce Framer Motion transitions for deterministic headless clicks
    await this.setReducedMotion(true);
    return this;
  }

  async _waitForWsUrl(timeoutMs) {
    let stderrBuf = '';
    this.proc.stderr.on('data', (chunk) => {
      stderrBuf += chunk.toString();
    });

    const activePortPath = path.join(this.userDataDir, 'DevToolsActivePort');
    const deadline = Date.now() + timeoutMs;

    while (Date.now() < deadline) {
      if (this.proc.exitCode !== null) {
        throw new Error(
          `Headless browser exited prematurely with code ${this.proc.exitCode}: ${stderrBuf}`,
        );
      }

      const match = stderrBuf.match(/DevTools listening on (ws:\/\/[^\s]+)/);
      if (match) {
        return match[1];
      }

      if (fs.existsSync(activePortPath)) {
        const lines = fs
          .readFileSync(activePortPath, 'utf-8')
          .split(/\r?\n/)
          .filter(Boolean);
        if (lines.length >= 2) {
          return `ws://127.0.0.1:${lines[0]}${lines[1]}`;
        }
      }
      await sleep(100);
    }
    throw new Error(`Timed out waiting for browser DevTools WebSocket. stderr=${stderrBuf}`);
  }

  async _connectWs(wsUrl) {
    await new Promise((resolve, reject) => {
      const ws = new WebSocket(wsUrl);
      this.ws = ws;
      ws.addEventListener('open', () => resolve(), { once: true });
      ws.addEventListener('error', (err) => reject(err), { once: true });
      ws.addEventListener('message', (event) => this._onMessage(event.data));
      ws.addEventListener('close', () => {
        for (const { reject: rej } of this.pending.values()) {
          rej(new Error('CDP WebSocket closed'));
        }
        this.pending.clear();
      });
    });
  }

  _onMessage(raw) {
    let msg;
    try {
      msg = JSON.parse(typeof raw === 'string' ? raw : raw.toString());
    } catch {
      return;
    }

    if (msg.id !== undefined) {
      const entry = this.pending.get(msg.id);
      if (entry) {
        this.pending.delete(msg.id);
        if (msg.error) {
          entry.reject(new Error(`CDP error ${msg.error.code}: ${msg.error.message}`));
        } else {
          entry.resolve(msg.result || {});
        }
      }
      return;
    }

    const method = msg.method;
    const params = msg.params || {};

    if (method === 'Page.loadEventFired') {
      const waiters = this._loadWaiters.splice(0, this._loadWaiters.length);
      for (const resolve of waiters) {
        resolve();
      }
    } else if (method === 'Page.javascriptDialogOpening') {
      this.dialogs.push({
        type: params.type,
        message: params.message,
        url: params.url,
        timestamp: Date.now(),
      });
      this.send('Page.handleJavaScriptDialog', { accept: true }).catch(() => {});
    } else if (method === 'Runtime.exceptionThrown') {
      const det = params.exceptionDetails || {};
      const desc =
        det.exception?.description || det.text || 'Unknown Runtime.exceptionThrown';
      this.pageErrors.push(desc);
    } else if (method === 'Runtime.consoleAPICalled') {
      if (params.type === 'error') {
        const text = (params.args || [])
          .map((a) => a.value ?? a.description ?? '')
          .join(' ');
        // Ignore third-party network noise logged by browser console
        const isThirdParty = IGNORED_THIRD_PARTY_HOSTS.some((h) => text.includes(h));
        if (!isThirdParty && text) {
          this.consoleErrors.push(text);
        }
      }
    } else if (method === 'Network.responseReceived') {
      const resp = params.response || {};
      const url = resp.url || '';
      const status = Number(resp.status || 0);
      const isSameOrigin =
        (this.uiOrigin && url.startsWith(this.uiOrigin)) ||
        (this.apiOrigin && url.startsWith(this.apiOrigin));
      if (isSameOrigin) {
        this.apiResponses.push({
          url,
          status,
          mimeType: resp.mimeType || '',
          timestamp: Date.now(),
        });
        if (status >= 500) {
          this.sameOriginServerErrors.push({ url, status });
        }
      }
    } else if (method === 'Fetch.requestPaused') {
      const reqId = params.requestId;
      const url = params.request?.url || '';
      const shouldFail = this._blockedPatterns.some((pat) => url.includes(pat));
      if (shouldFail) {
        this.send('Fetch.failRequest', {
          requestId: reqId,
          errorReason: 'Failed',
        }).catch(() => {});
      } else {
        this.send('Fetch.continueRequest', { requestId: reqId }).catch(() => {});
      }
    }
  }

  sendBrowser(method, params = {}) {
    return this._rawSend(method, params, null);
  }

  send(method, params = {}) {
    return this._rawSend(method, params, this.sessionId);
  }

  _rawSend(method, params, sessionId) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error(`CDP socket not open for ${method}`));
    }
    const id = this.nextId++;
    const payload = { id, method, params };
    if (sessionId) {
      payload.sessionId = sessionId;
    }
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify(payload));
    });
  }

  clearTelemetry() {
    this.dialogs = [];
    this.pageErrors = [];
    this.consoleErrors = [];
    this.sameOriginServerErrors = [];
    this.apiResponses = [];
  }

  async setReducedMotion(enabled = true) {
    await this.send('Emulation.setEmulatedMedia', {
      features: [
        {
          name: 'prefers-reduced-motion',
          value: enabled ? 'reduce' : 'no-preference',
        },
      ],
    });
  }

  async setViewport(width, height, mobile = false) {
    await this.send('Emulation.setDeviceMetricsOverride', {
      width,
      height,
      deviceScaleFactor: 1,
      mobile,
      screenWidth: width,
      screenHeight: height,
    });
  }

  async blockExternalHosts(hostSubstrings) {
    this._blockedPatterns = [...hostSubstrings];
    await this.send('Fetch.enable', {
      patterns: hostSubstrings.map((h) => ({
        urlPattern: `*${h}*`,
        requestStage: 'Request',
      })),
    });
  }

  async unblockExternalHosts() {
    this._blockedPatterns = [];
    try {
      await this.send('Fetch.disable');
    } catch {
      // Ignore if not enabled
    }
  }

  async goto(url, { timeoutMs = 30000 } = {}) {
    const loadPromise = new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        const idx = this._loadWaiters.indexOf(onLoad);
        if (idx >= 0) this._loadWaiters.splice(idx, 1);
        reject(new Error(`Timed out waiting for Page.loadEventFired (${url})`));
      }, timeoutMs);
      const onLoad = () => {
        clearTimeout(timer);
        resolve();
      };
      this._loadWaiters.push(onLoad);
    });
    await this.send('Page.navigate', { url });
    await loadPromise;
    await this.waitFor(
      () => document.readyState === 'complete',
      [],
      { timeoutMs, label: `document.readyState === complete (${url})` },
    );
  }

  async reload({ timeoutMs = 30000 } = {}) {
    const loadPromise = new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        const idx = this._loadWaiters.indexOf(onLoad);
        if (idx >= 0) this._loadWaiters.splice(idx, 1);
        reject(new Error('Timed out waiting for Page.loadEventFired on reload'));
      }, timeoutMs);
      const onLoad = () => {
        clearTimeout(timer);
        resolve();
      };
      this._loadWaiters.push(onLoad);
    });
    await this.send('Page.reload', { ignoreCache: false });
    await loadPromise;
    await this.waitFor(
      () => document.readyState === 'complete',
      [],
      { timeoutMs, label: 'page reload complete' },
    );
  }

  async evaluate(fnOrExpr, args = []) {
    let expression;
    if (typeof fnOrExpr === 'function') {
      const serializedArgs = args.map((a) => JSON.stringify(a)).join(', ');
      expression = `(${fnOrExpr.toString()})(${serializedArgs})`;
    } else {
      expression = String(fnOrExpr);
    }

    const res = await this.send('Runtime.evaluate', {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (res.exceptionDetails) {
      const desc =
        res.exceptionDetails.exception?.description ||
        res.exceptionDetails.text ||
        'Evaluation failed';
      throw new Error(`Runtime.evaluate exception: ${desc}`);
    }
    return res.result ? res.result.value : undefined;
  }

  async waitFor(fnOrExpr, args = [], { timeoutMs = 15000, pollMs = 120, label = 'condition' } = {}) {
    const deadline = Date.now() + timeoutMs;
    let lastErr = null;
    while (Date.now() < deadline) {
      try {
        const val = await this.evaluate(fnOrExpr, args);
        if (val) {
          return val;
        }
      } catch (err) {
        lastErr = err;
      }
      await sleep(pollMs);
    }
    throw new Error(
      `Timed out after ${timeoutMs}ms waiting for ${label}${lastErr ? ` (lastErr: ${lastErr.message})` : ''}`,
    );
  }

  async waitForSelector(selector, { timeoutMs = 15000, visible = true } = {}) {
    return this.waitFor(
      (sel, mustBeVisible) => {
        const el = document.querySelector(sel);
        if (!el) return false;
        if (!mustBeVisible) return true;
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
      },
      [selector, visible],
      { timeoutMs, label: `selector "${selector}"` },
    );
  }

  async waitForText(patternStr, { timeoutMs = 15000, flags = 'i' } = {}) {
    return this.waitFor(
      (pat, fl) => {
        const re = new RegExp(pat, fl);
        const normalized = (document.body?.innerText || '').replace(/\s+/g, ' ');
        return re.test(normalized);
      },
      [patternStr, flags],
      { timeoutMs, label: `text /${patternStr}/${flags}` },
    );
  }

  async clickPoint(x, y) {
    await this.send('Input.dispatchMouseEvent', {
      type: 'mouseMoved',
      x,
      y,
      button: 'none',
    });
    await this.send('Input.dispatchMouseEvent', {
      type: 'mousePressed',
      x,
      y,
      button: 'left',
      clickCount: 1,
    });
    await this.send('Input.dispatchMouseEvent', {
      type: 'mouseReleased',
      x,
      y,
      button: 'left',
      clickCount: 1,
    });
  }

  async moveMouse(x, y) {
    await this.send('Input.dispatchMouseEvent', {
      type: 'mouseMoved',
      x,
      y,
      button: 'none',
    });
  }

  async clickSelector(selector, { timeoutMs = 15000, index = 0 } = {}) {
    const box = await this.waitFor(
      (sel, idx) => {
        const els = Array.from(document.querySelectorAll(sel));
        const el = els[idx];
        if (!el) return null;
        el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
        const r = el.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) return null;
        return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
      },
      [selector, index],
      { timeoutMs, label: `clickable selector "${selector}"[${index}]` },
    );
    await this.clickPoint(box.x, box.y);
  }

  async clickByText(selector, patternStr, { timeoutMs = 15000, flags = 'i', exact = false } = {}) {
    await this.waitFor(
      (sel, pat, fl, isExact) => {
        const re = new RegExp(pat, fl);
        const els = Array.from(document.querySelectorAll(sel));
        for (const el of els) {
          const text = (el.innerText || el.textContent || '').trim();
          const matched = isExact ? text === pat : re.test(text);
          if (!matched) continue;
          const r = el.getBoundingClientRect();
          if (r.width <= 0 || r.height <= 0) continue;
          el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
          return true;
        }
        return false;
      },
      [selector, patternStr, flags, exact],
      { timeoutMs, label: `${selector} matching "${patternStr}"` },
    );
    await sleep(60);
    const box = await this.evaluate(
      (sel, pat, fl, isExact) => {
        const re = new RegExp(pat, fl);
        const els = Array.from(document.querySelectorAll(sel));
        for (const el of els) {
          const text = (el.innerText || el.textContent || '').trim();
          const matched = isExact ? text === pat : re.test(text);
          if (!matched) continue;
          const r = el.getBoundingClientRect();
          if (r.width <= 0 || r.height <= 0) continue;
          return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
        }
        return null;
      },
      [selector, patternStr, flags, exact],
    );
    if (!box) {
      throw new Error(`Element disappeared before click: ${selector} matching "${patternStr}"`);
    }
    await this.clickPoint(box.x, box.y);
  }

  async typeInto(selector, value, { timeoutMs = 15000, clear = true } = {}) {
    const box = await this.waitFor(
      (sel) => {
        const el = document.querySelector(sel);
        if (!el) return null;
        el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
        const r = el.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) return null;
        return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
      },
      [selector],
      { timeoutMs, label: `input "${selector}"` },
    );
    await this.clickPoint(box.x, box.y);
    if (clear) {
      await this.evaluate((sel) => {
        const el = document.querySelector(sel);
        if (!el) return;
        const proto =
          el.tagName === 'TEXTAREA'
            ? window.HTMLTextAreaElement.prototype
            : window.HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
        if (setter) {
          setter.call(el, '');
        } else {
          el.value = '';
        }
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
      }, [selector]);
    }
    if (value !== '') {
      await this.send('Input.insertText', { text: String(value) });
    }
  }

  async close() {
    if (this.closed) return;
    this.closed = true;

    try {
      await this.unblockExternalHosts();
    } catch {
      // Ignore
    }
    try {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        await Promise.race([
          this.sendBrowser('Browser.close'),
          sleep(1500),
        ]);
      }
    } catch {
      // Ignore
    }
    try {
      this.ws?.close();
    } catch {
      // Ignore
    }
    if (this.proc?.pid) {
      killProcessTreeWindows(this.proc.pid);
    }
    if (this.ownsUserDataDir && fs.existsSync(this.userDataDir)) {
      fs.rmSync(this.userDataDir, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
    }
  }
}
