// Ventana flotante de AudiBot: saludo, confirmación, ejecución en segundo plano y resultado.
const { app, BrowserWindow, Tray, Menu, nativeImage, screen, shell } = require('electron');
const { spawn } = require('child_process');
const readline = require('readline');
const path = require('path');
const fs = require('fs');

// 127.0.0.1 y no "localhost": el servidor solo escucha en IPv4 y "localhost" puede resolver a IPv6.
const BASE = process.env.AUDIBOT_URL || 'http://127.0.0.1:5000';
const PYTHON = process.env.AUDIBOT_PYTHON ||
  (process.platform === 'win32' ? 'python' : 'python3');

const RAIZ = path.join(__dirname, '..');
// Modelo de voz: el pequeño por defecto (carga en segundos y alcanza para "AudiBot", la orden y "sí/no").
// El grande necesita varios GB de RAM libres y, si no cabe, el micrófono nunca arranca.
// Para probarlo: AUDIBOT_MODELO=model-grande
const MODELO = process.env.AUDIBOT_MODELO
  ? path.resolve(RAIZ, process.env.AUDIBOT_MODELO)
  : path.join(RAIZ, 'model');

// Dispositivo de micrófono (número de sounddevice). Ej.: AUDIBOT_DEVICE=1
const DISPOSITIVO = process.env.AUDIBOT_DEVICE || '';

// El micrófono se pausa mientras habla el robot y se reanuda en cuanto el audio TERMINA de verdad
// (se detecta en la ventana flotante). COLA_VOZ es la espera tras acabar, para no captar el eco final.
// Si el robot se oye a sí mismo al final de la frase, sube COLA_VOZ (por ejemplo a 0.6).
const COLA_VOZ = 0.35;
// Respaldo por si no se puede detectar el fin del audio: margen fijo + tiempo por carácter.
const MARGEN_VOZ = 1.0;
const SEG_POR_CARACTER = 0.06;

let win;
let tray;
let listener;
let servidor;
let saliendo = false;
let ocultarTimer = null;
let pendiente = null;
let ultimoFin = null;
let vozServidor = null;
let ultimoSondeoVoz = 0;
let caidasEscucha = [];
let lineasSinTipo = 0;
let vozSeq = 0;

const unica = app.requestSingleInstanceLock();
if (!unica) {
  app.quit();
}

function crearVentana() {
  const { width, height } = screen.getPrimaryDisplay().workAreaSize;
  const W = 400;
  const H = 640;

  win = new BrowserWindow({
    width: W,
    height: H,
    x: width - W,
    y: height - H,
    show: false,
    transparent: true,
    frame: false,
    alwaysOnTop: true,
    resizable: false,
    hasShadow: false,
    skipTaskbar: true,
    webPreferences: {
      backgroundThrottling: false,
      autoplayPolicy: 'no-user-gesture-required'
    }
  });

  win.setMenu(null);
  win.setAlwaysOnTop(true, 'screen-saver');
  if (process.platform === 'darwin') {
    win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  }

  win.loadURL(BASE + '/overlay.html');

  // Detecta cuándo empieza y termina el audio que reproduce la ventana (voz de Edge TTS).
  win.webContents.on('dom-ready', () => {
    win.webContents.executeJavaScript(HOOK_AUDIO).catch(() => {});
  });

  win.webContents.on('did-fail-load', () => {
    if (!saliendo) {
      setTimeout(() => {
        if (win && !win.isDestroyed()) {
          win.loadURL(BASE + '/overlay.html');
        }
      }, 2000);
    }
  });

  win.webContents.on('page-title-updated', (_e, titulo) => {
    if (titulo === 'AudiBot:cerrar') {
      ocultar();
    }
  });

  // AUDIBOT_DEBUG=1: abre la consola de la ventana flotante para ver los errores de la página.
  if (process.env.AUDIBOT_DEBUG) {
    win.webContents.openDevTools({ mode: 'detach' });
  }
}


function lanzarServidor() {
  // Si servidor.py ya está funcionando, no abrir otro navegador.
  fetch(BASE + '/api/estado')
    .then(() => {
      console.log('El servidor de AudiBot ya está disponible.');
    })
    .catch(() => {
      // Si no está funcionando, iniciarlo desde Electron.
      servidor = spawn(
        PYTHON,
        [path.join(RAIZ, 'servidor.py')],
        {
          cwd: RAIZ,
          stdio: 'inherit',
          env: {
            ...process.env,
            AUDIBOT_NO_BROWSER: '1',
            PORT: new URL(BASE).port || '5000'
          }
        }
      );

      servidor.on('error', (e) => {
        console.error('No pude lanzar servidor.py:', e.message);
      });

      servidor.on('exit', (code) => {
        if (!saliendo) {
          console.error('servidor.py terminó (' + code + ')');
        }
      });

      // Esperar a que el servidor esté listo, sin abrir
      // automáticamente una segunda ventana del navegador.
      const t = setInterval(async () => {
        try {
          await fetch(BASE + '/api/estado');
          clearInterval(t);
          console.log('Servidor de AudiBot listo.');
        } catch {
          // El servidor todavía está iniciando.
        }
      }, 1000);
    });
}


