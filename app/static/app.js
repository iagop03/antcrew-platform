/* antcrew-platform shared dashboard utilities */

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
    { href: '/', label: 'Runs' },
    { href: '/reviews', label: 'Reviews' },
    { href: '/evals', label: 'Evals' },
    { href: '/tickets', label: 'Tickets' },
    { href: '/webhooks', label: 'Webhooks' },
  ];
  nav.innerHTML = links.map(l =>
    `<a href="${l.href}" class="${path === l.href ? 'active' : ''}">${l.label}</a>`
  ).join('');
  // Key setup button
  const btn = document.getElementById('key-btn');
  if (btn) btn.onclick = openKeyModal;
  // Show key banner if no key set and not open mode
  if (!getApiKey()) _maybeShowKeyBanner();
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
  let url = `ws://${location.host}/ws/events?api_key=${encodeURIComponent(key)}`;
  if (runId) url += `&run_id=${encodeURIComponent(runId)}`;

  function connect() {
    _ws = new WebSocket(url);
    _ws.onopen = () => { if (dot) dot.className = 'ws-dot connected'; };
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
    <h3>Platform API Key</h3>
    <div class="form-group">
      <label>X-Api-Key</label>
      <input type="password" id="key-input" placeholder="sk-…" autocomplete="off">
    </div>
    <p style="color:var(--text-muted);font-size:12px;margin-bottom:12px">
      Stored in browser localStorage. Leave blank if auth is disabled (open mode).
    </p>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="setApiKey('');closeKeyModal();location.reload()">Clear</button>
      <button class="btn btn-ghost" onclick="closeKeyModal()">Cancel</button>
      <button class="btn btn-primary" onclick="saveKeyModal()">Save</button>
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
    <div class="nav-right">
      <div class="ws-dot" id="ws-dot" title="WebSocket connection"></div>
      <button class="btn btn-ghost" id="key-btn" style="padding:4px 10px;font-size:12px">🔑 Key</button>
    </div>`;
  document.body.prepend(el);
}

// Call on every page
document.addEventListener('DOMContentLoaded', () => {
  injectNav();
  injectKeyModal();
  initNav();
});
