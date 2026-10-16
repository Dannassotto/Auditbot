"""Servidor local para la interfaz de AudiBot.

Sirve el HTML/CSS/JS y expone los endpoints que usa app.js:
  GET  /api/estado   -> {"estado": "reposo" | "trabajando" | "deteniendo"}
  POST /api/orden    -> {"texto": "..."}  (ejecuta la auditoría y responde con el resultado)
  POST /api/detener  -> pide parar la auditoría en curso (surte efecto entre una orden y otra)
  POST /api/ack      -> confirmación opcional

Uso (desde la raíz del proyecto):
    python servidor.py
"""
import json
import os
import re
import sqlite3
import threading
import unicodedata
import webbrowser
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

RAIZ = os.path.dirname(os.path.abspath(__file__))
os.chdir(RAIZ)  # las rutas data/auditbot.db y logs/ son relativas a la raíz

from engine.margins import revisar_orden  # noqa: E402
from agent.loop import auditar_orden, Cancelado  # noqa: E402
from services import llm  # noqa: E402

PUERTO = int(os.environ.get("PORT", "5000"))
LIMITE_POR_DEFECTO = 3     # cuántas órdenes investiga si no dices una cantidad
LIMITE_MAXIMO = 5          # tope por consulta, para cuidar los créditos
ESPERA_ANTES_DE_ENVIAR = 2.0   # segundos para arrepentirse: si pulsas Detener aquí, no se envía NADA a Grok 
RUTA_BD = "data/auditbot.db"
RUTA_LOG = "logs/decisiones.jsonl"

ETIQUETAS = {
    "margen_negativo": "Margen negativo",
    "stock_insuficiente": "Stock insuficiente",
    "sin_factura": "Sin factura",
    "factura_duplicada": "Factura duplicada",
    "subfacturada": "Subfacturada",
}

# Carpeta donde está index.html (se busca en estos sitios, en orden).
CANDIDATAS = [".", "web", "ui", "frontend", "public", "static", "interfaz"]
ESTATICOS = os.path.normpath(next(
    (os.path.join(RAIZ, c) for c in CANDIDATAS
     if os.path.exists(os.path.join(RAIZ, c, "index.html"))),
    RAIZ,
))

TIPOS = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}

candado = threading.Lock()
estado = {"estado": "reposo"}
contador = {"n": 0}
cancelar_evento = threading.Event()
progreso = {"hechas": 0, "enviado": False}   # lo que ya pasó en la consulta en curso


def _decision_a_dict(d):
    if isinstance(d, dict):
        return d
    if d is None:
        return {"accion": "revisar",
                "causa": "El agente no llegó a una decisión; requiere revisión humana.",
                "evidencia": "", "perdida_estimada_mxn": 0}
    return {"accion": "revisar", "causa": str(d), "evidencia": "", "perdida_estimada_mxn": 0}


def _plural(n, singular, plural):
    return f"{n} {singular if n == 1 else plural}"


MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
NUMEROS = {"un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
           "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10}


def _sin_acentos(t):
    return "".join(c for c in unicodedata.normalize("NFD", t.lower())
                   if unicodedata.category(c) != "Mn")


def interpretar(texto, hoy):
    """Devuelve ids, cantidad y un filtro de fechas (pasa) con su etiqueta.
    `hoy` es la última fecha de la base, no la del sistema.
    Soporta: "18 de agosto", "del 10 al 20 de agosto", "agosto", "agosto de 2026",
    "hoy", "ayer", "la semana", "el último mes", "3 órdenes", "la orden 55".
    """
    t = _sin_acentos(texto)
    anio = hoy.year
    mes_pat = "|".join(MESES)
    sufijo = r"(?:\s+(?:de|del)\s+(\d{4}))?"

    # --- ids concretos
    ids = set()
    patron_ids = (r"orden(?:es)?\s*(?:numero|num\.?|no\.?|#)?\s*#?\s*"
                  r"(\d+(?:\s*(?:,|y|e)\s*\d+)*)(?!\d|\s*de\s+(?:" + mes_pat + r"))")
    for m in re.finditer(patron_ids, t):
        ids |= {int(x) for x in re.findall(r"\d+", m.group(1))}

    # --- cantidad: "3 órdenes", "tres órdenes"
    cantidad = None
    m = re.search(r"\b(\d+|" + "|".join(NUMEROS) + r")\s+orden(?:es)?\b", t)
    if m:
        cantidad = int(m.group(1)) if m.group(1).isdigit() else NUMEROS[m.group(1)]

    def fecha(d, mes, a=None):
        return f"{int(a or anio):04d}-{MESES.index(mes) + 1:02d}-{int(d):02d}"

    # --- periodo
    desde = hasta = etiqueta = None

    m = re.search(rf"\bdel?\s+(\d{{1,2}})\s+al?\s+(\d{{1,2}})\s+de\s+({mes_pat})\b{sufijo}", t)
    if m:                                               # "del 10 al 20 de agosto"
        desde, hasta = fecha(m[1], m[3], m[4]), fecha(m[2], m[3], m[4])
        etiqueta = f"el periodo del {int(m[1])} al {int(m[2])} de {m[3]} de {m[4] or anio}"
    elif (m := re.search(rf"\b(\d{{1,2}})\s+de\s+({mes_pat})\b{sufijo}", t)):
        desde = hasta = fecha(m[1], m[2], m[3])         # "18 de agosto"
        etiqueta = f"el {int(m[1])} de {m[2]} de {m[3] or anio}"
    elif (m := re.search(rf"\b({mes_pat})\b{sufijo}", t)):
        a = int(m[2] or anio)                           # "agosto"
        n = MESES.index(m[1]) + 1
        ultimo = (date(a + (n == 12), n % 12 + 1, 1) - timedelta(days=1)).day
        desde, hasta = fecha(1, m[1], a), fecha(ultimo, m[1], a)
        etiqueta = f"el mes de {m[1]} de {a}"
    elif re.search(r"\bhoy\b", t):
        desde = hasta = str(hoy)
        etiqueta = f"el día de hoy ({hoy})"
    elif re.search(r"\bayer\b", t):
        desde = hasta = str(hoy - timedelta(days=1))
        etiqueta = f"el día de ayer ({desde})"
    elif "semana" in t:
        desde, hasta = str(hoy - timedelta(days=7)), str(hoy)
        etiqueta = "la última semana"
    elif re.search(r"ultimo mes|mes pasado|ultimos 30", t):
        desde, hasta = str(hoy - timedelta(days=30)), str(hoy)
        etiqueta = "los últimos 30 días"

    pasa = (lambda f: desde <= f <= hasta) if desde else (lambda f: True)
    return {"ids": ids, "cantidad": cantidad, "pasa": pasa, "etiqueta": etiqueta}

def ejecutar_auditoria(texto):
    """Misma lógica del script de consola. Números en el texto = ids de órdenes.

    Devuelve {"mensaje": texto_plano, "resultado": datos_estructurados | None, "cancelado": bool}.
    """
    

    con = sqlite3.connect(RUTA_BD)
    try:
        hoy = date.fromisoformat(con.execute("SELECT MAX(fecha) FROM ordenes").fetchone()[0])
        pedido = interpretar(texto, hoy)
        pedidas = pedido["ids"]
        ids = [r[0] for r in con.execute("SELECT id FROM ordenes ORDER BY id")]
        con_bandera = [h for h in (revisar_orden(con, i) for i in ids) if h["banderas"]]
        base = (f"Revisé {_plural(len(ids), 'orden', 'órdenes')} con las reglas y "
                f"{len(con_bandera)} tienen banderas.")

        if pedidas:
            # Pidió órdenes concretas ("la orden 55"): se investigan esas.
            seleccion = [h for h in con_bandera if h["orden_id"] in pedidas]
            if not seleccion:
                nums = ", ".join(map(str, sorted(pedidas)))
                return {"mensaje": f"{base}\nNinguna de las órdenes pedidas ({nums}) tiene banderas.",
                        "resultado": None}
        else:
            en_periodo = [h for h in con_bandera if pedido["pasa"](h["fecha"])]
            donde = f" en {pedido['etiqueta']}" if pedido["etiqueta"] else ""
            if pedido["etiqueta"]:
                base += f" En {pedido['etiqueta']}: {len(en_periodo)}."

            hechas = set()
            if os.path.exists(RUTA_LOG):
                with open(RUTA_LOG, encoding="utf-8") as f:
                    for linea in f:
                        hechas.add(json.loads(linea)["orden"])
            pendientes = [h for h in en_periodo if h["orden_id"] not in hechas]
            if not pendientes:
                if en_periodo:
                    return {"mensaje": (f"{base}\n" + (f"La orden con bandera{donde} ya fue investigada" if len(en_periodo) == 1 else f"Las {len(en_periodo)} órdenes con banderas{donde} ya fueron investigadas") + " (ver bitácora)."),
                    "resultado": None}
                return {"mensaje": f"{base}\nNo hay órdenes con banderas{donde}.", "resultado": None}

            limite = min(pedido["cantidad"] or LIMITE_POR_DEFECTO, LIMITE_MAXIMO)
            if pedido["cantidad"] and pedido["cantidad"] > LIMITE_MAXIMO:
                base += f" (Máximo {LIMITE_MAXIMO} por consulta, para cuidar los créditos.)"

            por_tipo = {}
            for h in pendientes:
                por_tipo.setdefault(tuple(h["banderas"]), []).append(h)
            seleccion = []
            while len(seleccion) < limite and any(por_tipo.values()):
                for tipo in list(por_tipo):
                    if por_tipo[tipo] and len(seleccion) < limite:
                        seleccion.append(por_tipo[tipo].pop(0))

        os.makedirs("logs", exist_ok=True)
        ordenes = []
        cancelado = False

        # Ventana para arrepentirse: si pulsan Detener aquí, no se envía nada a Grok.
        if cancelar_evento.wait(ESPERA_ANTES_DE_ENVIAR):
            return {"mensaje": "Consulta detenida. No se envió nada a Grok.",
                    "resultado": None, "cancelado": True}

        for h in seleccion:
            # Si cancelaron antes de empezar esta orden, no se gasta nada más.
            if cancelar_evento.is_set():
                cancelado = True
                break

            # La llamada a Grok que ya salió no se puede recoger de vuelta (y se cobra),
            # pero auditar_orden revisa cancelar_evento antes de cada llamada NUEVA.
            progreso["enviado"] = True
            try:
                d = auditar_orden(con, h, cancelar_evento)
            except Cancelado:
                cancelado = True
                break

            # Esa orden ya se pagó, así que se guarda aunque hayan cancelado durante la llamada.
            with open(RUTA_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({"orden": h["orden_id"], "banderas": h["banderas"],
                                    "decision": d}, ensure_ascii=False, default=str) + "\n")
            progreso["hechas"] += 1

            dd = _decision_a_dict(d)
            ordenes.append({
                "orden_id": h["orden_id"],
                "fecha": h["fecha"],
                "cliente": h["cliente"],
                "banderas": [ETIQUETAS.get(b, b) for b in h["banderas"]],
                "margen_pct": h["margen_pct"],
                "accion": dd.get("accion", "revisar"),
                "causa": dd.get("causa", ""),
                "evidencia": dd.get("evidencia", ""),
                "perdida_mxn": dd.get("perdida_estimada_mxn") or 0,
            })

            if cancelar_evento.is_set():
                cancelado = True
                break

        if not ordenes:
            return {"mensaje": "Consulta detenida antes de terminar ninguna orden.",
                    "resultado": None, "cancelado": True}

        alertas = [o for o in ordenes if o["accion"] == "alertar"]
        ignoradas = [o for o in ordenes if o["accion"] == "ignorar"]
        perdida_total = round(sum(o["perdida_mxn"] for o in alertas), 2)
        resumen = (f"{base} Investigué {_plural(len(ordenes), 'orden', 'órdenes')}: "
                   f"{_plural(len(alertas), 'requiere alerta', 'requieren alerta')} "
                   f"y {_plural(len(ignoradas), 'se puede ignorar', 'se pueden ignorar')}.")
        if cancelado:
            resumen += " Consulta detenida: las órdenes restantes no se investigaron."

        resultado = {
            "resumen": resumen,
            "ordenes": ordenes,
            "perdida_total_mxn": perdida_total,
            "gasto_usd": round(llm.gasto_total, 4),
        }

        lineas = [resumen, ""]
        for o in ordenes:
            lineas.append(f"Orden {o['orden_id']} ({o['cliente']}) - {o['accion'].upper()}: {o['causa']}")
        lineas.append(f"\nPérdida estimada en alertas: ${perdida_total:,.2f} MXN")
        return {"mensaje": "\n".join(lineas), "resultado": resultado, "cancelado": cancelado}
    finally:
        con.close()


class Handler(BaseHTTPRequestHandler):
    def _json(self, codigo, datos):
        cuerpo = json.dumps(datos, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def _leer_json(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def do_GET(self):
        ruta = urlparse(self.path).path
        if ruta == "/api/estado":
            return self._json(200, dict(estado))

        if ruta == "/":
            ruta = "/index.html"
        archivo = os.path.normpath(os.path.join(ESTATICOS, ruta.lstrip("/")))
        relativa = os.path.relpath(archivo, ESTATICOS).replace("\\", "/")
        permitido = (
            relativa == "index.html"
            or relativa.startswith(("css/", "js/"))
        ) and os.path.splitext(archivo)[1] in TIPOS
        try:
            dentro = os.path.commonpath([archivo, ESTATICOS]) == ESTATICOS
        except ValueError:
            dentro = False
        if not dentro or not permitido or not os.path.isfile(archivo):
            print(f"  404: buscaba {archivo} (dentro={dentro}, permitido={permitido}, existe={os.path.isfile(archivo)})")
            self.send_response(404)
            self.end_headers()
            return
        with open(archivo, "rb") as f:
            cuerpo = f.read()
        self.send_response(200)
        self.send_header("Content-Type", TIPOS.get(os.path.splitext(archivo)[1], "application/octet-stream"))
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_POST(self):
        ruta = urlparse(self.path).path
        datos = self._leer_json()

        if ruta == "/api/ack":
            return self._json(200, {"ok": True})

        if ruta == "/api/detener":
            if estado["estado"] not in ("trabajando", "deteniendo"):
                return self._json(409, {"error": "No hay ninguna auditoría en curso."})
            cancelar_evento.set()
            estado["estado"] = "deteniendo"
            return self._json(200, {"ok": True, "mensaje": "Solicitud de detención recibida."})

        if ruta == "/api/orden":
            texto = str(datos.get("texto", "")).strip()
            if not texto:
                return self._json(400, {"error": "La orden está vacía."})
            if not candado.acquire(blocking=False):
                # No se toca cancelar_evento aquí: si alguien pidió detener, esa petición debe seguir en pie.
                if estado["estado"] == "deteniendo":
                    return self._json(409, {"error": "Terminando la consulta anterior; intenta de nuevo en unos segundos."})
                return self._json(409, {"error": "Ya hay una orden en curso."})

            estado["estado"] = "trabajando"
            contador["n"] += 1
            id_orden = contador["n"]
            progreso.update(hechas=0, enviado=False)
            salida, hecho = {}, threading.Event()

            def trabajo():
                try:
                    salida["ok"] = ejecutar_auditoria(texto)
                except Exception as e:  # noqa: BLE001
                    salida["error"] = str(e)
                finally:
                    cancelar_evento.clear()
                    estado["estado"] = "reposo"
                    candado.release()
                    hecho.set()

            threading.Thread(target=trabajo, daemon=True).start()

            while not hecho.wait(0.2):
                if cancelar_evento.is_set():       # responder YA, sin esperar a que termine la llamada en vuelo
                    n = progreso["hechas"]
                    if n:
                        msg = (f"Consulta detenida. Alcanzó a investigar {_plural(n, 'orden', 'órdenes')}; "
                               "quedaron guardadas en la bitácora.")
                    elif progreso["enviado"]:
                        msg = ("Consulta detenida. Una llamada a Grok ya había salido: su respuesta se descarta "
                               "y se cobra igual (normalmente menos de un centavo).")
                    else:
                        msg = "Consulta detenida. No se envió nada a Grok."
                    return self._json(200, {"id": id_orden, "mensaje": msg, "resultado": None, "cancelado": True})

            if "error" in salida:
                return self._json(500, {"error": f"Falló la auditoría: {salida['error']}"})
            return self._json(200, {"id": id_orden, **salida["ok"]})

        self.send_response(404)
        self.end_headers()

    def log_message(self, fmt, *args):
        print(f"[{self.address_string()}] {fmt % args}")


if __name__ == "__main__":
    if not os.path.exists(RUTA_BD):   # primera vez tras clonar el repo: la base se genera con seed.py
        raise SystemExit("Falta la base de datos. Corre primero: python db/seed.py")
    url = f"http://127.0.0.1:{PUERTO}"
    print(f"Sirviendo interfaz desde: {ESTATICOS}")
    print(f"Abriendo {url} en el navegador (Ctrl+C para detener)")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    ThreadingHTTPServer(("127.0.0.1", PUERTO), Handler).serve_forever()