// Manda un evento a la ventana flotante y avisa en la terminal si la página no lo recibe.
function ui(ev) {
  if (!win || win.isDestroyed()) return;

  win.webContents
    .executeJavaScript(
      `typeof window.__ov === 'function' ? (window.__ov(${JSON.stringify(ev)}), 'ok') : 'SIN __ov'`
    )
    .then((r) => {
      if (r !== 'ok') {
        console.error('[ui] la ventana no tiene __ov todavía:', ev.tipo);
      }
    })
    .catch((e) => console.error('[ui] error:', e.message));
}

/**
 * ¿Funciona la voz de Edge TTS del servidor? Si la primera comprobación falla (servidor todavía
 * arrancando, un corte de internet) NO se queda en "no" para siempre: se vuelve a probar cada 30 s.
 */
async function vozDisponible() {
  if (vozServidor === true) return true;

  const ahora = Date.now();
  if (vozServidor === false && ahora - ultimoSondeoVoz < 30000) return false;

  ultimoSondeoVoz = ahora;
  try {
    const respuesta = await fetch(
      BASE + '/api/tts?texto=' + encodeURIComponent('hola')
    );

    vozServidor = respuesta.ok;

    if (!respuesta.ok) {
      console.error('Edge TTS no está disponible. HTTP:', respuesta.status);
    }
  } catch (e) {
    vozServidor = false;
    console.error('No se pudo conectar con el servicio Edge TTS:', e.message);
  }

  return vozServidor;
}

function pausaListener(segundos) {
  // Evita que el micrófono interprete la voz del robot como una orden.
  if (listener && listener.stdin && listener.stdin.writable) {
    listener.stdin.write(`pausa ${segundos}\n`);
  }
}

// Reanuda el micrófono tras "segundos" (sustituye a cualquier pausa en curso).
function reanudarListener(segundos) {
  if (listener && listener.stdin && listener.stdin.writable) {
    listener.stdin.write(`reanudar ${segundos}\n`);
  }
}

// Se inyecta en la ventana flotante: cuenta los audios que empiezan y los que terminan.
const HOOK_AUDIO = `(() => {
  if (window.__voz) return;
  window.__voz = { sonando: 0, inicios: 0 };
  const orig = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function () {
    const el = this;
    const fin = () => {
      if (el.__vozActivo) {
        el.__vozActivo = false;
        window.__voz.sonando = Math.max(0, window.__voz.sonando - 1);
      }
    };
    if (!el.__vozHook) {
      el.__vozHook = true;
      ['ended', 'pause', 'error', 'abort', 'emptied'].forEach((n) => el.addEventListener(n, fin));
    }
    if (!el.__vozActivo) {
      el.__vozActivo = true;
      window.__voz.sonando++;
      window.__voz.inicios++;
    }
    const p = orig.apply(this, arguments);
    if (p && p.catch) p.catch(fin);
    return p;
  };
})();`;

function leerVoz() {
  if (!win || win.isDestroyed()) return Promise.resolve(null);
  return win.webContents
    .executeJavaScript('window.__voz ? JSON.stringify(window.__voz) : "null"')
    .then((s) => JSON.parse(s))
    .catch(() => null);
}

// Espera a que el audio termine de verdad y entonces reanuda el micrófono.
async function esperarFinVoz(texto, seq, base) {
  const t0 = Date.now();
  const estimado = (MARGEN_VOZ + SEG_POR_CARACTER * texto.length) * 1000;
  let empezo = false;
  let motivo = '';

  while (!saliendo) {
    await new Promise((r) => setTimeout(r, 100));
    if (seq !== vozSeq) return;   // otra frase tomó el relevo

    const v = await leerVoz();
    const t = Date.now() - t0;

    if (v) {
      if (v.inicios > base) empezo = true;
      if (empezo && v.sonando <= 0) { motivo = 'audio terminó'; break; }
    }
    if (!empezo && t > estimado + 2000) { motivo = 'no se detectó el audio (respaldo)'; break; }
    if (t > 25000) { motivo = 'tiempo máximo'; break; }
  }

  if (seq !== vozSeq) return;
  console.log('[decir] ' + motivo + ' tras ' + (Date.now() - t0) + ' ms: te escucho');
  reanudarListener(COLA_VOZ);
}

