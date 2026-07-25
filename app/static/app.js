/* antcrew-platform shared dashboard utilities */

// ── i18n ──────────────────────────────────────────────────────────────────────

const _i18nCache = {};
let _i18nLang = localStorage.getItem('antcrew_lang') ||
  (navigator.language || 'en').split('-')[0];
if (!['en', 'es'].includes(_i18nLang)) _i18nLang = 'en';

async function _loadI18n(lang) {
  if (_i18nCache[lang]) return _i18nCache[lang];
  try {
    const r = await fetch(`/static/i18n/${lang}.json`);
    if (r.ok) { _i18nCache[lang] = await r.json(); return _i18nCache[lang]; }
  } catch {}
  return {};
}

function $t(key, params = {}) {
  const dict = _i18nCache[_i18nLang] || _i18nCache['en'] || {};
  let val = dict[key] ?? key;
  for (const [k, v] of Object.entries(params)) val = val.replace(`{${k}}`, v);
  return val;
}

async function setLang(lang) {
  _i18nLang = lang;
  localStorage.setItem('antcrew_lang', lang);
  await _loadI18n(lang);
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.dataset.i18n;
    el.textContent = $t(key);
  });
  // Trigger Alpine.js re-render for components that use $t()
  document.querySelectorAll('[x-data]').forEach(el => {
    if (window.Alpine) {
      try { Alpine.nextTick(() => {}); } catch {}
    }
  });
}

// Load on startup — before DOMContentLoaded fires
_loadI18n(_i18nLang);
_loadI18n('en'); // preload fallback

// ── API key storage ──────────────────────────────────────────────────────────

function getApiKey() {
  return localStorage.getItem('antcrew_api_key') || '';
}
function setApiKey(key) {
  if (key) localStorage.setItem('antcrew_api_key', key.trim());
  else localStorage.removeItem('antcrew_api_key');
}

// ── API helpers ──────────────────────────────────────────────────────────────

