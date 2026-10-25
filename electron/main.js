
 // Ventana flotante de AudiBot: saludo, confirmación, ejecución en segundo plano y resultado.
const { app, BrowserWindow, Tray, Menu, nativeImage, screen, shell } = require('electron');
const { spawn } = require('child_process');
const readline = require('readline');
const path = require('path');
const fs = require('fs');

const BASE = process.env.AUDIBOT_URL || 'http://localhost:5000';
const PYTHON = process.env.AUDIBOT_PYTHON ||
  (process.platform === 'win32' ? 'python' : 'python3');

const RAIZ = path.join(__dirname, '..');
const MODELO = process.env.AUDIBOT_MODELO ||
  (fs.existsSync(path.join(RAIZ, 'model-grande'))
    ? path.join(RAIZ, 'model-grande')
    : path.join(RAIZ, 'model'));

let win;
let tray;
let listener;
let servidor;
let saliendo = false;
let ocultarTimer = null;
let pendiente = null;
let ultimoFin = null;
let vozServidor = null;

if (!app.requestSingleInstanceLock()) {
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
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });

  win.loadURL(BASE + '/overlay.html');

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
            AUDIBOT_NO_BROWSER: '1'
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


function ui(ev) {
  if (!win || win.isDestroyed()) return;

  win.webContents
    .executeJavaScript(
      `window.__ov && window.__ov(${JSON.stringify(ev)})`
    )
    .catch(() => {});
}

/**
 * Usa exclusivamente Edge TTS a través del panel y del servidor.
 * No se utiliza System.Speech ni ninguna voz de Windows.
 *
 * La voz es-VE-SebastianNeural debe configurarse en servidor.py.
 */
async function decir(texto) {
  if (vozServidor === null) {
    try {
      const respuesta = await fetch(
        BASE + '/api/tts?texto=' + encodeURIComponent('hola')
      );

      vozServidor = respuesta.ok;

      if (!respuesta.ok) {
        console.error(
          'Edge TTS no está disponible. HTTP:',
          respuesta.status
        );
      }
    } catch (e) {
      vozServidor = false;
      console.error(
        'No se pudo conectar con el servicio Edge TTS:',
        e.message
      );
    }
  }

  if (!vozServidor) {
    console.error(
      'AudiBot no reproducirá una voz alternativa. ' +
      'Comprueba servidor.py y el servicio Edge TTS.'
    );
    return;
  }

  // El panel flotante se encarga de reproducir el audio de Edge TTS.
  ui({
    tipo: 'decir',
    texto
  });
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

function pausaListener(segundos) {
  // Evita que el micrófono interprete la voz del robot como una orden.
  if (listener && listener.stdin && listener.stdin.writable) {
    listener.stdin.write(`pausa ${segundos}\n`);
  }
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

function alEvento(ev) {
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

      decir('Entendí: ' + ev.texto + '. ¿Es correcto?');

      pausaListener(2.5 + 0.075 * (ev.texto.length + 20));
      ocultarEn(60000);
      break;

    case 'rechazado':
      pendiente = null;
      ui({ tipo: 'rechazado' });
      decir('De acuerdo. Dime la orden otra vez.');
      pausaListener(4);
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

  listener = spawn(
    PYTHON,
    [
      path.join(RAIZ, 'listener.py'),
      '--model',
      MODELO
    ],
    {
      stdio: ['pipe', 'pipe', 'pipe']
    }
  );

  readline
    .createInterface({ input: listener.stdout })
    .on('line', (l) => {
      try {
        alEvento(JSON.parse(l));
      } catch (e) {
        console.error('Evento del listener no válido:', l);
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
    const fin = e.fin;

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
