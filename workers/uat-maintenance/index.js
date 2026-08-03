// Cloudflare Worker: serve a maintenance page when the UAT Hetzner server is down.
// Passes all requests through when the server is up; returns 503 + maintenance HTML
// for browser requests when the origin is unreachable or returns 5xx.

const MAINTENANCE_HTML = `<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>antcrew \xB7 en proceso</title>
<style>
:root{--bg:#07070f;--accent:#818CF8;--accent-dim:rgba(129,140,248,.09);--hi:#e4e4f0;--lo:#45455e;--border:rgba(129,140,248,.14)}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;background:var(--bg);color:var(--hi);font-family:system-ui,-apple-system,sans-serif;overflow:hidden}
canvas{position:fixed;inset:0;pointer-events:none;z-index:0}
.grid{position:fixed;inset:0;pointer-events:none;z-index:1;background-image:radial-gradient(var(--border) 1px,transparent 1px);background-size:28px 28px;mask-image:radial-gradient(ellipse 80% 80% at 50% 50%,black 40%,transparent 100%)}
.wrap{position:relative;z-index:10;height:100%;display:flex;align-items:center;justify-content:center;padding:2rem}
.card{background:rgba(9,9,19,0.82);border:1px solid var(--border);border-radius:18px;padding:2.5rem 3rem;max-width:400px;width:100%;backdrop-filter:blur(20px);box-shadow:0 0 0 1px rgba(129,140,248,.04),0 32px 64px rgba(0,0,0,.55),inset 0 1px 0 rgba(129,140,248,.08);text-align:center}
.brand{display:flex;align-items:center;justify-content:center;gap:.5rem;margin-bottom:1.6rem}
.brand svg{width:26px;height:26px}
.brand-name{font-family:'SF Mono','Cascadia Code','Fira Code',monospace;font-size:.78rem;letter-spacing:.18em;color:var(--lo);text-transform:uppercase}
.badge{display:inline-flex;align-items:center;gap:.4em;background:var(--accent-dim);border:1px solid var(--border);color:var(--accent);font-size:.65rem;letter-spacing:.12em;text-transform:uppercase;font-family:'SF Mono','Cascadia Code',monospace;padding:.3em .85em;border-radius:99px;margin-bottom:1.8rem}
.badge::before{content:'';width:5px;height:5px;border-radius:50%;background:var(--accent);opacity:.6;animation:blink-dot 2s ease-in-out infinite}
@keyframes blink-dot{0%,100%{opacity:.3}50%{opacity:1}}
h1{font-family:'SF Mono','Cascadia Code','Fira Code',monospace;font-size:clamp(1.9rem,5vw,2.6rem);font-weight:600;color:var(--hi);letter-spacing:-.02em;margin-bottom:.8rem;line-height:1.1}
.cursor{display:inline-block;width:3px;height:.85em;background:var(--accent);vertical-align:text-bottom;border-radius:1px;margin-left:5px;animation:blink 1s step-end infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:0}}
p{color:var(--lo);font-size:.88rem;line-height:1.65;margin-bottom:1.8rem}
.dots{display:flex;justify-content:center;gap:7px}
.dot{width:5px;height:5px;border-radius:50%;background:var(--accent);animation:pulse 1.5s ease-in-out infinite}
.dot:nth-child(2){animation-delay:.2s}
.dot:nth-child(3){animation-delay:.4s}
@keyframes pulse{0%,100%{opacity:.15;transform:scale(.8)}50%{opacity:.75;transform:scale(1.15)}}
</style>
</head>
<body>
<div class="grid"></div>
<canvas id="c"></canvas>
<div class="wrap">
  <div class="card">
    <div class="brand">
      <svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
        <ellipse cx="50" cy="72" rx="14" ry="18" fill="#818CF8"/>
        <ellipse cx="50" cy="52" rx="8" ry="7.5" fill="#818CF8"/>
        <circle cx="50" cy="37" r="10" fill="#818CF8"/>
        <line x1="45" y1="28" x2="32" y2="15" stroke="#818CF8" stroke-width="1.5" stroke-linecap="round"/>
        <line x1="55" y1="28" x2="68" y2="15" stroke="#818CF8" stroke-width="1.5" stroke-linecap="round"/>
        <polyline points="42,47 28,44 22,37" fill="none" stroke="#818CF8" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
        <polyline points="42,53 26,53 20,47" fill="none" stroke="#818CF8" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
        <polyline points="42,59 28,62 22,71" fill="none" stroke="#818CF8" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
        <polyline points="58,47 72,44 78,37" fill="none" stroke="#818CF8" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
        <polyline points="58,53 74,53 80,47" fill="none" stroke="#818CF8" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
        <polyline points="58,59 72,62 78,71" fill="none" stroke="#818CF8" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      <span class="brand-name">antcrew</span>
    </div>
    <div class="badge">UAT &middot; entorno de pruebas</div>
    <h1>En proceso<span class="cursor"></span></h1>
    <p>El servidor de pruebas est&#225; temporalmente inactivo.<br>Vuelve en un momento.</p>
    <div class="dots"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div>
  </div>
</div>
<script>
const canvas=document.getElementById('c');
const ctx=canvas.getContext('2d');
const COLOR='#818CF8';
function normAngle(a){while(a>Math.PI)a-=2*Math.PI;while(a<-Math.PI)a+=2*Math.PI;return a}
function makeAnt(x,y,angle,scale,speed){return{x,y,angle,targetAngle:angle,scale,speed,phase:Math.random()*Math.PI*2,turnRate:1.4+Math.random()*1.2,turnTimer:1.5+Math.random()*3.5}}
let ants=[];
function initAnts(){const W=canvas.width,H=canvas.height;ants=[makeAnt(W*.75,H*.78,0,1.30,52),makeAnt(W*.20,H*.55,Math.PI*.05,.72,34),makeAnt(W*.88,H*.88,Math.PI,.52,64),makeAnt(W*.12,H*.32,-Math.PI*.1,.40,26),makeAnt(W*.55,H*.15,Math.PI*1.05,.30,43)]}
function resize(){canvas.width=window.innerWidth;canvas.height=window.innerHeight;ants.length||initAnts()}
window.addEventListener('resize',resize);resize();
function pickTarget(ant){const r=Math.random();if(r<.25)ant.targetAngle=normAngle(ant.angle+Math.PI);else if(r<.55)ant.targetAngle=normAngle(ant.angle+(Math.random()<.5?1:-1)*(Math.PI*.25+Math.random()*Math.PI*.25));else ant.targetAngle=normAngle(ant.angle+(Math.random()<.5?1:-1)*(Math.PI*.08+Math.random()*Math.PI*.1));ant.turnTimer=2.5+Math.random()*4.5}
function updateAnt(ant,dt){let diff=normAngle(ant.targetAngle-ant.angle);const ms=ant.turnRate*dt;ant.angle=Math.abs(diff)<=ms?ant.targetAngle:normAngle(ant.angle+Math.sign(diff)*ms);ant.x+=Math.cos(ant.angle)*ant.speed*dt;ant.y+=Math.sin(ant.angle)*ant.speed*dt;ant.phase+=dt*7;ant.turnTimer-=dt;if(ant.turnTimer<=0)pickTarget(ant);const W=canvas.width,H=canvas.height,m=80*ant.scale;if(ant.x<-m||ant.x>W+m||ant.y<-m||ant.y>H+m){ant.x=Math.max(-m,Math.min(W+m,ant.x));ant.y=Math.max(-m,Math.min(H+m,ant.y));ant.targetAngle=normAngle(Math.atan2(H/2-ant.y,W/2-ant.x)+(Math.random()-.5)*.6);ant.turnTimer=.5}}
function drawAnt(ant){ctx.save();ctx.translate(ant.x,ant.y);ctx.rotate(ant.angle);ctx.scale(ant.scale,ant.scale);ctx.shadowColor='rgba(129,140,248,.5)';ctx.shadowBlur=10;ctx.strokeStyle=COLOR;ctx.fillStyle=COLOR;ctx.lineCap='round';ctx.lineJoin='round';const pairs=[{bx:3,po:0},{bx:-7,po:Math.PI},{bx:-17,po:0}];ctx.lineWidth=2.2;pairs.forEach(({bx,po})=>{const sw=Math.sin(ant.phase+po)*.44;const ta=-Math.PI/2+sw;const tkx=bx+Math.cos(ta)*13,tky=Math.sin(ta)*13,tfx=tkx+Math.cos(ta+.55)*9,tfy=tky+Math.sin(ta+.55)*9;ctx.beginPath();ctx.moveTo(bx,-2);ctx.lineTo(tkx,tky);ctx.lineTo(tfx,tfy);ctx.stroke();const ba=Math.PI/2-sw;const bkx=bx+Math.cos(ba)*13,bky=Math.sin(ba)*13,bfx=bkx+Math.cos(ba-.55)*9,bfy=bky+Math.sin(ba-.55)*9;ctx.beginPath();ctx.moveTo(bx,2);ctx.lineTo(bkx,bky);ctx.lineTo(bfx,bfy);ctx.stroke()});ctx.beginPath();ctx.ellipse(-28,0,16,10,0,0,Math.PI*2);ctx.fill();ctx.beginPath();ctx.ellipse(-5,0,8,7,0,0,Math.PI*2);ctx.fill();ctx.beginPath();ctx.arc(14,0,9,0,Math.PI*2);ctx.fill();ctx.lineWidth=1.5;ctx.beginPath();ctx.moveTo(21,-5);ctx.lineTo(34,-17);ctx.stroke();ctx.beginPath();ctx.moveTo(21,-3);ctx.lineTo(37,-9);ctx.stroke();ctx.restore()}
let last=0;
function loop(ts){const dt=Math.min((ts-last)/1000,.05);last=ts;ctx.clearRect(0,0,canvas.width,canvas.height);ants.forEach(a=>{updateAnt(a,dt);drawAnt(a)});requestAnimationFrame(loop)}
requestAnimationFrame(loop);
<\/script>
</body>
</html>`;

function isBrowserRequest(request) {
  return (request.headers.get('Accept') || '').includes('text/html');
}

function maintenanceResponse() {
  return new Response(MAINTENANCE_HTML, {
    status: 503,
    headers: {
      'Content-Type': 'text/html;charset=utf-8',
      'Retry-After': '3600',
      'Cache-Control': 'no-store',
    },
  });
}

function maintenanceJson() {
  return new Response(JSON.stringify({ error: 'service_unavailable', env: 'uat' }), {
    status: 503,
    headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
  });
}

export default {
  async fetch(request) {
    try {
      const response = await fetch(request, { cf: { cacheTtl: 0 } });
      if (response.status < 500) return response;
    } catch (_) {
      // origin unreachable
    }
    return isBrowserRequest(request) ? maintenanceResponse() : maintenanceJson();
  },
};
