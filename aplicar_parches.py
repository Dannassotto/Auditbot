"""Aplica las correcciones pendientes a tus archivos, de una sola vez.

Uso (desde la raíz del proyecto):
    python aplicar_parches.py                 # todo
    python aplicar_parches.py --sin-prompt    # sin tocar el prompt de agent/uci.py

Es seguro repetirlo: lo que ya está aplicado se salta. Antes de escribir un archivo guarda una copia
en respaldo_parches/ y comprueba que quede con sintaxis válida. Si un parche no encuentra su lugar,
ese archivo NO se toca y te dice cuál falló.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

RESPALDO = "respaldo_parches"
SIN_PROMPT = "--sin-prompt" in sys.argv


# ----------------------------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------------------------
def flex(s):
    """Patrón que ignora diferencias de espacios y saltos de línea entre las palabras."""
    return r"\s+".join(re.escape(p) for p in s.split())


def sangrar(bloque, ind):
    return "\n".join((ind + l) if l.strip() else l for l in bloque.split("\n"))


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


class Archivo:
    def __init__(self, ruta):
        self.ruta = ruta
        crudo = open(ruta, "rb").read().decode("utf-8")
        self.crlf = "\r\n" in crudo
        self.texto = crudo.replace("\r\n", "\n")
        self.original = self.texto
        self.res = []          # (estado, nombre)

    def cambiar(self, nombre, old, new):
        if norm(new) in norm(self.texto):          # ya aplicado (se mira primero: el ancla sigue existiendo)
            self.res.append(("ya", nombre))
            return
        patron = re.compile(r"^(?P<ind>[ \t]*)" + flex(old), re.M)
        n = len(patron.findall(self.texto))
        if n == 0:
            self.res.append(("FALTA", nombre))
        elif n > 1:
            self.res.append(("AMBIGUO", f"{nombre} ({n} coincidencias)"))
        else:
            self.texto = patron.sub(lambda m: sangrar(new, m["ind"]), self.texto)
            self.res.append(("ok", nombre))

    def quitar_lineas(self, nombre, inicio_linea):
        patron = re.compile(r"^[ \t]*" + inicio_linea + r"[^\n]*\n", re.M)
        n = len(patron.findall(self.texto))
        if n == 0:
            self.res.append(("ya", nombre))
        else:
            self.texto = patron.sub("", self.texto)
            self.res.append(("ok", f"{nombre} ({n} líneas)"))

    def personalizado(self, nombre, funcion):
        self.res.append((funcion(self), nombre))

    def fallos(self):
        return [r for r in self.res if r[0] in ("FALTA", "AMBIGUO")]

    def guardar(self):
        if self.texto == self.original:
            return "sin cambios"
        os.makedirs(RESPALDO, exist_ok=True)
        marca = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(self.ruta, os.path.join(RESPALDO, f"{os.path.basename(self.ruta)}.{marca}"))
        datos = self.texto.replace("\n", "\r\n") if self.crlf else self.texto
        open(self.ruta, "wb").write(datos.encode("utf-8"))
        return "guardado"


def verificar_sintaxis(arch, modulo_js=False):
    """Devuelve (True, '') si el archivo quedó bien, (False, motivo) si no, (None, motivo) si no se pudo comprobar."""
    if arch.ruta.endswith(".py"):
        try:
            compile(arch.texto, arch.ruta, "exec")
            return True, ""
        except SyntaxError as e:
            return False, f"línea {e.lineno}: {e.msg}"
    node = shutil.which("node")
    if not node:
        return None, "node no está en el PATH: no pude comprobar el JavaScript"
    with tempfile.NamedTemporaryFile("w", suffix=".mjs" if modulo_js else ".js", delete=False, encoding="utf-8") as f:
        f.write(arch.texto)
        ruta = f.name
    try:
        r = subprocess.run([node, "--check", ruta], capture_output=True, text=True)
    finally:
        os.unlink(ruta)
    return (r.returncode == 0), (r.stderr.strip().splitlines() or [""])[-1]


# ----------------------------------------------------------------------------------------------
# servidor.py
# ----------------------------------------------------------------------------------------------
ESTADO_CORRIDA = r'''def estado_corrida(nombre="corrida"):
    """Avance de la corrida larga, leído de logs/<nombre>/. Solo lectura."""
    base = os.path.join("logs", os.path.basename(nombre) or "corrida")
    ords = {}
    for o in _leer_log(os.path.join(base, "ordenes.jsonl")):
        if "pos" in o:
            ords[o["pos"]] = o                       # la última entrada de cada orden
    llam = _leer_log(os.path.join(base, "llamadas.jsonl"))
    errs = _leer_log(os.path.join(base, "errores.jsonl"))
    evs = _leer_log(os.path.join(base, "eventos.jsonl"))
    total = None
    try:
        c = sqlite3.connect("data/uci.db")
        total = c.execute("SELECT COUNT(*) FROM cola").fetchone()[0]
        c.close()
    except sqlite3.Error:
        pass
    inicios = [e for e in evs if e.get("evento") == "inicio"]
    if inicios:
        total = inicios[-1].get("ya_hechas", 0) + inicios[-1].get("pendientes", 0)
    cuenta = lambda r: sum(1 for o in ords.values() if o["resultado"] == r)
    alertas = sum(1 for o in ords.values() if (o.get("decision") or {}).get("accion") == "alertar")
    try:
        antig = time.time() - os.path.getmtime(os.path.join(base, "ordenes.jsonl"))
    except OSError:
        antig = None
    if not evs:
        estado_c = "sin iniciar"
    elif evs[-1].get("evento") == "fin":
        estado_c = "terminada"
    elif antig is not None and antig < 300:
        estado_c = "en curso"
    else:
        estado_c = "detenida sin cierre"
    ultimas = sorted(ords.values(), key=lambda o: o["hora"], reverse=True)[:8]
    return {
        "estado": estado_c, "total": total, "hechas": len(ords),
        "ok": cuenta("ok"), "sin_decision": cuenta("sin_decision"), "error": cuenta("error"),
        "alertas": alertas, "llamadas": len(llam),
        "tokens": sum(x.get("tokens", 0) for x in llam),
        "costo_usd": round(sum(_num(x.get("costo_usd")) for x in llam), 4),
        "intentos_fallidos": len(errs),
        "inicio": inicios[0]["hora"] if inicios else None,
        "ultima": evs[-1]["hora"] if evs else None,
        "ultimas": [{"regla": o["regla"], "resultado": o["resultado"],
                     "accion": (o.get("decision") or {}).get("accion") or o.get("codigo", "-"),
                     "causa": ((o.get("decision") or {}).get("causa") or o.get("nota") or o.get("error") or "")[:160],
                     "costo_usd": o.get("costo_usd")} for o in ultimas],
    }


def _estado_publico():'''


def parchar_servidor(a):
    a.quitar_lineas("quitar `from openai import audio` (no se usa)", r"from openai import audio\b")
    a.cambiar("import time", "import threading", "import threading\nimport time")
    a.cambiar("importar ErrorLLM",
              "from services import llm  # noqa: E402",
              "from services import llm  # noqa: E402\nfrom services.llm import ErrorLLM  # noqa: E402")
    a.quitar_lineas("quitar los print [DEBUG] que rompían 'la orden 55'", r'print\(f"\[DEBUG\]')
    a.cambiar("variable codigo_parada", "tope = False", "tope = False\ncodigo_parada = None")
    a.cambiar(
        "errores del LLM con código",
        '''except Exception as e:  # noqa: BLE001 - una orden que falla no tumba toda la consulta
               if "Tope de gasto" in str(e):
                   tope = True
                   break
               d, err = None, f"{type(e).__name__}: {e}"[:200]''',
        '''except ErrorLLM as e:
    if e.info["codigo"] in ("TOPE_GASTO", "LLAVE_INVALIDA"):
        tope = True
        codigo_parada = e.info["codigo"]
        break
    d, err = None, f"{e.info['codigo']}: {e.info['mensaje']}"
except Exception as e:  # noqa: BLE001 - una orden que falla no tumba toda la consulta
    if "Tope de gasto" in str(e):
        tope = True
        codigo_parada = "TOPE_GASTO"
        break
    d, err = None, f"{type(e).__name__}: {e}"[:200]''')
    a.cambiar(
        "respuesta de tope/llave con código",
        '''if tope:
               return {"mensaje": f"Tope de gasto alcanzado (US${llm.MAX_BUDGET_USD:.2f}): no hice más llamadas a Grok.",
                       "resultado": None}''',
        '''if tope:
    if codigo_parada == "LLAVE_INVALIDA":
        return {"mensaje": "La API_KEY no es válida. Revisa tu archivo .env y reinicia AudiBot.",
                "resultado": None, "codigo": "LLAVE_INVALIDA"}
    return {"mensaje": f"Tope de gasto alcanzado (US${llm.MAX_BUDGET_USD:.2f}): no hice más llamadas a Grok.",
            "resultado": None, "codigo": "TOPE_GASTO"}''')
    a.cambiar(
        "nota según la causa de la parada",
        'nota = " Tope de gasto alcanzado: las órdenes restantes no se investigaron." if tope else ""',
        '''nota = ((" Me detuve: la API_KEY no es válida." if codigo_parada == "LLAVE_INVALIDA"
         else " Tope de gasto alcanzado: las órdenes restantes no se investigaron.") if tope else "")''')
    a.cambiar("función estado_corrida (panel)", "def _estado_publico():", ESTADO_CORRIDA)
    a.cambiar(
        "endpoint /api/corrida",
        'if ruta == "/api/estado": return self._json(200, _estado_publico())',
        '''if ruta == "/api/estado":
    return self._json(200, _estado_publico())

if ruta == "/api/corrida":
    nombre = parse_qs(urlparse(self.path).query).get("dir", ["corrida"])[0]
    return self._json(200, estado_corrida(nombre))''')
    a.cambiar(
        "HTTP 503 con código cuando se detiene por tope/llave",
        'return self._json( 200, {"id": id_orden, **salida["ok"]} )',
        '''if salida["ok"].get("codigo"):
    return self._json(503, {"error": salida["ok"]["mensaje"],
                            "codigo": salida["ok"]["codigo"], "reintentable": False})
return self._json(
    200, {"id": id_orden, **salida["ok"]}
)''')
    a.cambiar(
        "HTTP 500 con código",
        '''500, { "error": ( f"Falló la auditoría: {salida['error']}" ) }''',
        '''500,
{
    "error": f"Falló la auditoría: {salida['error']}",
    "codigo": "DESCONOCIDO"
}''')


# ----------------------------------------------------------------------------------------------
# js/app.js
# ----------------------------------------------------------------------------------------------
PANEL_JS = r'''// ---- Panel de la corrida larga (solo observa: la corrida se lanza con worker.py) ----
(function panelCorrida() {
  const dir = new URLSearchParams(location.search).get('dir') || 'corrida';
  const css = document.createElement('style');
  css.textContent = `
    .corrida-btn { position: fixed; right: 16px; top: 88px; z-index: 50; padding: 8px 14px; border-radius: 999px;
      border: 1px solid rgba(148,163,184,.4); background: rgba(15,23,42,.9); color: inherit; cursor: pointer; }
    .corrida-panel { position: fixed; right: 16px; top: 134px; z-index: 50; width: min(420px, 92vw); max-height: 70vh;
      overflow: auto; padding: 14px; border-radius: 14px; border: 1px solid rgba(148,163,184,.3);
      background: rgba(15,23,42,.96); color: #e2e8f0; font-size: .9em; }
    .corrida-barra { height: 10px; border-radius: 999px; background: rgba(148,163,184,.2); overflow: hidden; margin: 8px 0; }
    .corrida-barra > div { height: 100%; background: #22c55e; width: 0; transition: width .4s; }
    .corrida-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 12px; margin-bottom: 10px; }
    .corrida-item { border-top: 1px solid rgba(148,163,184,.2); padding: 6px 0; line-height: 1.4; }
    .corrida-item small { opacity: .7; }`;
  document.head.appendChild(css);

  const btn = el('button', 'corrida-btn', 'Ver corrida');
  btn.type = 'button';
  const panel = el('div', 'corrida-panel hidden');
  document.body.append(btn, panel);
  let timer = null;

  const hms = (ms) => {
    const m = Math.max(0, Math.floor(ms / 60000));
    return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min`;
  };

  async function pintar() {
    let d;
    try {
      const r = await fetch('/api/corrida?dir=' + encodeURIComponent(dir), { cache: 'no-store' });
      d = await r.json();
    } catch (_) { panel.replaceChildren(el('p', '', 'No se pudo leer la corrida.')); return; }

    const total = d.total || 0;
    const pct = total ? Math.min(100, (d.hechas / total) * 100) : 0;
    const fin = d.estado === 'en curso' ? Date.now() : (d.ultima ? Date.parse(d.ultima) : Date.now());
    const transcurrido = d.inicio ? hms(fin - Date.parse(d.inicio)) : '—';
    const barra = el('div', 'corrida-barra'); barra.appendChild(el('div'));
    barra.firstChild.style.width = pct + '%';
    const celdas = [
      ['Estado', d.estado], ['Tiempo', transcurrido],
      ['Con decisión', d.ok], ['Alertas', d.alertas],
      ['Sin decisión', d.sin_decision], ['Con error', d.error],
      ['Llamadas', d.llamadas], ['Tokens', Number(d.tokens).toLocaleString('es-MX')],
      ['Costo', 'US$' + Number(d.costo_usd).toFixed(4)], ['Intentos fallidos', d.intentos_fallidos],
    ];
    const grid = el('div', 'corrida-grid');
    celdas.forEach(([k, v]) => grid.append(el('span', '', k + ': '), el('strong', '', String(v))));
    const lista = el('div');
    (d.ultimas || []).forEach((o) => {
      const item = el('div', 'corrida-item');
      item.append(el('strong', '', `${o.accion} · ${o.regla}`), el('br'), el('small', '', o.causa || ''));
      lista.appendChild(item);
    });
    panel.replaceChildren(
      el('strong', '', `Corrida: ${d.hechas} de ${total || '?'} órdenes (${pct.toFixed(1)}%)`),
      barra, grid, el('strong', '', 'Últimas decisiones'), lista);
  }

  btn.addEventListener('click', () => {
    panel.classList.toggle('hidden');
    clearInterval(timer);
    if (!panel.classList.contains('hidden')) { pintar(); timer = setInterval(pintar, 5000); }
  });
})();

renderHistory();
setupSpeechRecognition();'''


def parchar_app(a):
    a.cambiar(
        "la burbuja de error lleva data-codigo",
        "message.className = `message ${role}${options.error ? ' error' : ''}`;",
        "message.className = `message ${role}${options.error ? ' error' : ''}`;\n"
        "if (options.codigo) message.dataset.codigo = options.codigo;")
    a.cambiar(
        "el error del servidor conserva su código",
        "const detail = data.error || data.mensaje || `Error ${response.status}`; throw new Error(detail);",
        "const err = new Error(data.error || data.mensaje || `Error ${response.status}`);\n"
        "err.codigo = data.codigo;\n"
        "throw err;")
    a.cambiar(
        "catch: código del error y 'sin conexión' solo si de verdad no hubo red",
        "addMessage('system', `No pude enviar la orden. ${error.message || 'Verifica que servidor.py esté ejecutándose.'}`, { error: true });"
        " toast('No se pudo enviar la orden.', 'error'); setConnected(false);",
        "const sinRed = error instanceof TypeError;   // fetch ni llegó al servidor\n"
        "const codigo = error.codigo || (sinRed ? 'SIN_CONEXION' : 'DESCONOCIDO');\n"
        "addMessage('system', `No pude enviar la orden. ${error.message || 'Verifica que servidor.py esté ejecutándose.'}`, { error: true, codigo });\n"
        "toast('No se pudo enviar la orden.', 'error');\n"
        "if (sinRed) setConnected(false);")
    if "function panelCorrida" in a.texto:      # ya está (aunque le hayas cambiado el CSS): no se duplica
        a.res.append(("ya", "botón y panel 'Ver corrida'"))
    else:
        a.cambiar("botón y panel 'Ver corrida'",
                  "renderHistory(); setupSpeechRecognition();", PANEL_JS)


# ----------------------------------------------------------------------------------------------
# js/robot.js
# ----------------------------------------------------------------------------------------------
HABLAR_LOCAL = '''  function hablarLocal(texto) {
    if (!('speechSynthesis' in window)) return;
    const u = new SpeechSynthesisUtterance(texto);
    u.lang = 'es-ES';
    speechSynthesis.cancel();
    speechSynthesis.speak(u);
  }

'''

ERRORES_JS = '''const ERRORES = {
  LIMITE_VELOCIDAD: { estado: 'fallo',     voz: 'Hay mucho tráfico. Puedes reintentar en unos segundos.' },
  TIMEOUT:          { estado: 'fallo',     voz: 'El modelo tardó demasiado. Puedes reintentar.' },
  ERROR_SERVIDOR:   { estado: 'fallo',     voz: 'El servicio tuvo un problema. Reintenta en un momento.' },
  SIN_CONEXION:     { estado: 'fallo',     voz: 'No hay conexión con el servicio. Revisa el servidor.' },
  LLAVE_INVALIDA:   { estado: 'bloqueado', voz: 'Mi llave no es válida. Revisa el archivo punto env.' },
  TOPE_GASTO:       { estado: 'bloqueado', voz: 'Llegué al tope de gasto. Me detengo.' },
  DESCONOCIDO:      { estado: 'fallo',     voz: 'Ocurrió un error inesperado.' },
};
let vozError = null;

function iniciar() {'''


def _arreglar_hablar(a):
    pos = [m.start() for m in re.finditer(r"^[ \t]*async function hablar\(texto\)", a.texto, re.M)]
    if len(pos) == 2:        # la primera versión está duplicada y llama a una función que no existía
        a.texto = a.texto[:pos[0]] + HABLAR_LOCAL + a.texto[pos[1]:]
        return "ok"
    if len(pos) == 1:
        if "function hablarLocal" in a.texto:
            return "ya"
        a.texto = a.texto[:pos[0]] + HABLAR_LOCAL + a.texto[pos[0]:]
        return "ok"
    return "FALTA"


def parchar_robot(a):
    a.personalizado("hablar duplicada + hablarLocal que no existía", _arreglar_hablar)
    a.cambiar("estados 'fallo' y 'bloqueado'",
              "menor: { label: 'Aviso', color: 0xff9a1f },",
              "menor:      { label: 'Aviso',          color: 0xff9a1f },\n"
              "fallo:      { label: 'Error, reintentable', color: 0xffd400 },\n"
              "bloqueado:  { label: 'Detenido',       color: 0xff3b30 },")
    a.cambiar("tabla de códigos de error", "function iniciar() {", ERRORES_JS)
    a.cambiar("el robot lee el código del error",
              "if (ultimo.classList.contains('error')) return 'menor';",
              "if (ultimo.classList.contains('error')) {\n"
              "  const e = ERRORES[ultimo.dataset.codigo] || ERRORES.DESCONOCIDO;\n"
              "  vozError = e.voz;\n"
              "  return e.estado;\n"
              "}")
    a.cambiar("el robot dice el error",
              "const e = ESTADOS[s];",
              "const e = ESTADOS[s];\n"
              "if ((s === 'fallo' || s === 'bloqueado') && vozError) { hablar(vozError); vozError = null; }")
    a.cambiar("'fallo' vuelve solo a reposo ('bloqueado' no)",
              "if (s === 'grave' || s === 'menor') timer = setTimeout(() => setState('reposo'), SEGUNDOS_ALERTA * 1000);",
              "if (s === 'grave' || s === 'menor' || s === 'fallo') timer = setTimeout(() => setState('reposo'), SEGUNDOS_ALERTA * 1000);")
    a.cambiar("animación de 'fallo'",
              "case 'grave': case 'menor': {",
              "case 'grave': case 'menor': case 'fallo': {")
    a.cambiar("animación de 'bloqueado'",
              "default: chestI = 0.35;",
              "case 'bloqueado':\n"
              "  eyeI = 0.6 + 0.4 * Math.sin(tt * 3); chestI = 0.8; wiggle = 0.02; break;\n"
              "default: chestI = 0.35;")


# ----------------------------------------------------------------------------------------------
# agent/uci.py (prompt)
# ----------------------------------------------------------------------------------------------
def parchar_uci(a):
    a.cambiar(
        "prompt: no usar 'compra anterior al archivo' como excusa lejos del inicio",
        "- Los datos empiezan el 2009-12-01: que falte una compra anterior puede deberse a que ocurrió antes del archivo.",
        "- Los datos empiezan el 2009-12-01. Menciona que la compra podría ser anterior al archivo solo si la línea "
        "está a menos de 3 meses de esa fecha; después es poco probable y no lo uses como excusa.")


# ----------------------------------------------------------------------------------------------
# .gitignore y requirements.txt
# ----------------------------------------------------------------------------------------------
def anexar_lineas(ruta, lineas):
    if not os.path.exists(ruta):
        return "no existe (lo omito)"
    crudo = open(ruta, "rb").read()
    if crudo[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return f"está en UTF-16: agrega a mano -> {', '.join(lineas)}"
    try:
        texto = crudo.decode("utf-8")
    except UnicodeDecodeError:
        return f"codificación rara: agrega a mano -> {', '.join(lineas)}"
    existentes = {l.strip() for l in texto.splitlines()}
    nuevas = [l for l in lineas if l not in existentes]
    if not nuevas:
        return "ya estaba"
    with open(ruta, "ab") as f:
        if texto and not texto.endswith("\n"):
            f.write(b"\n")
        f.write(("\n".join(nuevas) + "\n").encode("utf-8"))
    return "agregado: " + ", ".join(nuevas)


# ----------------------------------------------------------------------------------------------
# Ejecución
# ----------------------------------------------------------------------------------------------
def procesar(ruta, parchar, modulo_js=False):
    print(f"\n== {ruta}")
    if not os.path.exists(ruta):
        print("   no existe: lo omito")
        return False
    a = Archivo(ruta)
    parchar(a)
    for estado, nombre in a.res:
        marca = {"ok": "[aplicado]", "ya": "[ya estaba]"}.get(estado, f"[{estado}]")
        print(f"   {marca:<12} {nombre}")
    if a.fallos():
        print("   -> NO toqué este archivo porque un parche no encontró su lugar. Pégame esta salida.")
        return False
    bien, motivo = verificar_sintaxis(a, modulo_js)
    if bien is False:
        print(f"   -> NO toqué este archivo: quedaría con error de sintaxis ({motivo}). Pégame esta salida.")
        return False
    if bien is None:
        print(f"   (aviso: {motivo})")
    print("   ->", a.guardar())
    return True


def main():
    todos = []
    try:
        servidor_ya_completo = "def estado_corrida(" in open("servidor.py", encoding="utf-8").read()
    except OSError:
        servidor_ya_completo = False
    if servidor_ya_completo:
        print("\n== servidor.py\n   ya trae estado_corrida: es la versión completa nueva, no la toco.")
    else:
        todos.append(procesar("servidor.py", parchar_servidor))
    todos.append(procesar(os.path.join("js", "app.js"), parchar_app))
    todos.append(procesar(os.path.join("js", "robot.js"), parchar_robot, modulo_js=True))
    if SIN_PROMPT:
        print("\n== agent/uci.py\n   omitido (--sin-prompt)")
    else:
        todos.append(procesar(os.path.join("agent", "uci.py"), parchar_uci))

    print("\n== .gitignore:", anexar_lineas(".gitignore", ["data/uci/", "data/uci.db", "database/", "respaldo_parches/"]))
    print("== requirements.txt:", anexar_lineas("requirements.txt", ["openpyxl"]))
    if os.path.exists("requirements.txt"):
        try:
            if "pandas" in open("requirements.txt", encoding="utf-8").read().lower():
                print("   OJO: requirements.txt menciona pandas y tu Windows bloquea sus DLL; quítalo si no lo usas.")
        except UnicodeDecodeError:
            pass

    print("\nListo." if all(todos) else "\nTerminé, pero algún archivo quedó sin tocar (ver arriba).")
    print("Siguiente: python -m pytest")


if __name__ == "__main__":
    main()