/**
 * Usa exclusivamente Edge TTS a través del panel y del servidor.
 * No se utiliza System.Speech ni ninguna voz de Windows.
 *
 * La voz (por ejemplo es-VE-SebastianNeural) se configura en VOZ_TTS, dentro de servidor.py.
 *
 * Cada vez que el robot habla, también se pausa el micrófono el tiempo que dure el audio,
 * para que no se oiga a sí mismo.
 */
async function decir(texto) {
  console.log('[decir] intentando decir:', texto);

  if (!(await vozDisponible())) {
    console.error(
      '[decir] NO HABLO: /api/tts no responde. ' +
      'Comprueba servidor.py y el servicio Edge TTS (pip install edge-tts).'
    );
    return;
  }

  const seq = ++vozSeq;

  // Pausa larga de seguridad: se levanta en cuanto el audio termina de verdad.
  pausaListener(30);

  const antes = await leerVoz();
  const base = antes ? antes.inicios : 0;

  // El panel flotante se encarga de reproducir el audio de Edge TTS.
  ui({
    tipo: 'decir',
    texto
  });

  // Sin await: no bloquea el resto del flujo.
  esperarFinVoz(texto, seq, base);
}

function mostrar() {
  if (!win || win.isDestroyed()) return;

  win.setAlwaysOnTop(true, 'screen-saver');

  if (!win.isVisible()) {
    win.showInactive();
  }

  win.moveTop();
}

function ocultar() {
  clearTimeout(ocultarTimer);

  if (win && !win.isDestroyed() && win.isVisible()) {
    win.hide();
  }

  ui({ tipo: 'reiniciar' });
}

function ocultarEn(ms) {
  clearTimeout(ocultarTimer);
  ocultarTimer = setTimeout(ocultar, ms);
}

async function enviarOrden(texto) {
  try {
    const r = await fetch(BASE + '/api/orden', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        texto,
        async: true
      })
    });

    if (r.status === 409) {
      decir('Ya hay otra orden en curso.');
    } else if (!r.ok) {
      throw new Error('Error ' + r.status);
    }
  } catch (e) {
    mostrar();

    ui({
      tipo: 'fin',
      fin: {
        error:
          'No pude mandar la orden: ' +
          e.message +
          '. ¿Está corriendo servidor.py?'
      }
    });
  }
}

// Ojo con las frases del robot: su propio micrófono las oye. "¿Es correcto?" contaba como un "sí"
// (el robot se confirmaba solo) y "Dime la orden otra vez" como una orden nueva. Por eso se dicen de otra forma.
// La pausa del micrófono mientras habla la gestiona decir().
function alEvento(ev) {
  // En la terminal se ve qué está oyendo el micrófono (menos las transcripciones parciales).
  if (ev.ev !== 'parcial') {
    console.log('[voz] evento:', ev.ev, ev.texto || '');
  }

  switch (ev.ev) {
    case 'wake':
      pendiente = null;
      mostrar();
      ui({ tipo: 'wake' });
      decir('Hola, ¿en qué puedo ayudarte?');
      ocultarEn(60000);
      break;

    case 'parcial':
      ui({
        tipo: 'parcial',
        texto: ev.texto
      });
      break;

    case 'orden':
      pendiente = ev.texto;
      mostrar();

      ui({
        tipo: 'orden',
        texto: ev.texto
      });

      decir('Entendí: ' + ev.texto + '. ¿Lo hago?');
      ocultarEn(60000);
      break;

    case 'rechazado':
      pendiente = null;
      ui({ tipo: 'rechazado' });
      decir('Sin problema, te escucho.');
      break;

    case 'confirmado':
      if (pendiente) {
        enviarOrden(pendiente);
        pendiente = null;
      }

      ui({ tipo: 'confirmado' });
      decir('Entendido, voy a revisarlo.');
      ocultarEn(3500);
      break;

    case 'timeout':
      if (pendiente) {
        pendiente = null;
        ui({ tipo: 'cancelada' });
        decir('No escuché la confirmación. Orden cancelada.');
        ocultarEn(4000);
      } else {
        ocultarEn(500);
      }
      break;
  }
}

