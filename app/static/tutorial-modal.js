/* antcrew — tutorial modal (lazy-loaded on first Tutorial click) */
(function () {
  'use strict';

  // ── Slide data ─────────────────────────────────────────────────────────────
  var SLIDES = [
    {
      tag: '01 — Visión general',
      title: '¿Qué es antcrew?',
      body: 'antcrew orquesta <strong>equipos de agentes IA</strong> especializados que trabajan en secuencia para completar tareas de ingeniería. En vez de pedirle a un LLM que "haga todo", defines un <strong>pipeline</strong> con agentes que se pasan el estado entre sí: uno captura requisitos, otro escribe código, otro revisa, otro crea el PR.' +
            '<br><br>El mismo pipeline que resuelve un ticket hoy, lo resuelve igual de bien la próxima semana. <strong>Proceso repetible, no magia puntual.</strong>',
      flow: ['💬 Discover', '⚙️ Pipeline', '▶ Run', '👁 Review', '🔀 PR'],
      example: null,
      callout: '<strong>Diferencia clave vs un chatbot:</strong> los agentes tienen roles, contratos tipados de estado (qué consumen y qué producen), y cada step queda trazado en el TraceLog — puedes reproducir exactamente qué pasó.',
      calloutType: '',
      list: [
        '<strong>Discover</strong> — captura requisitos conversacionalmente antes de ejecutar',
        '<strong>Pipelines</strong> — define quién hace qué y en qué orden',
        '<strong>Runs</strong> — cada ejecución queda registrada con trazas completas',
        '<strong>Reviews</strong> — apruebas antes de que los agentes continúen',
        '<strong>Workspaces</strong> — separa proyectos, clientes o entornos',
        '<strong>BYOK</strong> — usa tu propia clave de cualquier proveedor LLM',
      ],
    },
    {
      tag: '02 — Discover',
      title: 'Capturar requisitos antes de ejecutar',
      body: 'Discover es un agente conversacional que te hace hasta N preguntas (configurable 2–20) para entender el problema antes de lanzar el pipeline. Cada respuesta refina un <strong>DiscoveryContext</strong> — un objeto estructurado con project name, problem statement, usuarios, tech stack y features clave.' +
            '<br><br>Al completarse genera un <strong>PRD</strong> automáticamente que se convierte en el input del primer step.',
      example: {
        label: 'Sesión de Discover — Font Jardineria',
        html: '<span class="dim">Starting discovery (up to 7 questions)…</span>\n\n' +
              '<span class="hi">Q1 ›</span> ¿Qué problema resuelve este proyecto?\n' +
              '<span class="ok">&gt; </span>Una plataforma para gestionar el catálogo de plantas\n\n' +
              '<span class="hi">Q2 ›</span> ¿Quiénes son los usuarios principales?\n' +
              '<span class="ok">&gt; </span>Empleados de la tienda y clientes que buscan plants online\n\n' +
              '<span class="hi">Q3 ›</span> ¿Cuáles son las 3-5 funcionalidades clave?\n' +
              '<span class="ok">&gt; </span>Catálogo con filtros, stock, pedidos online, fichas de cuidado\n\n' +
              '<span class="dim">Discovery complete — generating PRD…</span>\n' +
              '<span class="ok">✓</span> PRD: <span class="hi">Font Jardineria Catalog Platform</span>\n' +
              '<span class="dim">4 rondas · 6 requisitos funcionales · 0 preguntas abiertas</span>',
      },
      callout: '<strong>Configuración:</strong> el slider de "Máximo X rondas" controla cuántas preguntas se hacen. Con 2 es rápido pero superficial; con 7–10 el PRD sale mucho más rico y reduce alucinaciones en steps posteriores.',
      calloutType: '',
      list: [
        'Funciona también desde terminal con <code>agent.run_interactive()</code> — sin plataforma',
        'Si ya tienes contexto, pásalo directamente al pipeline y salta las preguntas',
        'El PRD incluye: goals, functional requirements, non-functional requirements, out-of-scope',
      ],
    },
    {
      tag: '03 — Pipelines',
      title: 'Equipos de agentes con roles definidos',
      body: 'Un <strong>pipeline</strong> es una secuencia de agentes. Cada agente declara qué claves del estado <strong>consume</strong> (ej. <code>prd</code>) y cuáles <strong>produce</strong> (ej. <code>code_files</code>). Si falta un campo requerido, el pipeline falla limpiamente antes de gastar tokens.' +
            '<br><br>Hay equipos predefinidos como <strong>DevTeam</strong> (discovery → backend → frontend → tests → review), pero puedes componer el tuyo propio.',
      example: {
        label: 'Definición de pipeline personalizado',
        html: '<span class="dim"># Python — componer un equipo propio</span>\n' +
              '<span class="hi">from</span> antcrew <span class="hi">import</span> build_llm\n' +
              '<span class="hi">from</span> antcrew.agents <span class="hi">import</span> DiscoveryAgent, BackendDevAgent, ReviewAgent\n\n' +
              'llm = build_llm(<span class="ok">"deepseek:deepseek-chat"</span>)  <span class="dim"># cualquier proveedor</span>\n\n' +
              '<span class="hi">class</span> <span class="warn">MyTeam</span>(CustomTeam):\n' +
              '  steps = [\n' +
              '    DiscoveryAgent(llm),\n' +
              '    BackendDevAgent(llm),\n' +
              '    ReviewAgent(llm),\n' +
              '  ]\n\n' +
              'result = MyTeam().run({})  <span class="dim"># dispara Discover en terminal</span>',
      },
      callout: '<strong>Modelos por agente:</strong> cada step puede usar un modelo distinto. Agentes rápidos con Groq, razonamiento con Claude, código económico con DeepSeek.',
      calloutType: '',
      list: [
        'Los pipelines se disparan manualmente, por <strong>webhook</strong> de GitHub, o por <strong>schedule</strong> (cron)',
        'El campo <code>request</code> del Run es el texto libre que recibe el primer agente',
        'antcrew incluye <strong>TraceLog</strong> — cada step queda registrado con prompt, respuesta y duración',
      ],
    },
    {
      tag: '04 — Runs',
      title: 'Cada ejecución queda trazada por completo',
      body: 'Un <strong>Run</strong> se crea cada vez que ejecutas un pipeline. Tiene un estado (<code>pending</code> → <code>running</code> → <code>complete</code> / <code>failed</code>) y guarda el <strong>TraceLog</strong> — historial completo de qué hizo cada agente: prompts, respuestas, herramientas usadas, tiempo de cada step.',
      example: {
        label: 'Lista de Runs — workspace Font Jardineria',
        html: '<span class="dim">run_id              status      pipeline      started       duración</span>\n' +
              '<span class="dim">──────────────────────────────────────────────────────────────</span>\n' +
              '<span class="hi">run_a1b2c3d4</span>  <span class="ok">complete</span>    DevTeam       hace 2 h      4m 12s\n' +
              '<span class="hi">run_e5f6g7h8</span>  <span class="warn">running </span>    ResearchTeam  hace 5 min    …\n' +
              '<span class="hi">run_i9j0k1l2</span>  <span class="red">failed  </span>    DevTeam       ayer          1m 03s\n\n' +
              '<span class="dim">▸ run_a1b2c3d4 — TraceLog</span>\n' +
              '  <span class="ok">[step 1]</span> DiscoveryAgent   → prd generated       <span class="warn">8.2s</span>\n' +
              '  <span class="ok">[step 2]</span> BackendDevAgent  → 12 files written    <span class="warn">42.1s</span>\n' +
              '  <span class="ok">[step 3]</span> ReviewAgent      → HITL pause          <span class="dim">—</span>',
      },
      callout: '<strong>Replay:</strong> el TraceLog permite reproducir exactamente qué pasó en un run fallido. No necesitas reproducir el bug — ya está capturado. Abre el run y mira el trace paso a paso.',
      calloutType: '',
      list: [
        'Los runs fallidos muestran el error exacto del step que falló y el prompt que lo causó',
        'El output de un run puede usarse como input de otro — encadenamiento manual',
        'El campo <code>created_by</code> registra quién disparó el run (usuario, webhook o schedule)',
      ],
    },
    {
      tag: '05 — Reviews (HITL)',
      title: 'Tú apruebas antes de que los agentes continúen',
      body: '<strong>HITL</strong> (Human-in-the-Loop): ciertos steps del pipeline pueden pausar y esperar tu aprobación. Cuando hay una review pendiente aparece el <strong>badge ámbar</strong> en la barra lateral.' +
            '<br><br>Cada review muestra qué generó el agente y te da tres opciones: <strong>aprobar</strong> (el pipeline continúa), <strong>rechazar</strong> (el run termina), o <strong>pedir cambios</strong> (el agente vuelve a intentarlo con tu feedback como contexto adicional).',
      example: {
        label: 'Review pendiente — BackendDevAgent output',
        html: '<span class="warn">⏸ Review #47</span>  ·  run_a1b2c3d4  ·  BackendDevAgent\n' +
              '<span class="dim">──────────────────────────────────────────────────────────</span>\n' +
              '<span class="dim">El agente ha generado:</span>\n\n' +
              '  <span class="ok">+</span> app/api/catalog.py         <span class="dim">(234 líneas)</span>\n' +
              '  <span class="ok">+</span> app/models/plant.py        <span class="dim">(67 líneas)</span>\n' +
              '  <span class="ok">+</span> tests/test_catalog.py      <span class="dim">(89 líneas)</span>\n\n' +
              '<span class="dim">Feedback (opcional):</span>\n' +
              '<span class="dim">┌─────────────────────────────────────────────┐</span>\n' +
              '<span class="dim">│</span> Añade paginación al endpoint GET /plants    <span class="dim">│</span>\n' +
              '<span class="dim">└─────────────────────────────────────────────┘</span>\n\n' +
              '<span class="ok">[ Aprobar ]</span>  <span class="warn">[ Pedir cambios ]</span>  <span class="red">[ Rechazar ]</span>',
      },
      callout: '<strong>Reviewers API key:</strong> puedes dar acceso de solo revisión a otros miembros del equipo con una API key limitada a un workspace. Van a <em>Settings → Reviewers</em>.',
      calloutType: '',
      list: [
        '"Pedir cambios" añade tu comentario al estado — el agente lo usa como contexto adicional en el siguiente intento',
        'Configuras qué steps requieren HITL al definir el pipeline con <code>require_review=True</code>',
        'Los workspaces con HITL=no ejecutan sin pausas — útil para pipelines automatizados de confianza',
      ],
    },
    {
      tag: '06 — Tickets',
      title: 'Tareas generadas por los agentes durante la ejecución',
      body: 'Mientras un pipeline se ejecuta, los agentes pueden crear <strong>tickets</strong> — unidades discretas de trabajo que identifican pero quedan fuera del scope del run actual. Es la forma en que los agentes te dicen "esto también habría que hacerlo".' +
            '<br><br>Desde Tickets puedes ver, filtrar y gestionar todas las tareas generadas, o convertirlas en el <code>request</code> de un Run nuevo.',
      example: {
        label: 'Tickets generados — run_a1b2c3d4',
        html: '<span class="warn">pendiente</span>  <span class="hi">TKT-001</span>  Añadir autenticación OAuth para clientes\n' +
              '           <span class="dim">GenerAdo por BackendDevAgent · hace 2 h</span>\n\n' +
              '<span class="warn">pendiente</span>  <span class="hi">TKT-002</span>  Implementar búsqueda full-text en catálogo\n' +
              '           <span class="dim">Generado por BackendDevAgent · hace 2 h</span>\n\n' +
              '<span class="ok">cerrado  </span>  <span class="hi">TKT-003</span>  Migración inicial de schema PostgreSQL\n' +
              '           <span class="dim">Generado por DiscoveryAgent · ayer</span>',
      },
      callout: '<strong>Flujo recomendado:</strong> deja que el run complete → revisa los tickets generados → elige el más prioritario → lánzalo como <code>request</code> del siguiente Run. Loop continuo.',
      calloutType: '',
      list: [
        'Los tickets tienen prioridad, workspace, run de origen y agente que los creó',
        'Puedes crear tickets manualmente también — no todo tiene que venir de un agente',
        'Un ticket puede convertirse en el input del siguiente Run directamente desde la UI',
      ],
    },
    {
      tag: '07 — Workspaces',
      title: 'Aislar proyectos, clientes o entornos',
      body: 'Un <strong>workspace</strong> es un espacio de trabajo con presupuesto, permisos y reviewer API keys propias. Todo — runs, tickets, reviews, pipelines — vive dentro de un workspace.' +
            '<br><br>La separación es total: los datos de un workspace no son visibles desde otro. Puedes tener un workspace por cliente, proyecto o entorno (dev/staging/prod), cada uno con su propio límite de gasto.',
      example: {
        label: 'Workspaces configurados',
        html: '<span class="dim">nombre              slug             budget      runs  HITL  LLM default</span>\n' +
              '<span class="dim">────────────────────────────────────────────────────────────────────</span>\n' +
              '<span class="hi">Font Jardineria</span>  font-jardineria  $50/mes     23    sí    claude-sonnet\n' +
              '<span class="hi">Proyecto Interno</span> interno          sin límite  8     no    deepseek-chat\n' +
              '<span class="hi">Cliente Acme</span>     acme             $200/mes    0     sí    gpt-4o\n\n' +
              '<span class="dim">Reviewer API keys — workspace: font-jardineria</span>\n' +
              '<span class="ok">sk-rev-a1b2…</span>  <span class="dim">Juan · read-only · expira 2026-12-31</span>\n' +
              '<span class="ok">sk-rev-c3d4…</span>  <span class="dim">María · read-only · sin expiración</span>',
      },
      callout: '<strong>Budget:</strong> cuando un workspace supera su presupuesto mensual en tokens, los nuevos runs se bloquean automáticamente. Configúralo desde <em>Settings → Workspaces</em>.',
      calloutType: '',
      list: [
        'Workspaces con HITL=no ejecutan sin pausas — útil para pipelines automatizados de confianza',
        'La API key de reviewer da acceso <em>solo</em> a ese workspace, solo lectura y reviews',
        'Cada workspace puede tener un <strong>modelo LLM por defecto</strong> distinto al global',
      ],
    },
    {
      tag: '08 — Evals + Compare',
      title: 'Medir calidad y comparar modelos o versiones',
      body: '<strong>Evals</strong> te permite definir métricas de calidad y ejecutarlas sobre los outputs de los agentes — útil para detectar regresiones cuando cambias el modelo o el prompt.' +
            '<br><br><strong>Compare</strong> muestra dos runs lado a lado. Ideal para decidir entre GPT-4o y DeepSeek, o entre el prompt actual y una variante que estás probando.',
      example: {
        label: 'Compare — claude-sonnet vs deepseek-chat',
        html: '<span class="dim">Task: implementar CRUD para /plants</span>\n' +
              '<span class="dim">──────────────────────────────────────────────────────────</span>\n' +
              '                    <span class="hi">claude-sonnet</span>      <span class="hi">deepseek-chat</span>\n' +
              'Duración            <span class="warn">38.2s</span>              <span class="ok">12.1s</span>\n' +
              'Archivos            <span class="ok">5</span>                  <span class="ok">4</span>\n' +
              'Tests               <span class="ok">12</span>                 <span class="warn">6</span>\n' +
              'Cobertura           <span class="ok">94%</span>                <span class="warn">71%</span>\n' +
              'Errores lint        <span class="ok">0</span>                  <span class="red">3</span>\n' +
              'Coste tokens        <span class="warn">$0.08</span>             <span class="ok">$0.01</span>\n' +
              '<span class="dim">──────────────────────────────────────────────────────────</span>\n' +
              'Eval score          <span class="ok">8.4 / 10</span>           <span class="warn">6.1 / 10</span>',
      },
      callout: '<strong>Cuándo usarlo:</strong> cuando cambias el modelo de un pipeline, lanza el mismo request con ambos modelos y compara antes de migrar. Evita regresiones silenciosas.',
      calloutType: '',
      list: [
        'Las métricas de Evals las defines tú — pueden ser automáticas (lint, cobertura) o con LLM-judge',
        'Compare funciona con cualquier dos runs del mismo workspace',
        'Los resultados de Evals se guardan en DB — puedes ver la evolución en el tiempo',
      ],
    },
    {
      tag: '09 — LLM / BYOK',
      title: 'Qué modelo usa cada pipeline y cómo configurarlo',
      body: 'En <strong>Settings → LLM</strong> configuras el modelo por defecto para cada workspace y añades tus propias API keys (<strong>Bring Your Own Key</strong>).' +
            '<br><br>antcrew soporta cualquier proveedor OpenAI-compatible. Las keys se encriptan en base de datos y nunca aparecen en logs. Cada request pasa por el proxy que añade la key correcta según el prefijo del modelo.',
      example: {
        label: 'Proveedores soportados — prefijos de modelo',
        html: '<span class="hi">claude</span>         Anthropic Claude (Sonnet, Opus, Haiku)\n' +
              '<span class="hi">gpt</span> / <span class="hi">o1</span> / <span class="hi">o3</span>   OpenAI GPT-4o, o3-mini…\n' +
              '<span class="hi">deepseek:</span>      DeepSeek Chat, Reasoner\n' +
              '<span class="hi">groq:</span>          Llama 3.3, Mixtral  <span class="dim">(muy rápido, barato)</span>\n' +
              '<span class="hi">gemini</span>         Google Gemini 1.5 / 2.0\n' +
              '<span class="hi">mistral:</span>       Mistral Large, Codestral\n' +
              '<span class="hi">xai:</span>           Grok-2\n' +
              '<span class="hi">ollama:</span>        Modelos locales (Ollama en localhost)\n' +
              '<span class="hi">simulated</span>      Sin LLM real — respuestas fijas para tests\n\n' +
              '<span class="dim"># Ejemplo de uso en config de pipeline</span>\n' +
              '<span class="dim">model: deepseek:deepseek-chat  ← usa tu DEEPSEEK_API_KEY</span>',
      },
      callout: '<strong>BYOK vs Managed:</strong> en BYOK usas tu propia key y pagas directamente al proveedor (margen 0). En modo managed el proxy central pone la key — práctico para equipos, algo más caro. BYOK es lo recomendado.',
      calloutType: '',
      list: [
        'El prefijo <code>groq:</code> es el más rápido para prototipar — responde en 1-2 segundos',
        '<code>simulated</code> ejecuta el pipeline completo sin gastar tokens — ideal para tests de integración',
        'Puedes asignar <strong>modelos distintos por agente</strong> dentro del mismo pipeline',
      ],
    },
    {
      tag: '10 — GitHub + Webhooks',
      title: 'Disparar pipelines automáticamente desde tu repositorio',
      body: 'La <strong>GitHub App</strong> conecta antcrew con tus repositorios. Una vez instalada, los agentes pueden crear PRs, comentar en issues y leer código del repo.' +
            '<br><br>Los <strong>Webhooks</strong> permiten que GitHub dispare un pipeline automáticamente cuando ocurre un evento — un push a main, apertura de un PR, o un comentario específico.',
      example: {
        label: 'Webhook configurado — trigger on push',
        html: '<span class="dim">Webhook #3 — font-jardineria / backend-repo</span>\n' +
              '<span class="dim">────────────────────────────────────────────</span>\n' +
              'Evento        <span class="hi">push</span> a rama <span class="ok">main</span>\n' +
              'Pipeline      <span class="hi">DevTeam</span>\n' +
              'Request auto  <span class="dim">"Revisar cambios del último commit"</span>\n' +
              'HITL          <span class="ok">activo</span>\n\n' +
              '<span class="dim">Últimas ejecuciones:</span>\n' +
              '<span class="ok">✓</span> hace 2 h   push abc123f  →  <span class="hi">run_a1b2c3d4</span>  <span class="ok">complete</span>\n' +
              '<span class="ok">✓</span> ayer       push def456g  →  <span class="hi">run_x9y8z7w6</span>  <span class="ok">complete</span>\n' +
              '<span class="red">✗</span> ayer       push ghi789h  →  <span class="hi">run_p5q4r3s2</span>  <span class="red">failed  </span>',
      },
      callout: '<strong>Seguridad:</strong> cada webhook se valida con HMAC-SHA256 usando el <code>GITHUB_WEBHOOK_SECRET</code>. Sin firma correcta, la petición se rechaza antes de ejecutar nada.',
      calloutType: '',
      list: [
        'La GitHub App usa tokens de instalación de corta duración — no hay credenciales de larga vida en disco',
        'Puedes tener múltiples webhooks por workspace, cada uno con pipeline y evento diferente',
        'Configura: <em>Settings → GitHub App</em> (instalar) y <em>Settings → Webhooks</em> (crear reglas)',
      ],
    },
    {
      tag: '11 — Schedules',
      title: 'Automatización periódica sin intervención manual',
      body: 'Los <strong>Schedules</strong> ejecutan un pipeline de forma recurrente siguiendo una expresión cron. Útil para revisiones de código diarias, reportes semanales, análisis de métricas, o cualquier tarea recurrente.' +
            '<br><br>Cada schedule tiene su propio request de entrada y puede tener HITL desactivado para ejecutarse de forma completamente autónoma.',
      example: {
        label: 'Schedules activos',
        html: '<span class="dim">nombre              cron          próximo run    pipeline</span>\n' +
              '<span class="dim">───────────────────────────────────────────────────────</span>\n' +
              '<span class="hi">Revisión diaria</span>   <span class="ok">0 9 * * 1-5</span>   mañana 09:00   DevTeam\n' +
              '<span class="dim">  "Revisa los commits de ayer y genera un resumen"</span>\n\n' +
              '<span class="hi">Reporte semanal</span>   <span class="ok">0 8 * * 1</span>     lun 08:00      ResearchTeam\n' +
              '<span class="dim">  "Analiza las métricas de la semana y propón mejoras"</span>\n\n' +
              '<span class="hi">Health check</span>      <span class="ok">*/30 * * * *</span>  en 14 min      MonitorTeam\n' +
              '<span class="dim">  "Verifica que los endpoints principales responden"</span>',
      },
      callout: '<strong>Recomendación de inicio:</strong> empieza sin schedules hasta que tengas un pipeline que funcione bien en manual. El schedule diario de revisión es el más inmediatamente útil.',
      calloutType: 'green',
      list: [
        'El cron se evalúa en UTC — recuerda el offset de tu zona horaria',
        'Si un schedule falla, el siguiente se sigue ejecutando — no hay bloqueo en cascada',
        'Puedes pausar un schedule individualmente sin eliminarlo — útil durante un freeze de features',
      ],
    },
  ];

  // ── CSS ────────────────────────────────────────────────────────────────────
  function injectCSS() {
    if (document.getElementById('ac-tut-css')) return;
    var s = document.createElement('style');
    s.id = 'ac-tut-css';
    s.textContent =
      '#ac-tut{position:fixed;inset:0;z-index:500;background:rgba(0,0,0,.75);' +
        'display:flex;align-items:center;justify-content:center;' +
        'backdrop-filter:blur(4px);animation:acTutFadeIn .18s ease;}' +
      '@keyframes acTutFadeIn{from{opacity:0}to{opacity:1}}' +
      '#ac-tut-panel{width:900px;max-width:calc(100vw - 32px);height:640px;max-height:calc(100vh - 48px);' +
        'background:#030712;border:1px solid #1f2937;border-radius:12px;' +
        'display:grid;grid-template-columns:190px 1fr;grid-template-rows:48px 1fr 52px;' +
        'overflow:hidden;box-shadow:0 24px 64px rgba(0,0,0,.6);}' +
      '#ac-tut-top{grid-column:1/-1;display:flex;align-items:center;gap:10px;padding:0 16px 0 20px;' +
        'border-bottom:1px solid #1f2937;background:#0d1117;}' +
      '#ac-tut-top .logo{font-family:monospace;font-weight:700;color:#818cf8;font-size:.8rem;}' +
      '#ac-tut-top .sep{color:#374151;}' +
      '#ac-tut-top .lbl{font-size:.72rem;color:#6b7280;}' +
      '#ac-tut-top .ctr{margin-left:auto;font-family:monospace;font-size:.72rem;color:#6b7280;}' +
      '#ac-tut-close{margin-left:12px;width:28px;height:28px;border:none;background:none;cursor:pointer;' +
        'color:#6b7280;font-size:18px;display:flex;align-items:center;justify-content:center;' +
        'border-radius:5px;transition:color .1s,background .1s;padding:0;flex-shrink:0;}' +
      '#ac-tut-close:hover{color:#f3f4f6;background:#1f2937;}' +
      '#ac-tut-progress{position:absolute;top:47px;left:0;height:2px;background:#818cf8;transition:width .3s ease;pointer-events:none;}' +
      '#ac-tut-nav{border-right:1px solid #1f2937;overflow-y:auto;padding:6px 0;' +
        'scrollbar-width:thin;scrollbar-color:#1f2937 transparent;}' +
      '.ac-tut-ni{display:flex;align-items:flex-start;gap:8px;padding:6px 12px;cursor:pointer;' +
        'border-left:2px solid transparent;transition:background .1s;}' +
      '.ac-tut-ni:hover{background:rgba(31,41,55,.4);}' +
      '.ac-tut-ni.on{background:rgba(129,140,248,.08);border-left-color:#818cf8;}' +
      '.ac-tut-ni .nn{font-family:monospace;font-size:.62rem;color:#6b7280;min-width:14px;margin-top:2px;}' +
      '.ac-tut-ni.on .nn{color:#818cf8;}' +
      '.ac-tut-ni .nt{font-size:.72rem;color:#9ca3af;line-height:1.35;}' +
      '.ac-tut-ni.on .nt{color:#f3f4f6;}' +
      '#ac-tut-main{overflow-y:auto;padding:28px 32px;scrollbar-width:thin;scrollbar-color:#1f2937 transparent;}' +
      '.tut-tag{font-family:monospace;font-size:.62rem;text-transform:uppercase;letter-spacing:.1em;color:#818cf8;margin-bottom:8px;}' +
      '.tut-title{font-size:1.25rem;font-weight:700;color:#f3f4f6;line-height:1.25;margin-bottom:12px;}' +
      '.tut-body{color:#9ca3af;font-size:.82rem;line-height:1.75;max-width:600px;margin-bottom:18px;}' +
      '.tut-body strong{color:#f3f4f6;font-weight:600;}' +
      '.tut-body code,.tut-body em{font-family:monospace;font-size:.78rem;background:#111827;border:1px solid #1f2937;padding:1px 5px;border-radius:3px;color:#818cf8;font-style:normal;}' +
      '.tut-flow{display:flex;align-items:center;flex-wrap:wrap;gap:0;max-width:600px;margin-bottom:18px;}' +
      '.tut-fs{background:#111827;border:1px solid #1f2937;border-radius:5px;padding:6px 10px;font-size:.72rem;color:#f3f4f6;white-space:nowrap;}' +
      '.tut-fa{color:#4b5563;padding:0 5px;font-size:.875rem;}' +
      '.tut-ex{background:#0d1117;border:1px solid #1f2937;border-radius:7px;overflow:hidden;max-width:600px;margin-bottom:18px;}' +
      '.tut-ex-bar{display:flex;align-items:center;gap:5px;padding:6px 10px;border-bottom:1px solid #1f2937;background:#111827;}' +
      '.tut-d{width:7px;height:7px;border-radius:50%;}' +
      '.tut-dr{background:#ef4444;}.tut-dy{background:#f59e0b;}.tut-dg{background:#10b981;}' +
      '.tut-ex-lbl{font-family:monospace;font-size:.62rem;color:#6b7280;margin-left:4px;}' +
      '.tut-ex-body{padding:12px 14px;font-family:monospace;font-size:.72rem;line-height:1.7;color:#9ca3af;white-space:pre;overflow-x:auto;}' +
      '.tut-ex-body .hi{color:#818cf8;}.tut-ex-body .ok{color:#10b981;}' +
      '.tut-ex-body .dim{color:#4b5563;}.tut-ex-body .warn{color:#f59e0b;}.tut-ex-body .red{color:#f87171;}' +
      '.tut-co{border-left:3px solid #818cf8;background:rgba(129,140,248,.08);padding:10px 14px;' +
        'border-radius:0 5px 5px 0;max-width:600px;margin-bottom:18px;font-size:.78rem;color:#9ca3af;line-height:1.65;}' +
      '.tut-co strong{color:#818cf8;}' +
      '.tut-co.green{border-left-color:#10b981;background:rgba(16,185,129,.07);}' +
      '.tut-co.green strong{color:#10b981;}' +
      '.tut-co em{font-style:italic;}' +
      '.tut-list{list-style:none;display:flex;flex-direction:column;gap:7px;max-width:600px;}' +
      '.tut-list li{display:flex;gap:8px;font-size:.78rem;color:#9ca3af;align-items:flex-start;line-height:1.55;}' +
      '.tut-list li::before{content:"›";color:#818cf8;font-weight:700;flex-shrink:0;}' +
      '.tut-list li strong{color:#f3f4f6;}' +
      '.tut-list li code,.tut-list li em{font-family:monospace;font-size:.75rem;background:#111827;border:1px solid #1f2937;padding:1px 4px;border-radius:3px;color:#818cf8;font-style:normal;}' +
      '#ac-tut-bot{grid-column:1/-1;display:flex;align-items:center;justify-content:space-between;' +
        'padding:0 16px;border-top:1px solid #1f2937;background:#0d1117;}' +
      '.tut-hint{font-size:.68rem;color:#4b5563;}' +
      '.tut-hint kbd{display:inline-block;padding:1px 4px;border:1px solid #374151;border-radius:3px;' +
        'font-size:.62rem;font-family:monospace;background:#111827;color:#6b7280;}' +
      '.tut-btns{display:flex;gap:6px;}' +
      '.tut-btn{padding:5px 14px;border-radius:5px;border:1px solid #1f2937;background:#111827;' +
        'color:#9ca3af;font-size:.78rem;cursor:pointer;transition:border-color .15s,color .15s;}' +
      '.tut-btn:hover{border-color:#374151;color:#f3f4f6;}' +
      '.tut-btn.pri{background:#4f46e5;border-color:#4f46e5;color:#fff;}' +
      '.tut-btn.pri:hover{background:#4338ca;border-color:#4338ca;}' +
      '.tut-btn:disabled{opacity:.3;cursor:default;}' +
      '@media(max-width:640px){' +
        '#ac-tut-panel{grid-template-columns:1fr;grid-template-rows:48px 36px 1fr 52px;}' +
        '#ac-tut-nav{grid-row:2;border-right:none;border-bottom:1px solid #1f2937;padding:0;' +
          'display:flex;overflow-x:auto;overflow-y:hidden;scrollbar-width:none;}' +
        '.ac-tut-ni{flex-direction:row;border-left:none;border-bottom:2px solid transparent;padding:6px 10px;flex-shrink:0;}' +
        '.ac-tut-ni.on{border-bottom-color:#818cf8;border-left:none;background:none;}' +
        '.ac-tut-ni .nn{display:none;}' +
        '.ac-tut-ni .nt{font-size:.68rem;}' +
        '#ac-tut-main{padding:16px;}' +
        '.tut-hint{display:none;}' +
      '}';
    document.head.appendChild(s);
  }

  // ── Build modal DOM ────────────────────────────────────────────────────────
  var modal = null;
  var navEl, mainEl, progEl, ctrEl;
  var cur = 0;

  function build() {
    injectCSS();
    modal = document.createElement('div');
    modal.id = 'ac-tut';
    modal.style.display = 'none';

    var panel = document.createElement('div');
    panel.id = 'ac-tut-panel';
    panel.style.position = 'relative';

    // Progress bar
    progEl = document.createElement('div');
    progEl.id = 'ac-tut-progress';
    panel.appendChild(progEl);

    // Top bar
    var top = document.createElement('div');
    top.id = 'ac-tut-top';
    top.innerHTML =
      '<span class="logo">antcrew</span>' +
      '<span class="sep">/</span>' +
      '<span class="lbl">tutorial</span>' +
      '<span class="ctr" id="ac-tut-ctr">1 / ' + SLIDES.length + '</span>' +
      '<button id="ac-tut-close" title="Cerrar (Esc)">×</button>';
    panel.appendChild(top);

    // Side nav
    navEl = document.createElement('div');
    navEl.id = 'ac-tut-nav';
    SLIDES.forEach(function (sl, i) {
      var item = document.createElement('div');
      item.className = 'ac-tut-ni';
      item.dataset.i = i;
      item.innerHTML =
        '<span class="nn">' + String(i + 1).padStart(2, '0') + '</span>' +
        '<span class="nt">' + sl.title + '</span>';
      item.addEventListener('click', function () { jump(parseInt(this.dataset.i)); });
      navEl.appendChild(item);
    });
    panel.appendChild(navEl);

    // Main content
    mainEl = document.createElement('div');
    mainEl.id = 'ac-tut-main';
    panel.appendChild(mainEl);

    // Bottom nav
    var bot = document.createElement('div');
    bot.id = 'ac-tut-bot';
    bot.innerHTML =
      '<span class="tut-hint"><kbd>←</kbd><kbd>→</kbd> para navegar · <kbd>Esc</kbd> para cerrar</span>' +
      '<div class="tut-btns">' +
        '<button class="tut-btn" id="ac-tut-prev" onclick="window._acTutGo(-1)">← Anterior</button>' +
        '<button class="tut-btn pri" id="ac-tut-next" onclick="window._acTutGo(1)">Siguiente →</button>' +
      '</div>';
    panel.appendChild(bot);

    modal.appendChild(panel);
    document.body.appendChild(modal);
    ctrEl = document.getElementById('ac-tut-ctr');

    // Close on backdrop click
    modal.addEventListener('click', function (e) {
      if (e.target === modal) close();
    });
    document.getElementById('ac-tut-close').addEventListener('click', close);

    // Keyboard — only when modal is visible
    document.addEventListener('keydown', function (e) {
      if (!modal || modal.style.display === 'none') return;
      if (e.key === 'Escape') { close(); return; }
      if (e.key === 'ArrowRight' || e.key === 'ArrowDown') { e.preventDefault(); go(1); }
      if (e.key === 'ArrowLeft'  || e.key === 'ArrowUp')   { e.preventDefault(); go(-1); }
    });

    // Public go helper (used by inline onclick)
    window._acTutGo = go;
  }

  // ── Render slide ───────────────────────────────────────────────────────────
  function renderSlide(i) {
    cur = i;
    var sl = SLIDES[i];

    // Nav highlight
    var items = navEl.querySelectorAll('.ac-tut-ni');
    items.forEach(function (el, idx) { el.classList.toggle('on', idx === i); });
    var activeItem = navEl.querySelector('.ac-tut-ni.on');
    if (activeItem) activeItem.scrollIntoView({ block: 'nearest' });

    // Counter + progress
    if (ctrEl) ctrEl.textContent = (i + 1) + ' / ' + SLIDES.length;
    if (progEl) progEl.style.width = ((i + 1) / SLIDES.length * 100).toFixed(1) + '%';

    // Buttons
    var prevBtn = document.getElementById('ac-tut-prev');
    var nextBtn = document.getElementById('ac-tut-next');
    if (prevBtn) prevBtn.disabled = i === 0;
    if (nextBtn) {
      nextBtn.disabled = i === SLIDES.length - 1;
      nextBtn.textContent = i === SLIDES.length - 1 ? '✓ Completado' : 'Siguiente →';
    }

    // Build slide HTML
    var html = '';
    html += '<p class="tut-tag">' + sl.tag + '</p>';
    html += '<h2 class="tut-title">' + sl.title + '</h2>';
    html += '<div class="tut-body">' + sl.body + '</div>';

    if (sl.flow) {
      html += '<div class="tut-flow">';
      sl.flow.forEach(function (step, fi) {
        if (fi > 0) html += '<span class="tut-fa">→</span>';
        html += '<span class="tut-fs">' + step + '</span>';
      });
      html += '</div>';
    }

    if (sl.example) {
      html += '<div class="tut-ex">' +
        '<div class="tut-ex-bar">' +
          '<span class="tut-d tut-dr"></span>' +
          '<span class="tut-d tut-dy"></span>' +
          '<span class="tut-d tut-dg"></span>' +
          '<span class="tut-ex-lbl">' + sl.example.label + '</span>' +
        '</div>' +
        '<div class="tut-ex-body">' + sl.example.html + '</div>' +
      '</div>';
    }

    if (sl.callout) {
      html += '<div class="tut-co' + (sl.calloutType ? ' ' + sl.calloutType : '') + '">' + sl.callout + '</div>';
    }

    if (sl.list && sl.list.length) {
      html += '<ul class="tut-list">';
      sl.list.forEach(function (item) {
        html += '<li>' + item + '</li>';
      });
      html += '</ul>';
    }

    mainEl.innerHTML = html;
    mainEl.scrollTop = 0;
  }

  // ── Navigation ─────────────────────────────────────────────────────────────
  function go(d) {
    var next = cur + d;
    if (next >= 0 && next < SLIDES.length) jump(next);
  }

  function jump(i) { renderSlide(i); }

  // ── Public API ─────────────────────────────────────────────────────────────
  function open() {
    if (!modal) build();
    modal.style.display = 'flex';
    document.body.style.overflow = 'hidden';
    renderSlide(cur);
  }

  function close() {
    if (modal) modal.style.display = 'none';
    document.body.style.overflow = '';
  }

  window.acTutorial = { open: open, close: close };

})();
