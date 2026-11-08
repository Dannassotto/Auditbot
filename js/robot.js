// Robot 3D de AudiBot dentro de la interfaz de chat.
// No toca app.js: deduce su estado mirando lo que ya pasa en la página
// (dictado activo, "procesando…" visible, tarjetas de alerta en el chat).
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const MODELOS = ['assets/audibot.glb', 'robot/audibot.glb'];   // busca el modelo en cualquiera de las dos carpetas
const UMBRAL_GRAVE_MXN = 500;      // alerta "grave" si alguna pérdida llega a este monto
const SEGUNDOS_ALERTA = 8;         // cuánto dura el estado de alerta antes de volver a reposo

const ESTADOS = {
  reposo:     { label: 'En reposo',      color: 0x4da3ff },
  escuchando: { label: 'Escuchando',     color: 0x3ddc84 },
  recibida:   { label: 'Orden recibida', color: 0x3ddc84 },
  trabajando: { label: 'Investigando',   color: 0xb36bff },
  grave:      { label: 'Alerta grave',   color: 0xff3b30 },
  menor:      { label: 'Aviso',          color: 0xff9a1f },
  fallo:      { label: 'Error, reintentable', color: 0xffd400 },
  bloqueado:  { label: 'Detenido',       color: 0xff3b30 },
};

const $ = (id) => document.getElementById(id);
const norm = (s) => s.replace(/_/g, ' ').toLowerCase();   // three.js cambia espacios por "_"

const ERRORES = {
  LIMITE_VELOCIDAD: { estado: 'fallo',     voz: 'Hay mucho tráfico. Puedes reintentar en unos segundos.' },
  TIMEOUT:          { estado: 'fallo',     voz: 'El modelo tardó demasiado. Puedes reintentar.' },
  ERROR_SERVIDOR:   { estado: 'fallo',     voz: 'El servicio tuvo un problema. Reintenta en un momento.' },
  SIN_CONEXION:     { estado: 'fallo',     voz: 'No hay conexión con el servicio. Revisa el servidor.' },
  LLAVE_INVALIDA:   { estado: 'bloqueado', voz: 'Mi llave no es válida. Revisa el archivo punto env.' },
  TOPE_GASTO:       { estado: 'bloqueado', voz: 'Llegué al tope de gasto. Me detengo.' },
  DESCONOCIDO:      { estado: 'fallo',     voz: 'Ocurrió un error inesperado.' },
};
let vozError = null;

