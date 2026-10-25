(() => {
  const SUBTITULOS = false;   // true = mostrar en pantalla lo que escucha y entiende; false = solo voz
  const $ = (id) => document.getElementById(id);
  let esperaRobot = null;
  const robot = () => esperaRobot || (esperaRobot = new Promise((ok, mal) => {
    const limite = Date.now() + (window.AUDIBOT_ESPERA_ROBOT || 4000);
    const t = setInterval(() => {
      if (window.AudiBotRobot) { clearInterval(t); ok(window.AudiBotRobot); }
      else if (Date.now() > limite) {
        clearInterval(t); esperaRobot = null;
        mal(new Error('el robot no cargó (window.AudiBotRobot no existe)'));
      }
    }, 100);
  }));
  const nodo = (tag, clase, texto) => {
    const n = document.createElement(tag);
    if (clase) n.className = clase;
    if (texto !== undefined) n.textContent = texto;
    return n;
  };
  const pesos = (n) => '$' + (Number(n) || 0).toLocaleString('es-MX', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  function linea(texto, forzar) {
    if (!SUBTITULOS && !forzar) texto = '';       // los subtítulos van apagados; errores y resultados sí se muestran
    $('linea').textContent = texto || '';
    $('panel').classList.toggle('on', Boolean(texto) || $('tarjeta').childElementCount > 0);
  }

  const sub = (texto) => linea(texto);              // subtítulos: solo se ven si SUBTITULOS = true

  function aviso() {                     // sonido de notificación (sin archivos)
    try {
      const c = new AudioContext();
      [660, 880].forEach((f, i) => {
        const o = c.createOscillator(), g = c.createGain(), t = c.currentTime + i * 0.18;
        o.type = 'sine'; o.frequency.value = f;
        g.gain.setValueAtTime(0.0001, t);
        g.gain.exponentialRampToValueAtTime(0.25, t + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t + 0.35);
        o.connect(g).connect(c.destination); o.start(t); o.stop(t + 0.4);
      });
    } catch (_) { /* sin audio disponible */ }
  }

  function pintarTarjeta(fin) {          // devuelve el nivel de alerta para el robot
    const cont = $('tarjeta');
    cont.replaceChildren();
    if (fin.error || !fin.resultado) { linea(fin.error || fin.mensaje || 'Listo.', true); return fin.error ? 'menor' : 'reposo'; }
    const r = fin.resultado;
    linea('');
    cont.append(nodo('p', 'resumen', r.resumen));
    (r.ordenes || []).forEach((o) => {
      const tipo = o.accion === 'alertar' ? 'alert' : o.accion === 'ignorar' ? 'ok' : '';
      const t = nodo('div', 'tarj ' + tipo);
      t.append(nodo('span', '', o.accion === 'alertar' ? 'Alerta' : o.accion === 'ignorar' ? 'Sin acción' : 'Revisar'),
               nodo('b', '', `Orden #${o.orden_id}`), nodo('p', '', o.causa || ''));
      if (Number(o.perdida_mxn) > 0) t.append(nodo('small', '', 'Pérdida estimada: ' + pesos(o.perdida_mxn)));
      cont.append(t);
    });
    if (Number(r.perdida_total_mxn) > 0) cont.append(nodo('p', 'resumen', 'Pérdida estimada en alertas: ' + pesos(r.perdida_total_mxn)));
    const alertas = (r.ordenes || []).filter((o) => o.accion === 'alertar');
    if (!alertas.length) return 'reposo';
    return alertas.some((o) => Number(o.perdida_mxn) >= 500) ? 'grave' : 'menor';
  }

  $('cerrar').addEventListener('click', () => {
    document.title = 'AudiBot:cerrar';           // main.js lo ve y cierra la ventana
    setTimeout(() => { document.title = 'AudiBot'; }, 200);
  });

  window.__ov = async (ev) => {
    try { await manejar(ev); }
    catch (e) {                                       // antes el fallo se perdía y la ventana "no respondía"
      console.error('[overlay]', ev && ev.tipo, e);
      linea('Error en el robot: ' + e.message, true);
    }
  };

  // La voz la maneja main.js (la del servidor si funciona, si no la de Windows) y aquí llega como "decir".
  // Así no hay dos voces hablando a la vez y siempre se oye algo.
  async function manejar(ev) {
    const r = await robot();
    switch (ev.tipo) {
      case 'wake':
        $('tarjeta').replaceChildren(); $('cerrar').style.display = 'none';
        sub('Te escucho… di tu orden.'); r.setState('escuchando'); break;
      case 'parcial': sub('Escuchando: “' + ev.texto + '”'); break;
      case 'orden': sub('Entendí: “' + ev.texto + '”. ¿Lo hago? Di “sí” o “no”.'); break;
      case 'rechazado': sub('Sin problema. Dime la orden otra vez.'); break;
      case 'confirmado': sub('Entendido. Trabajando en segundo plano…'); r.setState('recibida'); break;
      case 'cancelada': r.setState('reposo'); break;
      case 'decir': r.hablar(ev.texto); break;
      case 'linea': linea(ev.texto, true); break;
      case 'fin': {                                    // al terminar no habla: suena un aviso y sale la tarjeta
        aviso();
        r.setState(pintarTarjeta(ev.fin));
        $('cerrar').style.display = 'inline-block'; $('panel').classList.add('on');
        break;
      }
      case 'reiniciar':
        $('panel').classList.remove('on'); $('tarjeta').replaceChildren(); $('linea').textContent = '';
        $('cerrar').style.display = 'none'; r.setState('reposo'); break;
    }
  }
})();