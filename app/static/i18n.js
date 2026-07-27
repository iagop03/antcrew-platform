(function () {
  // Inject [x-cloak] rule + page fade-in once (before any content paints).
  // Loaded synchronously by every page, so this fires before Alpine or Tailwind.
  (function () {
    var s = document.createElement('style');
    s.textContent =
      '[x-cloak]{display:none!important}' +
      '@keyframes _ac_fadein{from{opacity:0;transform:translateY(3px)}to{opacity:1;transform:translateY(0)}}' +
      'body{animation:_ac_fadein .15s ease-out}';
    document.head.appendChild(s);
  })();

  var DICT = {
    en: {
      /* nav / profile dropdown */
      'nav.profile':    'My profile',
      'nav.logout':     'Log out',
      'nav.lang':       'Language',
      'nav.settings':   'Settings',
      'nav.dashboard':  'Dashboard',
      'nav.runs':       'Runs',
      'nav.reviews':    'Reviews',
      'nav.evals':      'Evals',
      'nav.tickets':    'Tickets',
      'nav.webhooks':   'Webhooks',
      'nav.compare':    'Compare',
      'nav.pipelines':  'Pipelines',

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
      'ws.yes':         'Yes',
      'ws.no':          'No',

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
      'keys.copynow':   'Key created — copy it now, it won\'t be shown again',

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

      /* settings page */
      'settings.title':     'Settings',
      'settings.subtitle':  'Workspaces, reviewers and LLM configuration',
      'settings.api_keys':  'API Keys',
      'settings.webhooks':  'Webhooks',
      'settings.workspace': 'Workspace',
      'settings.team':      'Team',
      'settings.model':     'Model',

      /* common */
      'common.loading':  'Loading…',
      'common.no_data':  'No data',
      'common.error':    'Error',
      'common.save':     'Save',
      'common.cancel':   'Cancel',
      'common.delete':   'Delete',
      'common.create':   'Create',
      'common.submit':   'Submit',
      'common.refresh':  'Refresh',
      'common.retry':    'Retry',
      'common.close':    'Close',
      'common.confirm':  'Confirm',
      'common.search':   'Search',
      'common.filter':   'Filter',
      'common.all':      'All',
      'common.none':     'None',
      'common.yes':      'Yes',
      'common.no':       'No',
      'common.view':     'View',
      'common.edit':     'Edit',
      'common.export':   'Export',
      'btn.save':        'Save',
      'btn.cancel':      'Cancel',
      'btn.delete':      'Delete',
      'btn.edit':        'Edit',
      'btn.new':         'New',
      'btn.copy':        'Copy',
      'loading':         'Loading…',

      /* status */
      'status.running':   'running',
      'status.success':   'success',
      'status.error':     'error',
      'status.cancelled': 'cancelled',
      'status.pending':   'pending',
      'status.approved':  'approved',
      'status.rejected':  'rejected',
      'status.done':      'done',
      'status.active':    'active',
      'status.paused':    'paused',
      'status.pass':      'PASS',
      'status.fail':      'FAIL',

      /* runs page */
      'runs.title':               'Pipeline Runs',
      'runs.subtitle':            'Live updates via WebSocket',
      'runs.new':                 '+ New Run',
      'runs.empty':               'No runs yet. Click "+ New Run" to start one.',
      'runs.filter.label':        'Filter:',
      'runs.filter.all':          'All',
      'runs.filter.teams':        'Teams',
      'runs.filter.pipelines':    'Pipelines',
      'runs.filter.engine':       'Engine',
      'runs.col.run_id':          'Run ID',
      'runs.col.team':            'Team',
      'runs.col.request':         'Request',
      'runs.col.status':          'Status',
      'runs.col.cost':            'Cost',
      'runs.col.duration':        'Duration',
      'runs.col.started':         'Started',
      'runs.stats.total':         'Total',
      'runs.stats.running':       'Running',
      'runs.stats.success':       'Success',
      'runs.stats.error':         'Error',
      'runs.modal.title':         'New Run',
      'runs.modal.team_run':      'Team run',
      'runs.modal.engine_run':    'Engine run',
      'runs.modal.start':         'Start Run',
      'runs.modal.resume':        'Resume',
      'runs.modal.starting':      'Starting…',
      'runs.modal.load_template': 'Load from template',
      'runs.modal.save_template': 'Save template',
      'runs.modal.template_name': 'Template name (optional)',
      'runs.modal.hitl':          'Enable HITL — pause for human review at each checkpoint',
      'runs.modal.max_cost':      'Max cost (USD, optional)',
      'runs.modal.repo_url':      'Repo URL (optional)',
      'runs.modal.goal':          'Goal',
      'runs.modal.tech':          'Tech stack (comma-separated, optional)',
      'runs.modal.output_dir':    'Output dir',
      'runs.modal.source_dir':    'Source dir',
      'runs.modal.full_pipeline': 'Full pipeline — code + tests + review (uncheck for plan-only)',
      'runs.modal.resume_toggle': 'Resume — continue a previous run from output dir',

      /* reviews page */
      'rev.title':                      'HITL Review Queue',
      'rev.subtitle':                   'Approve or reject agent output — the pipeline resumes immediately.',
      'rev.signedin':                   'Signed in as',
      'rev.approve':                    'Approve',
      'rev.reject':                     'Reject',
      'rev.claim':                      'Claim',
      'rev.pending':                    'Pending',
      'rev.mine':                       'Mine',
      'rev.all':                        'All',
      'reviews.title':                  'HITL Review Queue',
      'reviews.subtitle':               'Approve or reject agent output — the pipeline resumes immediately.',
      'reviews.signed_in_as':           'Signed in as',
      'reviews.signed_as':              'Signed in as',
      'reviews.tab.mine':               'My queue',
      'reviews.tab.all':                'All',
      'reviews.tab.analytics':          'Analytics',
      'reviews.analytics.total':        'Total Reviews',
      'reviews.analytics.pending':      'Pending',
      'reviews.analytics.rejected':     'Rejected',
      'reviews.analytics.rejection_rate': 'Rejection Rate',
      'reviews.analytics.by_checkpoint': 'Rejection rate by checkpoint',
      'reviews.analytics.by_resolver':  'Decisions by resolver',
      'reviews.mine.empty':             'No pending reviews for you',
      'reviews.all.empty':              'No pending reviews',
      'reviews.empty.mine':             'You have no pending reviews',
      'reviews.empty.all':              'No pending reviews',
      'reviews.claim':                  'Claim',
      'reviews.submit_edit':            'Submit edit',
      'reviews.feedback_placeholder':   'Optional note sent alongside any decision…',
      'reviews.col.id':                 'ID',
      'reviews.col.title':              'Title',
      'reviews.col.priority':           'Priority',

      /* evals page */
      'evals.title':                  'Pipeline Evals',
      'evals.subtitle':               'Run quality checks against your teams without manual review.',
      'evals.new':                    '+ New Eval',
      'evals.empty':                  'No eval runs yet. Click "+ New Eval" to score a pipeline.',
      'evals.compare_selected':       'Compare 2 selected',
      'evals.col.name':               'Name / Request',
      'evals.col.team':               'Team',
      'evals.col.status':             'Status',
      'evals.col.score':              'Score',
      'evals.col.cost':               'Cost',
      'evals.col.time':               'Time',
      'evals.col.started':            'Started',
      'evals.col.cmp':                'cmp',
      'evals.modal.title':            'New Eval Run',
      'evals.modal.request':          'Request / task',
      'evals.modal.name':             'Name (optional)',
      'evals.modal.run':              'Run Eval',
      'evals.modal.starting':         'Starting…',
      'evals.schedules.title':        'Recurring Schedules',
      'evals.schedules.new':          '+ New Schedule',
      'evals.schedules.empty':        'No schedules yet.',
      'evals.schedules.col.name':     'Name',
      'evals.schedules.col.team':     'Team',
      'evals.schedules.col.every':    'Every',
      'evals.schedules.col.next_run': 'Next run',
      'evals.schedules.col.status':   'Status',
      'evals.schedules.col.actions':  'Actions',
      'evals.schedules.pause':        'Pause',
      'evals.schedules.resume':       'Resume',
      'evals.regression.title':       'Regression Tests',
      'evals.regression.subtitle':    '(prompt drift detection)',
      'evals.agent_scores':           'Agent scores',

      /* tickets page */
      'tickets.title':                'Tickets',
      'tickets.subtitle':             'Kanban board — click a card to move it',
      'tickets.search':               'Search tickets…',
      'tickets.empty':                'Empty',
      'tickets.col.open':             'Open',
      'tickets.col.in_progress':      'In Progress',
      'tickets.col.done':             'Done',
      'tickets.col.blocked':          'Blocked',
      'tickets.move_to':              'Move to',
      'tickets.export_to':            'Export to',
      'tickets.acceptance_criteria':  'Acceptance criteria',
      'tickets.view_run':             'View run →',

      /* compare page */
      'compare.title':        'Compare Evals',
      'compare.baseline':     'Baseline',
      'compare.candidate':    'Candidate',
      'compare.overall_score': 'Overall score',
      'compare.delta':        'Delta',
      'compare.improved':     'Improvement detected — candidate scores higher than baseline.',
      'compare.regression':   'Regression detected — candidate scores lower than baseline.',
      'compare.no_change':    'No significant change detected.',
      'compare.cost_time':    'Cost / Time',

      /* forms */
      'form.team':             'Team',
      'form.model':            'Model',
      'form.request':          'Request',
      'form.name':             'Name',
      'form.api_key':          'API Key',
      'form.email':            'Email',
      'form.password':         'Password',
      'form.confirm_password': 'Confirm password',

      /* budget */
      'budget.exhausted': 'Budget exhausted',
      'budget.left':      'left',
      'budget.label':     'budget',

      /* key modal */
      'key_modal.title':  'Platform API Key',
      'key_modal.stored': 'Stored in browser localStorage. Leave blank if auth is disabled (open mode).',
      'key_modal.clear':  'Clear',
      'key_modal.cancel': 'Cancel',
      'key_modal.save':   'Save',
      'key_modal.btn':    '🔑 Key',

      /* auth */
      'auth.login':            'Login',
      'auth.register':         'Register',
      'auth.email':            'Email',
      'auth.password':         'Password',
      'auth.confirm_password': 'Confirm password',
      'auth.sign_in':          'Sign in',
      'auth.create_account':   'Create account',
      'auth.logout':           'Logout',

      /* errors */
      'error.invalid_api_key': 'Invalid API key',
      'error.unauthorized':    'Unauthorized',
      'error.server':          'Server error',
      'error.load_runs':       'Failed to load runs',
      'error.load_tickets':    'Failed to load tickets',
    },

    es: {
      /* nav / profile dropdown */
      'nav.profile':    'Mi perfil',
      'nav.logout':     'Cerrar sesión',
      'nav.lang':       'Idioma',
      'nav.settings':   'Ajustes',
      'nav.dashboard':  'Dashboard',
      'nav.runs':       'Ejecuciones',
      'nav.reviews':    'Revisiones',
      'nav.evals':      'Evaluaciones',
      'nav.tickets':    'Tickets',
      'nav.webhooks':   'Webhooks',
      'nav.compare':    'Comparar',
      'nav.pipelines':  'Pipelines',

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
      'ws.yes':         'Sí',
      'ws.no':          'No',

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
      'keys.copynow':   'Clave creada — cópiala ahora, no se volverá a mostrar',

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

      /* settings page */
      'settings.title':     'Ajustes',
      'settings.subtitle':  'Workspaces, revisores y configuración LLM',
      'settings.api_keys':  'Claves API',
      'settings.webhooks':  'Webhooks',
      'settings.workspace': 'Espacio de trabajo',
      'settings.team':      'Equipo',
      'settings.model':     'Modelo',

      /* common */
      'common.loading':  'Cargando…',
      'common.no_data':  'Sin datos',
      'common.error':    'Error',
      'common.save':     'Guardar',
      'common.cancel':   'Cancelar',
      'common.delete':   'Eliminar',
      'common.create':   'Crear',
      'common.submit':   'Enviar',
      'common.refresh':  'Actualizar',
      'common.retry':    'Reintentar',
      'common.close':    'Cerrar',
      'common.confirm':  'Confirmar',
      'common.search':   'Buscar',
      'common.filter':   'Filtrar',
      'common.all':      'Todos',
      'common.none':     'Ninguno',
      'common.yes':      'Sí',
      'common.no':       'No',
      'common.view':     'Ver',
      'common.edit':     'Editar',
      'common.export':   'Exportar',
      'btn.save':        'Guardar',
      'btn.cancel':      'Cancelar',
      'btn.delete':      'Eliminar',
      'btn.edit':        'Editar',
      'btn.new':         'Nuevo',
      'btn.copy':        'Copiar',
      'loading':         'Cargando…',

      /* status */
      'status.running':   'en ejecución',
      'status.success':   'éxito',
      'status.error':     'error',
      'status.cancelled': 'cancelado',
      'status.pending':   'pendiente',
      'status.approved':  'aprobado',
      'status.rejected':  'rechazado',
      'status.done':      'completado',
      'status.active':    'activo',
      'status.paused':    'pausado',
      'status.pass':      'APROBADO',
      'status.fail':      'FALLIDO',

      /* runs page */
      'runs.title':               'Ejecuciones de Pipeline',
      'runs.subtitle':            'Actualizaciones en tiempo real vía WebSocket',
      'runs.new':                 '+ Nueva Ejecución',
      'runs.empty':               'Sin ejecuciones aún. Haz clic en "+ Nueva Ejecución" para comenzar.',
      'runs.filter.label':        'Filtrar:',
      'runs.filter.all':          'Todos',
      'runs.filter.teams':        'Equipos',
      'runs.filter.pipelines':    'Pipelines',
      'runs.filter.engine':       'Motor',
      'runs.col.run_id':          'ID de Ejecución',
      'runs.col.team':            'Equipo',
      'runs.col.request':         'Solicitud',
      'runs.col.status':          'Estado',
      'runs.col.cost':            'Costo',
      'runs.col.duration':        'Duración',
      'runs.col.started':         'Iniciado',
      'runs.stats.total':         'Total',
      'runs.stats.running':       'En ejecución',
      'runs.stats.success':       'Exitoso',
      'runs.stats.error':         'Error',
      'runs.modal.title':         'Nueva Ejecución',
      'runs.modal.team_run':      'Ejecución de equipo',
      'runs.modal.engine_run':    'Ejecución de motor',
      'runs.modal.start':         'Iniciar Ejecución',
      'runs.modal.resume':        'Reanudar',
      'runs.modal.starting':      'Iniciando…',
      'runs.modal.load_template': 'Cargar desde plantilla',
      'runs.modal.save_template': 'Guardar plantilla',
      'runs.modal.template_name': 'Nombre de plantilla (opcional)',
      'runs.modal.hitl':          'Activar HITL — pausar para revisión humana en cada punto de control',
      'runs.modal.max_cost':      'Costo máximo (USD, opcional)',
      'runs.modal.repo_url':      'URL del repositorio (opcional)',
      'runs.modal.goal':          'Objetivo',
      'runs.modal.tech':          'Stack tecnológico (separado por comas, opcional)',
      'runs.modal.output_dir':    'Directorio de salida',
      'runs.modal.source_dir':    'Directorio fuente',
      'runs.modal.full_pipeline': 'Pipeline completo — código + tests + revisión (desmarcar para solo planificación)',
      'runs.modal.resume_toggle': 'Reanudar — continuar una ejecución anterior desde el directorio de salida',

      /* reviews page */
      'rev.title':                      'Cola de revisión HITL',
      'rev.subtitle':                   'Aprueba o rechaza el output del agente — el pipeline continúa inmediatamente.',
      'rev.signedin':                   'Sesión como',
      'rev.approve':                    'Aprobar',
      'rev.reject':                     'Rechazar',
      'rev.claim':                      'Reclamar',
      'rev.pending':                    'Pendiente',
      'rev.mine':                       'Mías',
      'rev.all':                        'Todas',
      'reviews.title':                  'Cola de revisión HITL',
      'reviews.subtitle':               'Aprueba o rechaza el output del agente — el pipeline continúa inmediatamente.',
      'reviews.signed_in_as':           'Sesión como',
      'reviews.signed_as':              'Sesión como',
      'reviews.tab.mine':               'Mi cola',
      'reviews.tab.all':                'Todas',
      'reviews.tab.analytics':          'Analytics',
      'reviews.analytics.total':        'Total Revisiones',
      'reviews.analytics.pending':      'Pendientes',
      'reviews.analytics.rejected':     'Rechazadas',
      'reviews.analytics.rejection_rate': 'Tasa de Rechazo',
      'reviews.analytics.by_checkpoint': 'Tasa de rechazo por punto de control',
      'reviews.analytics.by_resolver':  'Decisiones por resolutor',
      'reviews.mine.empty':             'No tienes reviews pendientes',
      'reviews.all.empty':              'No hay reviews pendientes',
      'reviews.empty.mine':             'No tienes revisiones pendientes',
      'reviews.empty.all':              'No hay revisiones pendientes',
      'reviews.claim':                  'Reclamar',
      'reviews.submit_edit':            'Enviar edición',
      'reviews.feedback_placeholder':   'Nota opcional enviada con la decisión…',
      'reviews.col.id':                 'ID',
      'reviews.col.title':              'Título',
      'reviews.col.priority':           'Prioridad',

      /* evals page */
      'evals.title':                  'Evaluaciones de Pipeline',
      'evals.subtitle':               'Ejecuta controles de calidad en tus equipos sin revisión manual.',
      'evals.new':                    '+ Nueva Evaluación',
      'evals.empty':                  'Sin evaluaciones aún. Haz clic en "+ Nueva Evaluación" para puntuar un pipeline.',
      'evals.compare_selected':       'Comparar 2 seleccionados',
      'evals.col.name':               'Nombre / Solicitud',
      'evals.col.team':               'Equipo',
      'evals.col.status':             'Estado',
      'evals.col.score':              'Puntuación',
      'evals.col.cost':               'Costo',
      'evals.col.time':               'Tiempo',
      'evals.col.started':            'Iniciado',
      'evals.col.cmp':                'cmp',
      'evals.modal.title':            'Nueva Evaluación',
      'evals.modal.request':          'Solicitud / tarea',
      'evals.modal.name':             'Nombre (opcional)',
      'evals.modal.run':              'Ejecutar Evaluación',
      'evals.modal.starting':         'Iniciando…',
      'evals.schedules.title':        'Horarios Recurrentes',
      'evals.schedules.new':          '+ Nuevo Horario',
      'evals.schedules.empty':        'Sin horarios aún.',
      'evals.schedules.col.name':     'Nombre',
      'evals.schedules.col.team':     'Equipo',
      'evals.schedules.col.every':    'Cada',
      'evals.schedules.col.next_run': 'Próxima ejecución',
      'evals.schedules.col.status':   'Estado',
      'evals.schedules.col.actions':  'Acciones',
      'evals.schedules.pause':        'Pausar',
      'evals.schedules.resume':       'Reanudar',
      'evals.regression.title':       'Tests de Regresión',
      'evals.regression.subtitle':    '(detección de deriva de prompts)',
      'evals.agent_scores':           'Puntuaciones de agente',

      /* tickets page */
      'tickets.title':               'Tickets',
      'tickets.subtitle':            'Tablero Kanban — haz clic en una tarjeta para moverla',
      'tickets.search':              'Buscar tickets…',
      'tickets.empty':               'Vacío',
      'tickets.col.open':            'Abierto',
      'tickets.col.in_progress':     'En Progreso',
      'tickets.col.done':            'Completado',
      'tickets.col.blocked':         'Bloqueado',
      'tickets.move_to':             'Mover a',
      'tickets.export_to':           'Exportar a',
      'tickets.acceptance_criteria': 'Criterios de aceptación',
      'tickets.view_run':            'Ver ejecución →',

      /* compare page */
      'compare.title':         'Comparar Evaluaciones',
      'compare.baseline':      'Base',
      'compare.candidate':     'Candidato',
      'compare.overall_score': 'Puntuación general',
      'compare.delta':         'Delta',
      'compare.improved':      'Mejora detectada — el candidato supera a la base.',
      'compare.regression':    'Regresión detectada — el candidato es inferior a la base.',
      'compare.no_change':     'No se detectó ningún cambio significativo.',
      'compare.cost_time':     'Costo / Tiempo',

      /* forms */
      'form.team':             'Equipo',
      'form.model':            'Modelo',
      'form.request':          'Solicitud',
      'form.name':             'Nombre',
      'form.api_key':          'Clave API',
      'form.email':            'Correo electrónico',
      'form.password':         'Contraseña',
      'form.confirm_password': 'Confirmar contraseña',

      /* budget */
      'budget.exhausted': 'Presupuesto agotado',
      'budget.left':      'restante',
      'budget.label':     'presupuesto',

      /* key modal */
      'key_modal.title':  'Clave API de Plataforma',
      'key_modal.stored': 'Almacenada en el localStorage del navegador. Deja en blanco si la autenticación está desactivada (modo abierto).',
      'key_modal.clear':  'Borrar',
      'key_modal.cancel': 'Cancelar',
      'key_modal.save':   'Guardar',
      'key_modal.btn':    '🔑 Clave',

      /* auth */
      'auth.login':            'Iniciar sesión',
      'auth.register':         'Registrarse',
      'auth.email':            'Correo electrónico',
      'auth.password':         'Contraseña',
      'auth.confirm_password': 'Confirmar contraseña',
      'auth.sign_in':          'Entrar',
      'auth.create_account':   'Crear cuenta',
      'auth.logout':           'Cerrar sesión',

      /* errors */
      'error.invalid_api_key': 'Clave API inválida',
      'error.unauthorized':    'No autorizado',
      'error.server':          'Error del servidor',
      'error.load_runs':       'Error al cargar ejecuciones',
      'error.load_tickets':    'Error al cargar tickets',
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
    }
  });

  // Apply on DOMContentLoaded
  document.addEventListener('DOMContentLoaded', applyI18n);
  // Re-apply after Alpine finishes init (processes x-teleport, x-show, etc.)
  document.addEventListener('alpine:initialized', applyI18n);
  // Also apply immediately for scripts that run after DOM is ready
  if (document.readyState !== 'loading') applyI18n();
})();
