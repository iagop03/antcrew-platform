/* antcrew — collapsible left sidebar v2 */
(function () {
  'use strict';

  var EXP = 220, COL = 52;
  var SK = 'ac_sidebar_collapsed';
  var GK = 'ac_cfg_open';

  var NAV = [
    { group: 'Trabajo diario' },
    { label: 'Dashboard',  href: '/dashboard', icon: 'ti-layout-dashboard', ac: '/dashboard', i18n: 'nav.dashboard' },
    { label: 'Runs',       href: '/runs',      icon: 'ti-player-play',      ac: '/runs',      i18n: 'nav.runs' },
    { label: 'Tickets',    href: '/tickets',   icon: 'ti-checklist',        ac: '/tickets',   i18n: 'nav.tickets' },
    { label: 'Reviews',    href: '/reviews',   icon: 'ti-eye-check',        ac: '/reviews',   i18n: 'nav.reviews', badge: 'sb-rev-badge' },
    { group: 'Construir' },
    { label: 'Discover',   href: '/discover',                icon: 'ti-message-dots',  ac: '/discover' },
    { label: 'Pipelines',  href: '/pipelines',               icon: 'ti-sitemap',       ac: '/pipelines', i18n: 'nav.pipelines' },
    { label: 'Compare',    href: '/compare',                 icon: 'ti-scale',         ac: '/compare',   i18n: 'nav.compare' },
    { label: 'Workspaces', href: '/settings?tab=workspaces', icon: 'ti-building',      ac: '/settings',  acTab: 'workspaces' },
    { group: 'Observar' },
    { label: 'Evals',    href: '/evals',    icon: 'ti-chart-bar', ac: '/evals',    i18n: 'nav.evals' },
    { label: 'Webhooks', href: '/webhooks', icon: 'ti-webhook',   ac: '/webhooks', i18n: 'nav.webhooks' },
    { divider: true },
    { label: 'Docs', href: 'https://docs.antcrew.org', icon: 'ti-book-2', external: true },
    { group: 'Configurar', collapsible: true },
    { label: 'LLM / BYOK', href: '/settings?tab=llm',       icon: 'ti-robot',        ac: '/settings', acTab: 'llm',       cfg: true },
    { label: 'Schedules',  href: '/settings?tab=schedules',  icon: 'ti-calendar',     ac: '/settings', acTab: 'schedules', cfg: true },
    { label: 'GitHub App', href: '/settings?tab=github',     icon: 'ti-brand-github', ac: '/settings', acTab: 'github',    cfg: true },
    { label: 'Security',   href: '/settings?tab=security',   icon: 'ti-shield',       ac: '/settings', acTab: 'security',  cfg: true },
    { label: 'Reviewers',  href: '/settings?tab=reviewers',  icon: 'ti-key',          ac: '/settings', acTab: 'reviewers', cfg: true },
  ];

  // ── Active state ──────────────────────────────────────────────────────────
  function getTabParam() {
    var m = location.search.match(/[?&]tab=([^&]*)/);
    return m ? m[1] : null;
  }

  function isActive(item) {
    var p = location.pathname;
    if (!item.ac) return false;
    var pathOk = item.ac === '/dashboard'
      ? (p === '/' || p === '/dashboard')
      : (p === item.ac || p.startsWith(item.ac + '/'));
    if (!pathOk) return false;
    if (item.acTab) {
      var t = getTabParam();
      return t === item.acTab || (!t && item.acTab === 'workspaces');
    }
    if (item.ac === '/settings') return false;
    return true;
  }

  // ── State ─────────────────────────────────────────────────────────────────
  var collapsed = localStorage.getItem(SK) === '1';
  var anyCfgActive = NAV.some(function (i) { return i.cfg && isActive(i); });
  var cfgOpen = anyCfgActive || localStorage.getItem(GK) === '1';

  // ── Inject CSS ────────────────────────────────────────────────────────────
  var style = document.createElement('style');
  style.id = 'ac-sb-style';
  style.textContent =
    '#ac-sb{position:fixed;left:0;top:0;height:100%;z-index:50;' +
      'background:rgba(3,7,18,.97);border-right:1px solid rgba(31,41,55,.8);' +
      'display:flex;flex-direction:column;overflow:hidden;' +
      'transition:width .2s ease;will-change:width;box-sizing:border-box;}' +
    '.ac-sbi{display:flex;align-items:center;gap:9px;padding:6px 10px 6px 14px;' +
      'text-decoration:none;color:rgb(107,114,128);font-size:.8rem;font-weight:500;' +
      'border-radius:6px;margin:1px 8px;transition:background .1s,color .1s;cursor:pointer;' +
      'white-space:nowrap;min-height:32px;box-sizing:border-box;}' +
    '.ac-sbi:hover{background:rgba(31,41,55,.7);color:rgb(229,231,235);}' +
    '.ac-sbi.on{background:rgba(79,70,229,.12);color:#818cf8;' +
      'box-shadow:inset 2px 0 0 #818cf8;padding-left:12px;}' +
    '.ac-sbi-ic{font-size:15px;width:20px;text-align:center;flex-shrink:0;line-height:1;}' +
    '.ac-sbl{white-space:nowrap;overflow:hidden;min-width:0;flex:1;' +
      'transition:opacity .15s,max-width .2s;}' +
    '.ac-sbg{font-size:.6rem;text-transform:uppercase;letter-spacing:.09em;' +
      'color:rgba(75,85,99,.85);padding:11px 14px 3px;font-weight:700;' +
      'white-space:nowrap;overflow:hidden;transition:opacity .15s,max-height .2s,padding .2s;' +
      'max-height:40px;}' +
    '#ac-cfg-tog{width:100%;background:none;border:none;cursor:pointer;' +
      'display:flex;align-items:center;text-align:left;' +
      'font-size:.6rem;text-transform:uppercase;letter-spacing:.09em;' +
      'color:rgba(75,85,99,.85);padding:11px 14px 3px;font-weight:700;' +
      'white-space:nowrap;overflow:hidden;transition:opacity .15s,max-height .2s,padding .2s;' +
      'max-height:40px;box-sizing:border-box;}' +
    '#ac-cfg-tog:hover{color:rgba(107,114,128,.9);}' +
    '.ac-sb-div{height:1px;background:rgba(31,41,55,.6);margin:6px 12px;}' +
    '@media(max-width:767px){' +
      '#ac-sb{transform:translateX(-100%);transition:transform .22s ease,width .2s ease;}' +
      '#ac-sb.open{transform:translateX(0);}' +
      'body{padding-left:0!important;}' +
      '#ac-sb-bd{display:none!important;position:fixed;inset:0;z-index:49;background:rgba(0,0,0,.55);}' +
      '#ac-sb-bd.open{display:block!important;}' +
      '#ac-sb-mbar{display:flex!important;}' +
    '}' +
    '#ac-sb-mbar{display:none;position:sticky;top:0;z-index:40;height:44px;' +
      'align-items:center;gap:8px;padding:0 12px;' +
      'background:rgba(3,7,18,.96);border-bottom:1px solid rgba(31,41,55,.8);' +
      'backdrop-filter:blur(10px);}';
  document.head.appendChild(style);

  // Tabler icons — inject early so font is ready when sidebar renders
  if (!document.querySelector('link[href*="tabler-icons"]')) {
    var ico = document.createElement('link');
    ico.rel = 'stylesheet';
    ico.href = '/static/tabler-icons/tabler-icons.min.css';
    document.head.insertBefore(ico, document.head.firstChild);
  }

  // ── Build nav HTML ────────────────────────────────────────────────────────
  function buildNav() {
    return NAV.map(function (item) {
      if (item.divider) {
        return '<div class="ac-sb-div"></div>';
      }
      if (item.group) {
        if (item.collapsible) {
          return '<button id="ac-cfg-tog">' +
            '<span class="ac-sbl" style="flex:1;">' + item.group + '</span>' +
            '<i class="ti ti-chevron-right ac-sbl" id="ac-cfg-chev" ' +
              'style="font-size:10px;margin-right:2px;flex-shrink:0;transition:transform .15s;"></i>' +
            '</button>';
        }
        return '<div class="ac-sbg">' + item.group + '</div>';
      }
      if (item.external) {
        return '<a href="' + item.href + '" target="_blank" rel="noopener" class="ac-sbi">' +
          '<i class="ti ' + item.icon + ' ac-sbi-ic"></i>' +
          '<span class="ac-sbl">' + item.label + '</span>' +
          '<i class="ti ti-external-link ac-sbl" style="font-size:9px;margin-left:auto;flex-shrink:0;opacity:.5;"></i>' +
          '</a>';
      }
      var on = isActive(item);
      var cls = 'ac-sbi' + (on ? ' on' : '') + (item.cfg ? ' ac-cfg-item' : '');
      var i18n = item.i18n ? ' data-i18n="' + item.i18n + '"' : '';
      return (
        '<a href="' + item.href + '" class="' + cls + '"' + i18n + '>' +
        '<i class="ti ' + item.icon + ' ac-sbi-ic"></i>' +
        '<span class="ac-sbl"' + i18n + '>' + item.label + '</span>' +
        (item.badge
          ? '<span id="' + item.badge + '" style="display:none;margin-left:auto;min-width:18px;height:18px;' +
            'padding:0 4px;border-radius:9px;background:#f59e0b;color:#000;font-size:11px;font-weight:700;' +
            'flex-shrink:0;align-items:center;justify-content:center;box-sizing:border-box;"></span>'
          : '') +
        '</a>'
      );
    }).join('');
  }

  // ── Assemble sidebar ──────────────────────────────────────────────────────
  var w0 = collapsed ? COL : EXP;
  var sb = document.createElement('aside');
  sb.id = 'ac-sb';
  sb.style.width = w0 + 'px';

  // Header
  var hdr = document.createElement('div');
  hdr.id = 'ac-sb-hdr';
  hdr.style.cssText = 'display:flex;align-items:center;justify-content:space-between;height:48px;' +
    'padding:0 8px 0 14px;flex-shrink:0;border-bottom:1px solid rgba(31,41,55,.5);';
  hdr.innerHTML =
    '<a href="/dashboard" id="ac-sb-la" style="flex:1;display:flex;align-items:center;' +
      'gap:8px;text-decoration:none;overflow:hidden;min-width:0;">' +
      '<img id="ac-sb-ant" src="/static/favicon.svg" alt="antcrew" ' +
        'style="width:24px;height:24px;flex-shrink:0;display:none;">' +
      '<span id="ac-sb-lt" style="font-family:monospace;font-weight:700;color:#818cf8;' +
        'font-size:.875rem;white-space:nowrap;overflow:hidden;flex:1;">antcrew</span>' +
    '</a>' +
    '<button id="ac-sb-tog" title="Collapse sidebar" style="width:28px;height:28px;border:none;' +
      'background:none;cursor:pointer;color:rgb(107,114,128);display:flex;align-items:center;' +
      'justify-content:center;border-radius:4px;flex-shrink:0;padding:0;">' +
      '<i id="ac-sb-tog-ic" class="ti ti-layout-sidebar-left-collapse" style="font-size:18px;"></i>' +
    '</button>';
  sb.appendChild(hdr);

  // Scroll area
  var navEl = document.createElement('div');
  navEl.style.cssText = 'flex:1;overflow-y:auto;overflow-x:hidden;padding:6px 0;' +
    'scrollbar-width:thin;scrollbar-color:rgba(31,41,55,1) transparent;';
  navEl.innerHTML = buildNav();
  sb.appendChild(navEl);

  // Footer (user card + menu)
  var foot = document.createElement('div');
  foot.id = 'ac-sb-foot';
  foot.style.cssText = 'flex-shrink:0;border-top:1px solid rgba(31,41,55,.5);padding:6px;';
  foot.innerHTML =
    '<div id="ac-sb-u" style="display:flex;align-items:center;gap:8px;padding:5px 6px;' +
      'border-radius:6px;cursor:pointer;overflow:hidden;" title="Account">' +
      '<div id="ac-sb-av" style="width:26px;height:26px;border-radius:50%;background:#4338ca;' +
        'color:#fff;font-size:10px;font-weight:700;display:flex;align-items:center;' +
        'justify-content:center;flex-shrink:0;line-height:1;">?</div>' +
      '<div class="ac-sbl" style="min-width:0;flex:1;overflow:hidden;">' +
        '<p id="ac-sb-un" style="font-size:.8rem;font-weight:500;color:#f3f4f6;overflow:hidden;' +
          'text-overflow:ellipsis;white-space:nowrap;margin:0;line-height:1.35;"></p>' +
        '<p id="ac-sb-ue" style="font-size:.67rem;color:#6b7280;overflow:hidden;' +
          'text-overflow:ellipsis;white-space:nowrap;margin:0;line-height:1.35;"></p>' +
      '</div>' +
      '<i id="ac-sb-chev" class="ti ti-chevron-up ac-sbl" style="font-size:10px;color:#6b7280;' +
        'flex-shrink:0;transition:transform .15s;"></i>' +
    '</div>' +
    '<div id="ac-sb-umenu" style="display:none;background:#111827;border:1px solid #1f2937;' +
      'border-radius:6px;overflow:hidden;margin:4px 0 2px;">' +
      '<a href="/settings?tab=profile" class="ac-sbi" ' +
        'style="margin:2px 4px;font-size:.8rem;" data-i18n="nav.profile">' +
        '<i class="ti ti-user ac-sbi-ic"></i>' +
        '<span data-i18n="nav.profile">My profile</span></a>' +
      '<button id="ac-sb-logout" class="ac-sbi" ' +
        'style="margin:2px 4px;width:calc(100% - 8px);background:none;border:none;' +
        'text-align:left;font-size:.8rem;">' +
        '<i class="ti ti-logout ac-sbi-ic"></i>' +
        '<span data-i18n="nav.logout">Log out</span>' +
      '</button>' +
      '<div style="padding:5px 10px;border-top:1px solid #1f2937;display:flex;align-items:center;gap:4px;">' +
        '<span data-i18n="nav.lang" style="font-size:.67rem;color:#4b5563;flex:1;">Language</span>' +
        '<button data-lang-btn="en" onclick="window.__i18n&&window.__i18n.setLang(\'en\')" ' +
          'style="font-size:.72rem;background:none;border:none;cursor:pointer;padding:2px 5px;' +
          'border-radius:3px;color:#9ca3af;">EN</button>' +
        '<span style="font-size:.67rem;color:#374151;">|</span>' +
        '<button data-lang-btn="es" onclick="window.__i18n&&window.__i18n.setLang(\'es\')" ' +
          'style="font-size:.72rem;background:none;border:none;cursor:pointer;padding:2px 5px;' +
          'border-radius:3px;color:#9ca3af;">ES</button>' +
      '</div>' +
    '</div>';
  sb.appendChild(foot);

  document.body.insertBefore(sb, document.body.firstChild);
  document.body.style.paddingLeft = w0 + 'px';

  // Mobile bar
  var mbar = document.createElement('div');
  mbar.id = 'ac-sb-mbar';
  mbar.innerHTML =
    '<button id="ac-sb-mbtog" style="width:32px;height:32px;border:none;background:none;' +
      'cursor:pointer;color:#9ca3af;display:flex;align-items:center;justify-content:center;' +
      'border-radius:4px;padding:0;">' +
      '<i class="ti ti-menu-2" style="font-size:20px;"></i></button>' +
    '<a href="/dashboard" style="font-family:monospace;font-weight:700;color:#818cf8;' +
      'font-size:.875rem;text-decoration:none;">antcrew</a>' +
    '<span id="sb-mb-rev-badge" style="display:none;margin-left:auto;min-width:20px;height:20px;' +
      'padding:0 5px;border-radius:10px;background:#f59e0b;color:#000;font-size:11px;' +
      'font-weight:700;align-items:center;justify-content:center;box-sizing:border-box;"></span>';
  document.body.insertBefore(mbar, sb.nextSibling);

  // Backdrop
  var bd = document.createElement('div');
  bd.id = 'ac-sb-bd';
  document.body.insertBefore(bd, mbar.nextSibling);

  // ── Configurar group state ─────────────────────────────────────────────────
  function applyCfgState(open) {
    cfgOpen = open;
    localStorage.setItem(GK, open ? '1' : '0');
    var chev = document.getElementById('ac-cfg-chev');
    var tog = document.getElementById('ac-cfg-tog');
    var items = sb.querySelectorAll('.ac-cfg-item');

    // In collapsed sidebar: always show cfg items (icons only via .ac-sbl hiding)
    // In expanded sidebar: show based on open state
    items.forEach(function (el) {
      el.style.display = (collapsed || open) ? 'flex' : 'none';
    });

    if (chev) chev.style.transform = open ? 'rotate(90deg)' : '';
    // Hide the group header button when sidebar is collapsed
    if (tog) {
      tog.style.maxHeight = collapsed ? '0' : '40px';
      tog.style.opacity = collapsed ? '0' : '1';
      tog.style.padding = collapsed ? '0 14px' : '';
      tog.style.overflow = 'hidden';
    }
  }

  // ── Collapse / expand ─────────────────────────────────────────────────────
  function applyState(c) {
    collapsed = c;
    localStorage.setItem(SK, c ? '1' : '0');
    sb.style.width = (c ? COL : EXP) + 'px';
    document.body.style.paddingLeft = (c ? COL : EXP) + 'px';

    // Header: show ant SVG when collapsed, antcrew text when expanded
    var hdrEl = document.getElementById('ac-sb-hdr');
    var ant = document.getElementById('ac-sb-ant');
    var lt = document.getElementById('ac-sb-lt');
    var tog = document.getElementById('ac-sb-tog');

    if (hdrEl) {
      hdrEl.style.justifyContent = c ? 'center' : 'space-between';
      hdrEl.style.padding = c ? '0' : '0 8px 0 14px';
    }
    if (ant) ant.style.display = c ? 'block' : 'none';
    if (lt) lt.style.display = c ? 'none' : '';
    if (tog) tog.style.display = c ? 'none' : 'flex';

    var togIc = document.getElementById('ac-sb-tog-ic');
    if (togIc) togIc.className = 'ti ' + (c ? 'ti-layout-sidebar-left-expand' : 'ti-layout-sidebar-left-collapse');

    // Labels and group headers (non-collapsible)
    sb.querySelectorAll('.ac-sbl').forEach(function (el) {
      el.style.maxWidth = c ? '0' : '';
      el.style.opacity = c ? '0' : '1';
    });
    sb.querySelectorAll('.ac-sbg').forEach(function (el) {
      el.style.maxHeight = c ? '0' : '40px';
      el.style.opacity = c ? '0' : '1';
      el.style.padding = c ? '0 14px' : '';
    });

    // Re-apply cfg group state
    applyCfgState(cfgOpen);

    if (c) {
      var um = document.getElementById('ac-sb-umenu');
      if (um) um.style.display = 'none';
      var userChev = document.getElementById('ac-sb-chev');
      if (userChev) userChev.style.transform = '';
    }
  }

  // Initial render
  applyState(collapsed);

  // Logo link: navigate normally when expanded; expand sidebar when collapsed
  document.getElementById('ac-sb-la').addEventListener('click', function (e) {
    if (collapsed) {
      e.preventDefault();
      applyState(false);
    }
  });

  document.getElementById('ac-sb-tog').addEventListener('click', function () {
    applyState(!collapsed);
  });

  // Configurar toggle
  var cfgTogEl = document.getElementById('ac-cfg-tog');
  if (cfgTogEl) {
    cfgTogEl.addEventListener('click', function () {
      applyCfgState(!cfgOpen);
    });
  }

  // User menu
  var uEl = document.getElementById('ac-sb-u');
  var umEl = document.getElementById('ac-sb-umenu');
  var chevEl = document.getElementById('ac-sb-chev');
  uEl.addEventListener('mouseover', function () { uEl.style.background = 'rgba(31,41,55,.5)'; });
  uEl.addEventListener('mouseout', function () { uEl.style.background = ''; });
  uEl.addEventListener('click', function () {
    if (collapsed) { applyState(false); return; }
    var open = umEl.style.display !== 'none';
    umEl.style.display = open ? 'none' : 'block';
    if (chevEl) chevEl.style.transform = open ? '' : 'rotate(180deg)';
  });

  document.getElementById('ac-sb-logout').addEventListener('click', function () {
    fetch('/auth/token', { method: 'DELETE', credentials: 'same-origin' })
      .then(function () { localStorage.removeItem('antcrew_api_key'); window.location.replace('/login'); });
  });

  document.addEventListener('click', function (e) {
    if (umEl && foot && !foot.contains(e.target)) umEl.style.display = 'none';
  });

  // Mobile toggle
  document.getElementById('ac-sb-mbtog').addEventListener('click', function () {
    sb.classList.toggle('open');
    bd.classList.toggle('open');
  });
  bd.addEventListener('click', function () {
    sb.classList.remove('open');
    bd.classList.remove('open');
  });

  // ── Data fetches ──────────────────────────────────────────────────────────
  var apiKey = localStorage.getItem('antcrew_api_key');
  var hdrs = { 'Content-Type': 'application/json' };
  if (apiKey) hdrs['X-Api-Key'] = apiKey;

  fetch('/reviews/?status=pending&limit=100', { headers: hdrs, credentials: 'same-origin' })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) {
      if (!d || !d.length) return;
      var cnt = d.length > 99 ? '99+' : String(d.length);
      ['sb-rev-badge', 'sb-mb-rev-badge'].forEach(function (id) {
        var el = document.getElementById(id);
        if (el) { el.textContent = cnt; el.style.display = 'inline-flex'; }
      });
    })
    .catch(function () {});

  fetch('/auth/me', { credentials: 'same-origin' })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (me) {
      if (!me) return;
      var un = document.getElementById('ac-sb-un');
      var ue = document.getElementById('ac-sb-ue');
      var av = document.getElementById('ac-sb-av');
      if (un) un.textContent = me.display_name || '';
      if (ue) ue.textContent = me.email || '';
      if (av) {
        var s = (me.display_name || me.email || '').trim();
        var pts = s.split(/\s+/);
        av.textContent = (pts.length > 1 ? pts[0][0] + pts[pts.length - 1][0] : s.slice(0, 2)).toUpperCase();
      }
    })
    .catch(function () {});

  // Re-apply i18n after sidebar injects data-i18n elements
  if (window.__i18n) {
    window.__i18n.applyAll();
  } else {
    document.addEventListener('i18nReady', function () {
      if (window.__i18n) window.__i18n.applyAll();
    });
  }
})();