function iniciar() {
  const stage = $('robotStage');
  if (!stage) return;

  // ---------- Voz (la genera el servidor con edge-tts; si falla, voz del navegador) ----------
  let silenciado = false;
  try { silenciado = localStorage.getItem('audibot-mudo') === '1'; } catch (_) {}
  const botonMudo = $('robotMute');
  const pintarMudo = () => {
    if (!botonMudo) return;
    botonMudo.textContent = silenciado ? '🔇' : '🔊';
    botonMudo.title = silenciado ? 'Activar voz' : 'Silenciar voz';
    botonMudo.setAttribute('aria-label', botonMudo.title);
  };

  const audios = new Map();
  let reproduciendo = null;
  async function audioDe(texto) {
    if (!audios.has(texto)) {
      const r = await fetch('/api/tts?texto=' + encodeURIComponent(texto));
      if (!r.ok) throw new Error('voz no disponible');
      audios.set(texto, URL.createObjectURL(await r.blob()));
    }
    return audios.get(texto);
  }
  
  function hablarLocal(texto) {
    if (!('speechSynthesis' in window)) return;
    const u = new SpeechSynthesisUtterance(texto);
    u.lang = 'es-ES';
    speechSynthesis.cancel();
    speechSynthesis.speak(u);
  }

  async function hablar(texto) {
    if (silenciado) return;
    try {
      const url = await audioDe(texto);
      if (reproduciendo) reproduciendo.pause();
      if ('speechSynthesis' in window) speechSynthesis.cancel();
      reproduciendo = new Audio(url);
      await reproduciendo.play();
    } catch (_) { hablarLocal(texto); }
  }
  if (botonMudo) {
    pintarMudo();
    botonMudo.addEventListener('click', () => {
      silenciado = !silenciado;
      try { localStorage.setItem('audibot-mudo', silenciado ? '1' : '0'); } catch (_) {}
      if (silenciado) {
        if (reproduciendo) reproduciendo.pause();
        if ('speechSynthesis' in window) speechSynthesis.cancel();
      }
      pintarMudo();
    });
  }
  if (!silenciado) ['Hola, ¿en qué puedo ayudarte?', 'Entendido, voy a revisarlo.'].forEach((f) => audioDe(f).catch(() => {}));

  // ---------- Escena ----------
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  stage.prepend(renderer.domElement);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(28, 1, 0.1, 100);
  camera.position.set(0, 1.4, 7.2);                 // lejos y centrado para que quepa el brazo
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.set(0, 1.2, 0);
  controls.enablePan = false;
  controls.enableZoom = false;
  controls.enableDamping = true;
  scene.add(new THREE.HemisphereLight(0xffffff, 0x334455, 1.1));
  const key = new THREE.DirectionalLight(0xffffff, 2.0); key.position.set(2, 4, 5); scene.add(key);
  const rim = new THREE.DirectionalLight(0x88aaff, 0.8); rim.position.set(-3, 2, -4); scene.add(rim);

  function ajustarTamano() {
    const w = stage.clientWidth || 240, h = stage.clientHeight || 220;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  new ResizeObserver(ajustarTamano).observe(stage);
  ajustarTamano();

  // ---------- Partes del robot ----------
  const bot = new THREE.Group(); scene.add(bot);
  let eyes = [], screens = [], chests = [], ants = [];
  let brazo = null, antebrazo = null;
  const PIEZAS_BRAZO = ['articulacion hombro', 'aro hombro naranja', 'protector superior', 'codo', 'antebrazo',
    'manguito marfil', 'aro naranja de muñeca', 'muñeca', 'palma', 'dedo 1', 'dedo 2', 'dedo 3', 'pulgar'];
  const piezasBrazo = [];

  const cargador = new GLTFLoader();
  function cargarModelo(i) {
  cargador.load(MODELOS[i], (gltf) => {
    const model = gltf.scene;
    const found = { eyes: [], screens: [], chests: [], ants: [] };
    model.traverse((o) => {
      const n = norm(o.name);
      if (o.isMesh) {
        if (n.startsWith('ojo')) found.eyes.push(o);
        else if (n.startsWith('pantalla')) found.screens.push(o);
        else if (n.startsWith('indicador de pecho')) found.chests.push(o);
      } else if (n.startsWith('antena')) found.ants.push(o);
      else if (PIEZAS_BRAZO.some((p) => n.startsWith(p))) piezasBrazo.push(o);
    });
    [...found.eyes, ...found.screens, ...found.chests].forEach((m) => { m.material = m.material.clone(); });
    found.ants.forEach((a) => {                      // las antenas giran desde su base
      const b = new THREE.Box3().setFromObject(a);
      const pivot = new THREE.Group();
      pivot.position.set((b.min.x + b.max.x) / 2, b.min.y, (b.min.z + b.max.z) / 2);
      model.add(pivot); pivot.updateMatrixWorld(true); pivot.attach(a);
      ants.push(pivot);
    });
    // Agrupa un brazo (el del lado derecho de la pantalla) en un pivote en el hombro
    const k = PIEZAS_BRAZO.length;
    let mejor = null;
    [piezasBrazo.slice(0, k), piezasBrazo.slice(k, 2 * k)].filter((g) => g.length).forEach((g) => {
      const c = new THREE.Box3().setFromObject(g[0]).getCenter(new THREE.Vector3());
      if (!mejor || c.x > mejor.c.x) mejor = { g, c };
    });
    if (mejor) {
      const ANTEBRAZO = ['antebrazo', 'manguito', 'aro naranja de muñeca', 'muñeca', 'palma', 'dedo', 'pulgar'];
      const codo = mejor.g.find((p) => norm(p.name).startsWith('codo'));
      const pv = new THREE.Group();                  // pivote del hombro: sube todo el brazo
      pv.position.copy(mejor.c);
      const fp = new THREE.Group();                  // pivote del codo: agita antebrazo y mano
      if (codo) fp.position.copy(new THREE.Box3().setFromObject(codo).getCenter(new THREE.Vector3())).sub(mejor.c);
      model.add(pv); pv.add(fp); pv.updateMatrixWorld(true);
      mejor.g.forEach((p) => (ANTEBRAZO.some((a) => norm(p.name).startsWith(a)) ? fp : pv).attach(p));
      brazo = pv; antebrazo = fp;
    }
    eyes = found.eyes; screens = found.screens; chests = found.chests;
    const box = new THREE.Box3().setFromObject(model);
    const s = 2.2 / box.getSize(new THREE.Vector3()).y;
    model.scale.setScalar(s);
    box.setFromObject(model);
    const c = box.getCenter(new THREE.Vector3());
    model.position.set(-c.x, -box.min.y, -c.z);
    bot.add(model);
  }, undefined, (err) => {
    if (i + 1 < MODELOS.length) return cargarModelo(i + 1);     // no estaba aquí: prueba la otra carpeta
    console.error('No se pudo cargar el modelo del robot. Busqué en: ' + MODELOS.join(', '), err);
    $('robotLabel').textContent = 'Robot no disponible';
  });
  }
  cargarModelo(0);

  // ---------- Máquina de estados ----------
  let st = 'reposo', t0 = 0, timer = null, variante = 0;
  const clock = new THREE.Clock();
  const etiqueta = $('robotLabel'), punto = $('robotDot');

  function setState(s) {
    clearTimeout(timer);
    const anterior = st;
    st = s;
    t0 = clock.elapsedTime;
    if (s === 'escuchando') {
      variante = Math.floor(Math.random() * 3);
      if (anterior !== 'escuchando') hablar('Hola, ¿en qué puedo ayudarte?');
    }
    if (s === 'recibida' && anterior !== 'recibida') hablar('Entendido, voy a revisarlo.');
    const e = ESTADOS[s];
    if ((s === 'fallo' || s === 'bloqueado') && vozError) { hablar(vozError); vozError = null; }
    etiqueta.textContent = e.label;
    punto.style.background = '#' + e.color.toString(16).padStart(6, '0');
    stage.dataset.estado = s;
    if (s === 'recibida') timer = setTimeout(() => setState('trabajando'), 1400);
    if (s === 'grave' || s === 'menor' || s === 'fallo') timer = setTimeout(() => setState('reposo'), SEGUNDOS_ALERTA * 1000);
  }

  function glow(meshes, hex, intensity, base) {    // base: color fijo de la pieza; si no hay, toma el del estado
    meshes.forEach((m) => {
      m.material.color.setHex(base === undefined ? hex : base);
      m.material.emissive.setHex(hex);
      m.material.emissiveIntensity = intensity;
    });
  }

  // ---------- Animación ----------
  function frame() {
    requestAnimationFrame(frame);
    const t = clock.elapsedTime, tt = t - t0;
    let eyeI = 1, chestI = 0.3, eyeVisible = true, shake = 0, wiggle = 0.05, bounce = 0, yaw = 0, armTarget = 0, foreTarget = 0;
    switch (st) {
      case 'escuchando':
        chestI = 0.3 + 0.9 * (0.5 + 0.5 * Math.sin(tt * 6)); wiggle = 0.18;
        if (variante !== 0 && tt < 1.4) {
          yaw = Math.sin(tt * 9) * 0.35 * (1 - tt / 1.4);
          bounce = Math.abs(Math.sin(tt * 7)) * 0.12 * (1 - tt / 1.4);
        }
        if (variante !== 2) {
          armTarget = tt < 2.6 ? 1.9 : 0;
          foreTarget = tt > 0.5 && tt < 2.3 ? Math.sin((tt - 0.5) * 9) * 0.5 * (1 - (tt - 0.5) / 2.2) : 0;
          shake = tt < 2.6 ? Math.sin(tt * 3) * 0.03 : 0;
        } else wiggle = 0.3;
        break;
      case 'recibida':
        eyeVisible = Math.floor(tt * 8) % 2 === 0 || tt > 1.2; chestI = 1.2; wiggle = 0.12; break;
      case 'trabajando':
        eyeI = 0.35 + 0.65 * (0.5 + 0.5 * Math.sin(tt * 2.4));
        chestI = 0.2 + 0.6 * (0.5 + 0.5 * Math.sin(tt * 2.4)); break;
      case 'grave': case 'menor': case 'fallo': {
        const on = Math.sin(tt * 14) > -0.2;
        eyeI = on ? 1.6 : 0.25; chestI = on ? 1.6 : 0.2;
        shake = Math.sin(tt * 26) * 0.05; wiggle = 0.25; bounce = 0.04 * Math.abs(Math.sin(tt * 8));
        break;
      }
      case 'bloqueado':
        eyeI = 0.6 + 0.4 * Math.sin(tt * 3); chestI = 0.8; wiggle = 0.02; break;
      default: chestI = 0.35;
    }
    const hex = ESTADOS[st].color;
    glow(eyes, hex, eyeI);
    glow(chests, hex, chestI, 0x0b100d);
    glow(screens, hex, 0.02 * eyeI, 0x0a0f16);
    eyes.forEach((m) => { m.visible = eyeVisible; });
    bot.position.y = Math.sin(t * 1.6) * 0.05 + bounce;
    bot.rotation.y = yaw;
    bot.rotation.z = shake;
    ants.forEach((a, i) => { a.rotation.z = Math.sin(t * 2.2 + i * 1.7) * wiggle; });
    if (brazo) brazo.rotation.z += (armTarget - brazo.rotation.z) * 0.15;
    if (antebrazo) antebrazo.rotation.z += (foreTarget - antebrazo.rotation.z) * 0.25;
    controls.update();
    renderer.render(scene, camera);
  }
  window.AudiBotRobot = { setState, estado: () => st, hablar };   // lo usa la ventana flotante (overlay.js)
  setState('reposo');
  frame();

  // ---------- Estado del robot a partir de lo que ocurre en el chat ----------
  const escribiendo = $('typingRow');       // "AudiBot está procesando tu solicitud…"
  const lista = $('messageList');
  const botonVoz = $('voiceBtn');
  const ocupado = () => escribiendo && !escribiendo.classList.contains('hidden');
  let mensajesAlInicio = 0;

  function resultadoDeLaConsulta() {
    const ultimo = lista.lastElementChild;
    if (!ultimo || lista.childElementCount <= mensajesAlInicio) return 'reposo';   // no llegó nada nuevo
    if (ultimo.classList.contains('error')) {
      const e = ERRORES[ultimo.dataset.codigo] || ERRORES.DESCONOCIDO;
      vozError = e.voz;
      return e.estado;
    }
    const alertas = [...ultimo.querySelectorAll('.audit-card.alert')];
    if (!alertas.length) return 'reposo';
    const perdidas = alertas.map((a) => {
      const m = a.textContent.match(/Pérdida estimada:\s*\$([\d.,]+)/);
      return m ? parseFloat(m[1].replace(/,/g, '')) : 0;
    });
    return perdidas.some((p) => p >= UMBRAL_GRAVE_MXN) ? 'grave' : 'menor';
  }

  if (escribiendo) {
    new MutationObserver(() => {
      if (ocupado()) {
        if (st !== 'recibida' && st !== 'trabajando') {
          mensajesAlInicio = lista.childElementCount;
          setState('recibida');
        }
      } else if (st === 'recibida' || st === 'trabajando') {
        setState(resultadoDeLaConsulta());
      }
    }).observe(escribiendo, { attributes: true, attributeFilter: ['class'] });
  }

  // Eventos de la voz del servidor (listener.py): "AudiBot" detectado / se acabó el tiempo sin orden
  window.addEventListener('audibot:voz', (e) => {
    const ev = e.detail && e.detail.ev;
    if (ev === 'wake' && st !== 'recibida' && st !== 'trabajando') setState('escuchando');
    else if (ev === 'timeout' && st === 'escuchando') setState('reposo');
  });

  if (botonVoz) {
    new MutationObserver(() => {
      if (botonVoz.classList.contains('listening')) {
        if (st !== 'escuchando') setState('escuchando');
      } else if (st === 'escuchando') {
        setState(ocupado() ? 'trabajando' : 'reposo');
      }
    }).observe(botonVoz, { attributes: true, attributeFilter: ['class'] });
  }
}

iniciar();