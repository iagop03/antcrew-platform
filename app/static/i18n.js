(function () {
  var DICT = {
    en: {
      /* nav / profile dropdown */
      'nav.profile':  'My profile',
      'nav.logout':   'Log out',
      'nav.lang':     'Language',
      'nav.settings': 'Settings',

      /* settings — tabs */
      'tab.workspaces': 'Workspaces',
      'tab.reviewers':  'Reviewers',
      'tab.llm':        'LLM Mode',
      'tab.profile':    'My profile',

      /* settings — workspaces */
      'ws.all':         'All configured workspaces',
      'ws.none':        'No workspaces yet.',
      'ws.new':         'New',
      'ws.edit':        'Edit',
      'ws.delete':      'Delete',
      'ws.upgrade':     'Upgrade',
      'ws.name':        'Name',
      'ws.slug':        'Slug',
      'ws.budget':      'Budget',
      'ws.hitl':        'HITL',
      'ws.llm':         'LLM',
      'ws.actions':     'Actions',
      'ws.create':      'Create workspace',
      'ws.newTitle':    'New workspace',
      'ws.hitlDefault': 'Enable human review (HITL) by default',
      'ws.maxCost':     'Max budget in USD',
      'ws.maxCostHint': 'Leave empty for no limit',
      'ws.save':        'Save changes',
      'ws.cancel':      'Cancel',
      'ws.trial':       'Trial active',

      /* settings — reviewers / api keys */
      'keys.active':    'Active API keys',
      'keys.none':      'No API keys configured.',
      'keys.new':       'New key',
      'keys.newTitle':  'New API key',
      'keys.label':     'Label',
      'keys.role':      'Role',
      'keys.workspace': 'Workspace',
      'keys.notifs':    'Notifications',
      'keys.created':   'Created',
      'keys.actions':   'Actions',
      'keys.edit':      'Edit',
      'keys.revoke':    'Revoke',
      'keys.myAccount': 'Your account',
      'keys.create':    'Create key',
      'keys.editTitle': 'Edit role and notification channels',
      'keys.save':      'Save',

      /* settings — llm */
      'llm.title':      'BYOK keys configured',
      'llm.none':       'No BYOK keys. Workspace uses platform keys.',
      'llm.add':        'Add key',
      'llm.delete':     'Delete',
      'llm.addTitle':   'Add BYOK key',
      'llm.provider':   'Provider',
      'llm.apiKey':     'API key',
      'llm.baseUrl':    'Base URL',
      'llm.save':       'Save key',
      'llm.overwrite':  'Overwrite existing key',

      /* settings — profile */
      'prof.personal':  'Personal information',
      'prof.name':      'Full name',
      'prof.email':     'Email',
      'prof.save':      'Save',
      'prof.changePw':  'Change password',
      'prof.current':   'Current password',
      'prof.new':       'New password',
      'prof.change':    'Change password',
      'prof.saved':     'Name saved',
      'prof.pwSaved':   'Password updated',

      /* reviews */
      'rev.title':      'HITL Review Queue',
      'rev.subtitle':   'Approve or reject agent output — the pipeline resumes immediately.',
      'rev.signedin':   'Signed in as',
      'rev.approve':    'Approve',
      'rev.reject':     'Reject',
      'rev.claim':      'Claim',
      'rev.pending':    'Pending',
      'rev.mine':       'Mine',
      'rev.all':        'All',

      /* common */
      'btn.save':       'Save',
      'btn.cancel':     'Cancel',
      'btn.delete':     'Delete',
      'btn.edit':       'Edit',
      'btn.new':        'New',
      'loading':        'Loading…',
    },
    es: {
      /* nav / profile dropdown */
      'nav.profile':  'Mi perfil',
      'nav.logout':   'Cerrar sesión',
      'nav.lang':     'Idioma',
      'nav.settings': 'Ajustes',

      /* settings — tabs */
      'tab.workspaces': 'Workspaces',
      'tab.reviewers':  'Revisores',
      'tab.llm':        'Modo LLM',
      'tab.profile':    'Mi perfil',

      /* settings — workspaces */
      'ws.all':         'Todos los workspaces configurados',
      'ws.none':        'No hay workspaces todavía.',
      'ws.new':         'Nuevo',
      'ws.edit':        'Editar',
      'ws.delete':      'Eliminar',
      'ws.upgrade':     'Upgrade',
      'ws.name':        'Nombre',
      'ws.slug':        'Slug',
      'ws.budget':      'Presupuesto',
      'ws.hitl':        'HITL',
      'ws.llm':         'LLM',
      'ws.actions':     'Acciones',
      'ws.create':      'Crear workspace',
      'ws.newTitle':    'Nuevo workspace',
      'ws.hitlDefault': 'Activar revisión humana (HITL) por defecto',
      'ws.maxCost':     'Presupuesto máximo en USD',
      'ws.maxCostHint': 'Vacío = sin límite',
      'ws.save':        'Guardar cambios',
      'ws.cancel':      'Cancelar',
      'ws.trial':       'Trial activo',

      /* settings — reviewers / api keys */
      'keys.active':    'API keys activas',
      'keys.none':      'No hay API keys configuradas.',
      'keys.new':       'Nueva key',
      'keys.newTitle':  'Nueva API key',
      'keys.label':     'Label',
      'keys.role':      'Rol',
      'keys.workspace': 'Workspace',
      'keys.notifs':    'Notificaciones',
      'keys.created':   'Creada',
      'keys.actions':   'Acciones',
      'keys.edit':      'Editar',
      'keys.revoke':    'Revocar',
      'keys.myAccount': 'Tu cuenta',
      'keys.create':    'Crear key',
      'keys.editTitle': 'Editar rol y canales de notificación',
      'keys.save':      'Guardar',

      /* settings — llm */
      'llm.title':      'Claves BYOK configuradas',
      'llm.none':       'Sin claves BYOK. El workspace usa las claves de la plataforma.',
      'llm.add':        'Añadir clave',
      'llm.delete':     'Eliminar',
      'llm.addTitle':   'Añadir clave BYOK',
      'llm.provider':   'Proveedor',
      'llm.apiKey':     'API key',
      'llm.baseUrl':    'Base URL',
      'llm.save':       'Guardar clave',
      'llm.overwrite':  'Sobrescribir la clave existente',

      /* settings — profile */
      'prof.personal':  'Información personal',
      'prof.name':      'Nombre y apellidos',
      'prof.email':     'Email',
      'prof.save':      'Guardar',
      'prof.changePw':  'Cambiar contraseña',
      'prof.current':   'Contraseña actual',
      'prof.new':       'Nueva contraseña',
      'prof.change':    'Cambiar contraseña',
      'prof.saved':     'Nombre guardado',
      'prof.pwSaved':   'Contraseña actualizada',

      /* reviews */
      'rev.title':      'Cola de revisión HITL',
      'rev.subtitle':   'Aprueba o rechaza el output del agente — el pipeline continúa inmediatamente.',
      'rev.signedin':   'Sesión como',
      'rev.approve':    'Aprobar',
      'rev.reject':     'Rechazar',
      'rev.claim':      'Reclamar',
      'rev.pending':    'Pendiente',
      'rev.mine':       'Mías',
      'rev.all':        'Todas',

      /* common */
      'btn.save':       'Guardar',
      'btn.cancel':     'Cancelar',
      'btn.delete':     'Eliminar',
      'btn.edit':       'Editar',
      'btn.new':        'Nuevo',
      'loading':        'Cargando…',
    },
  };

  var STORAGE_KEY = 'ac_lang';

  function getLang() {
    return localStorage.getItem(STORAGE_KEY) || 'en';
  }

  function setLang(lang) {
    localStorage.setItem(STORAGE_KEY, lang);
    window.location.reload();
  }

  function t(key) {
    var lang = getLang();
    return (DICT[lang] && DICT[lang][key]) || (DICT['en'] && DICT['en'][key]) || key;
  }

  function applyI18n() {
    document.querySelectorAll('[data-i18n]').forEach(function (el) {
      var key = el.getAttribute('data-i18n');
      var val = t(key);
      if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
        el.placeholder = val;
      } else {
        el.textContent = val;
      }
    });
    document.querySelectorAll('[data-i18n-title]').forEach(function (el) {
      el.title = t(el.getAttribute('data-i18n-title'));
    });
    // Update lang toggle buttons
    var lang = getLang();
    document.querySelectorAll('[data-lang-btn]').forEach(function (el) {
      var active = el.getAttribute('data-lang-btn') === lang;
      el.style.color = active ? '#f3f4f6' : '#6b7280';
      el.style.fontWeight = active ? '600' : '400';
    });
  }

  // Expose globally
  window.__i18n = { t: t, setLang: setLang, getLang: getLang, applyI18n: applyI18n };
  window.__t = t;

  // Register Alpine magic $t when Alpine initialises
  document.addEventListener('alpine:init', function () {
    if (window.Alpine) {
      window.Alpine.magic('t', function () { return t; });
      // Re-apply whenever lang changes
      document.addEventListener('ac:langchange', function () {
        // Force Alpine to re-evaluate reactive expressions that use $t
        // by dispatching a harmless custom event Alpine can observe
      });
    }
  });

  // Apply on DOMContentLoaded
  document.addEventListener('DOMContentLoaded', applyI18n);
  // Also apply immediately for scripts that run after DOM is ready
  if (document.readyState !== 'loading') applyI18n();
})();