async function apiFetch(path, opts = {}) {
  const key = getApiKey();
  const headers = { 'Content-Type': 'application/json', ...( opts.headers || {}) };
  if (key) headers['X-Api-Key'] = key;
  const r = await fetch(path, { ...opts, headers });
  if (!r.ok) {
    let detail = `HTTP ${r.status}`;
    try { detail = (await r.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  if (r.status === 204) return null;
  return r.json();
}

async function apiDelete(path) {
  return apiFetch(path, { method: 'DELETE' });
}

async function apiPost(path, body) {
  return apiFetch(path, { method: 'POST', body: JSON.stringify(body) });
}

async function apiPatch(path, body) {
  return apiFetch(path, { method: 'PATCH', body: JSON.stringify(body) });
}

// ── Formatters ───────────────────────────────────────────────────────────────

function fmtDate(dt) {
  if (!dt) return '—';
  const d = new Date(dt);
  const now = new Date();
  const diff = (now - d) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return d.toLocaleDateString();
}

function fmtDateFull(dt) {
  if (!dt) return '—';
  return new Date(dt).toLocaleString();
}

function fmtCost(n) {
  if (!n && n !== 0) return '—';
  if (n < 0.001) return '<$0.001';
  return `$${n.toFixed(3)}`;
}

function fmtDuration(s) {
  if (!s && s !== 0) return '—';
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60), rem = Math.round(s % 60);
  return rem ? `${m}m ${rem}s` : `${m}m`;
}

function fmtRunId(rid) {
  return rid ? rid.slice(0, 8) + '…' : '—';
}

function fmtScore(v) {
  if (v === null || v === undefined) return '—';
  const pct = Math.round(v * 100);
  const cls = v >= 0.8 ? 'score-high' : v >= 0.5 ? 'score-mid' : 'score-low';
  return `<span class="score ${cls}">${pct}%</span>`;
}

// ── Status badge ─────────────────────────────────────────────────────────────

function statusBadge(s) {
  const map = {
    running: 'badge-running', success: 'badge-success', error: 'badge-error',
    cancelled: 'badge-cancelled', pending: 'badge-pending',
    approved: 'badge-approved', rejected: 'badge-rejected',
    edited: 'badge-approved', feedback: 'badge-pending',
    done: 'badge-done', external: 'badge-external', timeout: 'badge-cancelled',
  };
  const cls = map[s] || 'badge-cancelled';
  return `<span class="badge ${cls}">${s}</span>`;
}

function priorityPill(p) {
  const cls = { high: 'pill-high', medium: 'pill-medium', low: 'pill-low' }[p] || 'pill-medium';
  return `<span class="pill ${cls}">${p}</span>`;
}

// ── Navigation ────────────────────────────────────────────────────────────────

function initNav() {
  const path = location.pathname;
  const nav = document.getElementById('nav-links');
  if (!nav) return;
  const links = [
    { href: '/', key: 'nav.dashboard' },
    { href: '/runs', key: 'nav.runs' },
    { href: '/reviews', key: 'nav.reviews' },
    { href: '/evals', key: 'nav.evals' },
    { href: '/tickets', key: 'nav.tickets' },
    { href: '/compare', key: 'nav.compare' },
    { href: '/pipelines', key: 'nav.pipelines' },
    { href: '/webhooks', key: 'nav.webhooks' },
  ];
  nav.innerHTML = links.map(l =>
    `<a href="${l.href}" class="${path === l.href ? 'active' : ''}" data-i18n="${l.key}">${$t(l.key)}</a>`
  ).join('');
  // Key setup button
  const btn = document.getElementById('key-btn');
  if (btn) btn.onclick = openKeyModal;
  // Show key banner if no key set and not open mode
  if (!getApiKey()) _maybeShowKeyBanner();
  // Populate auth button (Login / Logout) from session state
  _initSessionNav();
}

async function _maybeShowKeyBanner() {
  // Probe /health — if it 401s without a key, show the banner
  const r = await fetch('/health');
  if (r.status === 401) {
    const banner = document.getElementById('key-banner');
    if (banner) banner.style.display = 'flex';
  }
}

// ── API key modal ─────────────────────────────────────────────────────────────

function openKeyModal() {
  const m = document.getElementById('key-modal');
  if (!m) return;
  const inp = document.getElementById('key-input');
  if (inp) inp.value = getApiKey();
  m.classList.add('open');
}

function closeKeyModal() {
  const m = document.getElementById('key-modal');
  if (m) m.classList.remove('open');
}

function saveKeyModal() {
  const inp = document.getElementById('key-input');
  if (!inp) return;
  setApiKey(inp.value.trim());
  closeKeyModal();
  location.reload();
}

// ── WebSocket live events ─────────────────────────────────────────────────────

let _ws = null;

function connectWs(onEvent, runId = null) {
  const dot = document.getElementById('ws-dot');
  const key = getApiKey();
  // Key-free URL — authenticate via first message to keep key out of access logs / browser history
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const url = `${proto}://${location.host}/ws/events`;

  function connect() {
    _ws = new WebSocket(url);
    _ws.onopen = () => {
      if (dot) dot.className = 'ws-dot connected';
      // Send auth + optional run_id as first message (backend waits up to 10 s)
      const msg = { auth: key };
      if (runId) msg.run_id = runId;
      _ws.send(JSON.stringify(msg));
    };
    _ws.onclose = () => {
      if (dot) dot.className = 'ws-dot error';
      setTimeout(connect, 3000);
    };
    _ws.onerror = () => { if (dot) dot.className = 'ws-dot error'; };
    _ws.onmessage = (e) => {
      try {
        const evt = JSON.parse(e.data);
        if (evt.type !== 'ping') onEvent(evt);
      } catch {}
    };
  }
  connect();
}

// ── Error / loading helpers ───────────────────────────────────────────────────

function showError(msg, containerId = 'error-box') {
  const el = document.getElementById(containerId);
  if (!el) { console.error(msg); return; }
  el.textContent = msg;
  el.style.display = 'block';
}

function hideError(containerId = 'error-box') {
  const el = document.getElementById(containerId);
  if (el) el.style.display = 'none';
}

// ── Shared modal markup (inject into body) ────────────────────────────────────

function injectKeyModal() {
  const el = document.createElement('div');
  el.innerHTML = `
<div id="key-modal" class="modal-overlay" onclick="if(event.target===this)closeKeyModal()">
  <div class="modal-box">
    <h3 data-i18n="key_modal.title">${$t('key_modal.title')}</h3>
    <div class="form-group">
      <label>X-Api-Key</label>
      <input type="password" id="key-input" placeholder="sk-…" autocomplete="off">
    </div>
    <p style="color:var(--text-muted);font-size:12px;margin-bottom:12px" data-i18n="key_modal.stored">
      ${$t('key_modal.stored')}
    </p>
    <div class="modal-actions">
      <button class="btn btn-ghost" data-i18n="key_modal.clear" onclick="setApiKey('');closeKeyModal();location.reload()">${$t('key_modal.clear')}</button>
      <button class="btn btn-ghost" data-i18n="key_modal.cancel" onclick="closeKeyModal()">${$t('key_modal.cancel')}</button>
      <button class="btn btn-primary" data-i18n="key_modal.save" onclick="saveKeyModal()">${$t('key_modal.save')}</button>
    </div>
  </div>
</div>`;
  document.body.appendChild(el.firstElementChild);
}

function injectNav() {
  const el = document.createElement('nav');
  el.className = 'nav';
  el.innerHTML = `
    <a class="brand" href="/"><span>ant</span>crew</a>
    <div class="nav-links" id="nav-links"></div>
    <div class="nav-right" id="nav-right">
      <div class="ws-dot" id="ws-dot" title="WebSocket connection"></div>
      <span id="auth-nav-btn"></span>
      <button class="btn btn-ghost" id="key-btn" style="padding:4px 10px;font-size:12px">🔑 Key</button>
    </div>`;
  document.body.prepend(el);

  // Language toggle — cycles en ↔ es
  const navRight = el.querySelector('#nav-right');
  const langBtn = document.createElement('button');
  langBtn.className = 'btn btn-ghost';
  langBtn.style.cssText = 'padding:3px 8px;font-size:11px';
  langBtn.textContent = _i18nLang === 'es' ? 'EN' : 'ES';
  langBtn.title = _i18nLang === 'es' ? 'Switch to English' : 'Cambiar a Español';
  langBtn.onclick = async () => {
    const next = _i18nLang === 'es' ? 'en' : 'es';
    await setLang(next);
    langBtn.textContent = next === 'es' ? 'EN' : 'ES';
    langBtn.title = next === 'es' ? 'Switch to English' : 'Cambiar a Español';
  };
  navRight.prepend(langBtn);
}

// ── Session-aware auth button ─────────────────────────────────────────────────

async function _initSessionNav() {
  const slot = document.getElementById('auth-nav-btn');
  if (!slot) return;
  try {
    const r = await fetch('/auth/me', { credentials: 'same-origin' });
    if (r.ok) {
      const data = await r.json();
      const email = data.email || '';
      // Show email label + Logout button
      const logoutBtn = document.createElement('button');
      logoutBtn.className = 'btn btn-ghost';
      logoutBtn.style.cssText = 'padding:4px 10px;font-size:12px';
      logoutBtn.title = email;
      logoutBtn.textContent = email ? email.split('@')[0] + ' · Logout' : 'Logout';
      logoutBtn.onclick = async () => {
        logoutBtn.disabled = true;
        try {
          await fetch('/auth/token', { method: 'DELETE', credentials: 'same-origin' });
        } catch {}
        window.location.href = '/login';
      };
      slot.appendChild(logoutBtn);
    } else {
      // No valid session — show Login link
      const loginLink = document.createElement('a');
      loginLink.className = 'btn btn-ghost';
      loginLink.style.cssText = 'padding:4px 10px;font-size:12px';
      loginLink.href = '/login';
      loginLink.textContent = 'Login';
      slot.appendChild(loginLink);
    }
  } catch {
    // Network error or /auth/me doesn't exist — silently skip
  }
}

// Call on every page
document.addEventListener('DOMContentLoaded', () => {
  window.$t = $t;
  // Register as Alpine magic if Alpine is present
  if (window.Alpine) {
    Alpine.magic('t', () => (key, params) => $t(key, params));
  }
  injectNav();
  injectKeyModal();
  initNav();
});