function lanzarEscucha() {
  if (saliendo) return;

  const args = [path.join(RAIZ, 'listener.py'), '--model', MODELO];
  if (DISPOSITIVO) args.push('--device', DISPOSITIVO);
  if (process.env.AUDIBOT_DEBUG) args.push('--debug');

  listener = spawn(PYTHON, args, {
    stdio: ['pipe', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' }
  });

  readline
    .createInterface({ input: listener.stdout })
    .on('line', (l) => {
      let ev;
      try {
        ev = JSON.parse(l);
      } catch (e) {
        console.error('Evento del listener no válido:', l);
        return;
      }

      // El tipo del evento debería llamarse "ev", pero si tu listener.py lo nombra distinto (evento, tipo...)
      // también se acepta. Si no trae ninguno, se muestra la línea tal cual para ver qué manda.
      const nombre = ev.ev ?? ev.evento ?? ev.event ?? ev.tipo ?? ev.type;
      if (nombre === undefined) {
        if (lineasSinTipo++ < 15) console.log('[voz] línea SIN tipo de evento:', l.slice(0, 200));
        return;
      }
      ev = { ...ev, ev: nombre };

      // Un error al procesar un evento se muestra como lo que es (antes salía como "evento no válido").
      try {
        alEvento(ev);
      } catch (e) {
        console.error('Error al procesar el evento "' + ev.ev + '":', e);
      }
    });

  listener.stderr.on('data', (d) => {
    process.stderr.write('[listener] ' + d);
  });

  listener.on('error', (e) => {
    console.error(
      'No pude lanzar Python (' + PYTHON + '):',
      e.message
    );
  });

  listener.on('exit', (code) => {
    if (saliendo) return;

    // Si se cae 3 veces en 2 minutos (modelo que no carga, falta vosk...), no insistir para siempre:
    // avisar en pantalla. Antes reintentaba sin parar y el micrófono nunca llegaba a escuchar.
    const ahora = Date.now();
    caidasEscucha = caidasEscucha.filter((t) => ahora - t < 120000);
    caidasEscucha.push(ahora);

    if (caidasEscucha.length >= 3) {
      console.error('listener.py se cayó 3 veces seguidas; dejo la voz desactivada.');
      mostrar();
      ui({
        tipo: 'fin',
        fin: {
          error:
            'No pude iniciar el micrófono (listener.py). ' +
            'Revisa vosk, sounddevice y la carpeta del modelo: ' + path.basename(MODELO) + '.'
        }
      });
      ocultarEn(15000);
      return;
    }

    console.error(
      'listener terminó (' + code + '); reintento en 3 s'
    );

    setTimeout(lanzarEscucha, 3000);
  });
}

async function sondear() {
  // Cuando termina una orden: mostrar la tarjeta del resultado.
  try {
    const respuesta = await fetch(BASE + '/api/estado');
    const e = await respuesta.json();
    // El servidor lo llama "resultado_voz": {id, pedido, mensaje, resultado, nivel}. Antes se leía "fin" y nunca llegaba nada.
    const fin = e.resultado_voz;

    if (ultimoFin === null) {
      ultimoFin = fin ? fin.id : '';
      return;
    }

    if (fin && fin.id !== ultimoFin) {
      ultimoFin = fin.id;
      mostrar();
      ui({
        tipo: 'fin',
        fin
      });
      ocultarEn(45000);
    }
  } catch {
    // El servidor puede estar iniciando o temporalmente apagado.
  }
}

function crearBandeja() {
  const iconPath = path.join(__dirname, 'icon.png');

  tray = new Tray(nativeImage.createFromPath(iconPath));
  tray.setToolTip('AudiBot: di "AudiBot" para dar una orden');

  tray.on('double-click', () => {
    shell.openExternal(BASE);
  });

  tray.setContextMenu(
    Menu.buildFromTemplate([
      {
        label: 'Abrir el chat',
        click: () => shell.openExternal(BASE)
      },
      { type: 'separator' },
      {
        label: 'Probar: "AudiBot"',
        click: () => alEvento({ ev: 'wake' })
      },
      {
        label: 'Probar: orden',
        click: () => alEvento({
          ev: 'orden',
          texto: 'revisa las órdenes de agosto'
        })
      },
      {
        label: 'Probar: confirmar',
        click: () => alEvento({ ev: 'confirmado' })
      },
      { type: 'separator' },
      {
        label: 'Salir',
        click: () => app.quit()
      }
    ])
  );
}

app.whenReady().then(() => {
  if (!unica) return;   // segunda instancia: se cierra sin arrancar otro servidor ni otro micrófono

  lanzarServidor();
  crearVentana();
  crearBandeja();

  if (fs.existsSync(MODELO)) {
    lanzarEscucha();
  } else {
    console.error(
      'No hay carpeta de modelo de voz (' +
      MODELO +
      '): el chat funciona, pero no escuchará "AudiBot".'
    );
  }

  setInterval(sondear, 1000);
});

app.on('before-quit', () => {
  saliendo = true;
  clearTimeout(ocultarTimer);

  if (listener) listener.kill();
  if (servidor) servidor.kill();
});

app.on('window-all-closed', (e) => {
  e.preventDefault();
});