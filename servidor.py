"""Servidor local para la interfaz de AudiBot.

Sirve el HTML/CSS/JS y expone los endpoints que usa app.js:
  GET  /api/estado   -> {"estado": "reposo" | "trabajando" | "deteniendo", "alerta": {...} | null,
                          "mensaje", "hechas", "total", "llamadas", "gasto_acumulado", ...}
  GET  /api/corrida?dir=corrida -> avance de la corrida larga, leído de logs/<dir>/ (solo lectura)
  POST /api/orden    -> {"texto": "..."}  (ejecuta la auditoría y responde con el resultado;
                          con "async": true responde al instante y trabaja en segundo plano)
  POST /api/detener  -> pide parar la auditoría en curso (surte efecto entre una orden y otra)
  POST /api/ack      -> confirmación opcional de que el chat recibió el resultado
  POST /api/alerta/confirmar -> el usuario cerró la alerta / la tarjeta de resultado del robot
  GET  /api/tts?texto=...    -> voz de AudiBot (edge-tts); 503 si no está instalado

Uso (desde la raíz del proyecto):
    python servidor.py
"""
import asyncio
import glob
import json
import os
import re
import time
import traceback
import sqlite3
import threading
import unicodedata
import webbrowser
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

RAIZ = os.path.dirname(os.path.abspath(__file__))
os.chdir(RAIZ)  # las rutas data/auditbot.db y logs/ son relativas a la raíz

from engine.margins import revisar_orden  # noqa: E402
from agent.loop import auditar_orden, Cancelado  # noqa: E402
from services import llm  # noqa: E402
from services.llm import ErrorLLM  # noqa: E402
from services import chat_uci  # noqa: E402

PUERTO = int(os.environ.get("PORT", "5000"))
LIMITE_POR_DEFECTO = 3     # cuántas órdenes investiga si no dices una cantidad
LIMITE_MAXIMO = 10          # tope por consulta, para cuidar los créditos
MAX_GUARDADAS = 10         # cuántas decisiones ya guardadas se muestran de una vez (no cuestan créditos)
UMBRAL_GRAVE_MXN = 500    # a partir de esta pérdida el robot marca "alerta grave"
ESPERA_ANTES_DE_ENVIAR = 2.0   # segundos para arrepentirse: si pulsas Detener aquí, no se envía NADA a Grok (0 = sin espera)
SEGUNDOS_CORRIDA_ACTIVA = 900  # si la bitácora no cambia en este tiempo, la corrida se da por detenida
# worker.py escribe "fin" también cuando se detiene antes de acabar; el evento previo dice por qué.
MOTIVOS_PARADA = {"stop_manual": "se detuvo manualmente", "tope_gasto": "se alcanzó el tope de gasto",
                  "limite_horas": "se alcanzó el límite de horas", "parada_por_error": "se detuvo por un error",
                  "interrumpido": "fue interrumpida"}
RUTA_BD = "data/auditbot.db"
RUTA_UCI = "data/uci.db"            # datos reales (modo "UCI" del chat)
DIR_CHAT_UCI = "logs/chat_uci"
RUTA_LOG = "logs/decisiones.jsonl"
RUTA_LLAMADAS = "logs/llamadas.jsonl"

VOZ_TTS = "es-VE-SebastianNeural"
VOZ_RITMO = "-5%"
VOZ_TONO = "+0Hz"               # más grave = "-20Hz", más agudo = "+20Hz"

ETIQUETAS = {
    "margen_negativo": "Margen negativo",
    "stock_insuficiente": "Stock insuficiente",
    "sin_factura": "Sin factura",
    "factura_duplicada": "Factura duplicada",
    "subfacturada": "Subfacturada",
    "datos_incompletos": "Datos incompletos",
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
    ".glb": "model/gltf-binary",
}

INICIO = int(time.time())   # distingue las órdenes de esta ejecución de las de una anterior
historial_voz = []     # últimas órdenes dadas por voz con su resultado: el chat las muestra como burbujas
MAX_HISTORIAL_VOZ = 20
candado = threading.Lock()
estado = {"estado": "reposo", "alerta": None, "resultado_voz": None, "pedido_voz": None, "mensaje": "Listo para recibir una orden."}
contador = {"n": 0}
cancelar_evento = threading.Event()
progreso = {"hechas": 0, "total": 0, "enviado": False}   # lo que ya pasó en la consulta en curso


def _decision_a_dict(d):
    if isinstance(d, dict):
        return d
    if d is None:
        return {"accion": "revisar",
                "causa": "El agente no llegó a una decisión; requiere revisión humana.",
                "evidencia": "", "perdida_estimada_mxn": 0}
    return {"accion": "revisar", "causa": str(d), "evidencia": "", "perdida_estimada_mxn": 0}


def _leer_log(ruta):
    """Lee un .jsonl saltando líneas vacías o dañadas (una línea mala no tumba la consulta)."""
    filas = []
    if os.path.exists(ruta):
        with open(ruta, encoding="utf-8-sig") as f:
            for linea in f:
                try:
                    filas.append(json.loads(linea))
                except ValueError:
                    pass
    return filas


def _num(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


_cache_gasto = {"firma": None, "llamadas": 0, "total": 0.0}


def _gasto_acumulado():
    """(llamadas, USD) de logs/llamadas.jsonl; solo se relee si el archivo cambió."""
    try:
        st = os.stat(RUTA_LLAMADAS)
        firma = (st.st_mtime_ns, st.st_size)
    except OSError:
        return 0, 0.0
    if _cache_gasto["firma"] != firma:
        filas = _leer_log(RUTA_LLAMADAS)
        _cache_gasto.update(firma=firma, llamadas=len(filas), total=sum(_num(x.get("costo_usd")) for x in filas))
    return _cache_gasto["llamadas"], _cache_gasto["total"]


def _reciente(evento, segundos):
    try:
        return (datetime.now() - datetime.fromisoformat(evento["hora"])).total_seconds() < segundos
    except (KeyError, ValueError):
        return False


def estado_corrida(nombre="corrida"):
    """Avance de la corrida larga, leído de logs/<nombre>/. Solo lectura: no toca nada."""
    nombre = nombre if re.fullmatch(r"[A-Za-z0-9_-]{1,40}", nombre or "") else "corrida"
    base = os.path.join("logs", nombre)
    ords = {}
    for o in _leer_log(os.path.join(base, "ordenes.jsonl")):
        if "pos" in o:
            ords[o["pos"]] = o                       # la última entrada de cada orden
    llam = _leer_log(os.path.join(base, "llamadas.jsonl"))
    errs = _leer_log(os.path.join(base, "errores.jsonl"))
    evs = _leer_log(os.path.join(base, "eventos.jsonl"))
    total = None
    if os.path.exists("data/uci.db"):                # sin crear archivos vacíos por accidente
        try:
            c = sqlite3.connect("file:data/uci.db?mode=ro", uri=True)
            total = c.execute("SELECT COUNT(*) FROM cola").fetchone()[0]
            c.close()
        except sqlite3.Error:
            pass
    inicios = [e for e in evs if e.get("evento") == "inicio"]
    if inicios:
        total = inicios[-1].get("ya_hechas", 0) + inicios[-1].get("pendientes", 0)
    cuenta = lambda r: sum(1 for o in ords.values() if o.get("resultado") == r)
    alertas = sum(1 for o in ords.values() if (o.get("decision") or {}).get("accion") == "alertar")
    try:
        antig = time.time() - os.path.getmtime(os.path.join(base, "ordenes.jsonl"))
    except OSError:
        antig = None
    motivo = None
    if not evs:
        estado_c = "sin iniciar"
    elif evs[-1].get("evento") == "fin":
        previo = evs[-2] if len(evs) > 1 else {}
        if previo.get("evento") in MOTIVOS_PARADA:          # "fin" no siempre significa que acabó
            estado_c, motivo = "detenida", MOTIVOS_PARADA[previo["evento"]]
            if previo.get("codigo"):
                motivo += f" ({previo['codigo']})"
        else:
            estado_c = "terminada"
    elif evs[-1].get("evento") == "pausa_por_errores_seguidos" and _reciente(evs[-1], 330):
        estado_c, motivo = "en pausa", "demasiados errores seguidos; reintenta en unos minutos"
    elif antig is not None and antig < SEGUNDOS_CORRIDA_ACTIVA:
        estado_c = "en curso"
    else:
        estado_c = "detenida sin cierre"
    ultimas = sorted(ords.values(), key=lambda o: o.get("hora", ""), reverse=True)[:8]
    return {
        "estado": estado_c, "motivo": motivo, "total": total, "hechas": len(ords),
        "ok": cuenta("ok"), "sin_decision": cuenta("sin_decision"), "error": cuenta("error"),
        "alertas": alertas, "llamadas": len(llam),
        "tokens": sum(x.get("tokens", 0) or 0 for x in llam),
        "costo_usd": round(sum(_num(x.get("costo_usd")) for x in llam), 4),
        "intentos_fallidos": len(errs),
        "inicio": inicios[0].get("hora") if inicios else None,
        "ultima": evs[-1].get("hora") if evs else None,
        "ultimas": [{"regla": o.get("regla", "-"), "resultado": o.get("resultado", "-"),
                     "accion": (o.get("decision") or {}).get("accion") or o.get("codigo", "-"),
                     "causa": ((o.get("decision") or {}).get("causa") or o.get("nota") or o.get("error") or "")[:160],
                     "costo_usd": o.get("costo_usd")} for o in ultimas],
    }


def _dir_corrida_por_defecto():
    """La carpeta de corrida que más recientemente escribió eventos (la normal es logs/corrida)."""
    if os.path.exists(os.path.join("logs", "corrida", "eventos.jsonl")):
        return "corrida"
    hay = [(os.path.getmtime(f), os.path.basename(os.path.dirname(f)))
           for f in glob.glob(os.path.join("logs", "*", "eventos.jsonl"))]
    return max(hay)[1] if hay else None


def es_pedido_corrida(texto):
    """"ver corrida", "cómo va la corrida piloto2"… (si piden datos reales de UCI es otra consulta)."""
    t = unicodedata.normalize("NFD", texto.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return bool(re.search(r"\bcorrida\b", t)) and not chat_uci.es_pedido_uci(texto)


def texto_corrida(texto):
    """Respuesta de chat con el avance de una corrida: solo lee archivos, no llama a Grok ni gasta nada."""
    t = texto.lower()
    if re.search(r"\b(inicia|iniciar|lanza|lanzar|arranca|arrancar|empieza|empezar|corre|correr)\b", t):
        return ("Las corridas largas no se lanzan desde el chat: se arrancan en la consola con worker.py "
                "(así siguen aunque cierres la ventana). Aquí puedes ver su avance escribiendo «ver corrida».")
    carpetas = sorted((os.path.basename(os.path.dirname(f))
                       for f in glob.glob(os.path.join("logs", "*", "eventos.jsonl"))), key=len, reverse=True)
    # "corrida" aparece en cualquier petición: solo cuenta como nombre si no se mencionó otra carpeta
    nombre = next((c for c in carpetas if c != "corrida" and c.lower() in t), None) or _dir_corrida_por_defecto()
    if not nombre:
        return ("Todavía no hay ninguna corrida registrada. Se lanza desde la consola con worker.py; "
                "cuando exista, aquí verás su avance.")
    e = estado_corrida(nombre)
    cab = f"Corrida «{nombre}»: {e['estado']}" + (f" ({e['motivo']})" if e["motivo"] else "") + "."
    avance = (f"Avance: {e['hechas']} de {e['total']} ({e['hechas'] / e['total']:.0%})."
              if e["total"] else f"Avance: {e['hechas']} órdenes.")
    lineas = [cab, avance,
              f"Decisiones: {e['ok']} correctas, {e['sin_decision']} sin decisión, {e['error']} con error; "
              f"alertas: {e['alertas']}.",
              f"Consumo: US${e['costo_usd']:.4f} en {e['llamadas']} llamadas a Grok ({e['tokens']:,} tokens)"
              + (f", unos US${e['costo_usd'] / e['hechas']:.4f} por orden." if e["hechas"] else "."),
              f"Intentos fallidos (reintentados): {e['intentos_fallidos']}."]
    if e["ultima"]:
        lineas.append(f"Última actividad: {e['ultima']}.")
    if e["ultimas"]:
        lineas.append("Últimas órdenes:")
        lineas += [f"- {o['regla']}: {o['accion']} — {o['causa'][:90]}" for o in e["ultimas"][:5]]
    return "\n".join(lineas)


def _estado_publico():
    llamadas, gasto = _gasto_acumulado()
    e = dict(estado)
    e.update(voz_ultimo=historial_voz[-1]["id"] if historial_voz else None,
             hechas=progreso["hechas"], total=progreso["total"], llamadas=llamadas,
             gasto_acumulado=round(gasto, 4), gasto_sesion=round(llm.gasto_total, 4))
    return e


def _plural(n, singular, plural):
    return f"{n} {singular if n == 1 else plural}"


def _pct(x):
    return "sin dato" if x is None else f"{x}%"


def _hay_hallazgo(h):
    """Una orden va a Grok si tiene alguna bandera real; 'datos_incompletos' solo avisa que no se pudo revisar."""
    return any(b != "datos_incompletos" for b in h["banderas"])


MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
NUMEROS = {"un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
           "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10}


def _sin_acentos(t):
    return "".join(c for c in unicodedata.normalize("NFD", t.lower())
                   if unicodedata.category(c) != "Mn")


_UNI = "dos|tres|cuatro|cinco|seis|siete|ocho|nueve"
_ESP = ("diez|once|doce|trece|catorce|quince|dieciseis|diecisiete|dieciocho|diecinueve|veinte|veintiuno|veintiun|"
        "veintidos|veintitres|veinticuatro|veinticinco|veintiseis|veintisiete|veintiocho|veintinueve")
_DEC = "treinta|cuarenta|cincuenta|sesenta|setenta|ochenta|noventa"
_CEN = "cien|ciento|doscientos|trescientos|cuatrocientos|quinientos|seiscientos|setecientos|ochocientos|novecientos"
_VALORES = {w: i for i, w in enumerate(
    ["", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez", "once", "doce", "trece",
     "catorce", "quince", "dieciseis", "diecisiete", "dieciocho", "diecinueve", "veinte", "veintiuno", "veintidos",
     "veintitres", "veinticuatro", "veinticinco", "veintiseis", "veintisiete", "veintiocho", "veintinueve"])}
_VALORES["veintiun"] = 21
_VALORES.update({w: 30 + 10 * i for i, w in enumerate(_DEC.split("|"))})
_VALORES.update({"cien": 100, "ciento": 100, **{w: 200 + 100 * i for i, w in enumerate(_CEN.split("|")[2:])}})
_NUM_PALABRAS = re.compile(
    rf"\b(?:(?P<c>{_CEN})\s+)?(?:(?P<d>{_DEC})(?:\s+y\s+(?P<u>uno|{_UNI}))?|(?P<e>{_ESP})|(?P<n>{_UNI})(?!\w))\b"
    rf"|\b(?P<c2>{_CEN})\b")


def _numeros_en_palabras(t):
    """"dieciocho de agosto" -> "18 de agosto". Vosk (la voz) escribe los números con letras; sin esto la fecha se perdía.
    Entiende de 2 a 999 y "uno" solo cuando va como fecha ("del uno al diez", "el uno de agosto")."""
    def valor(m):
        v = _VALORES[m["c"]] if m["c"] else 0
        for g in ("d", "u", "e", "n"):
            if m[g]:
                v += _VALORES[m[g]]
        return v if m["c"] or m["d"] or m["e"] or m["n"] else _VALORES[m["c2"]]
    t = _NUM_PALABRAS.sub(lambda m: str(valor(m)), t)
    t = re.sub(r"\b(?:uno|primero)(?=\s+(?:de|al|a)\b)", "1", t)
    return t


def interpretar(texto, hoy):
    """Devuelve ids, cantidad y un filtro de fechas (pasa) con su etiqueta.
    `hoy` es la última fecha de la base, no la del sistema.
    Soporta: "18 de agosto", "del 10 al 20 de agosto", "agosto", "agosto de 2026",
    "hoy", "ayer", "la semana", "el último mes", "3 órdenes", "la orden 55".
    """
    t = _numeros_en_palabras(_sin_acentos(texto))
    anio = hoy.year
    mes_pat = "|".join(MESES)
    sufijo = r"(?:\s+(?:de|del)\s+(\d{4}))?"

    # --- ids concretos: "la orden 55", "órdenes 3, 5 y 8" (no confundir con "orden 18 de agosto")
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

    m = re.search(rf"\b(?:del?\s+)?(\d{{1,2}})\s+al?\s+(\d{{1,2}})\s+de\s+({mes_pat})\b{sufijo}", t)
    m2 = None if m else re.search(rf"\b({mes_pat})\s+del?\s+(\d{{1,2}})\s+al?\s+(\d{{1,2}})\b(?:\s+(?:de|del)\s+(\d{{4}}))?", t)
    if m:                                               # "del 10 al 20 de agosto"
        desde, hasta = fecha(m[1], m[3], m[4]), fecha(m[2], m[3], m[4])
        etiqueta = f"el periodo del {int(m[1])} al {int(m[2])} de {m[3]} de {m[4] or anio}"
    elif m2:                                            # "agosto de 1 al 10"
        desde, hasta = fecha(m2[2], m2[1], m2[4]), fecha(m2[3], m2[1], m2[4])
        etiqueta = f"el periodo del {int(m2[2])} al {int(m2[3])} de {m2[1]} de {m2[4] or anio}"
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
    return {"ids": ids, "cantidad": cantidad, "pasa": pasa, "etiqueta": etiqueta, "desde": desde, "hasta": hasta}

def _tarjeta(h, decision):
    """Datos de una orden + decisión del agente, tal como los dibuja la interfaz."""
    dd = _decision_a_dict(decision)
    return {
        "orden_id": h["orden_id"],
        "fecha": h["fecha"],
        "cliente": h["cliente"],
        "banderas": [ETIQUETAS.get(b, b) for b in h["banderas"]],
        "margen_pct": h["margen_pct"],
        "accion": dd.get("accion", "revisar"),
        "causa": dd.get("causa", ""),
        "evidencia": dd.get("evidencia", ""),
        "perdida_mxn": _num(dd.get("perdida_estimada_mxn")),
    }


def _clasificar(ordenes):
    """(nivel, resumen) para el robot: 'grave' si hay un bloqueo o una pérdida >= UMBRAL_GRAVE_MXN,
    'menor' si hay otras alertas o una orden sin decisión, o (None, '') si no hay nada que avisar."""
    alertas = [o for o in ordenes if o["accion"] in ("alertar", "bloquear", "ajustar")]
    sin_decision = [o for o in ordenes if o["accion"] == "revisar"]
    if alertas:
        alertas.sort(key=lambda o: o["perdida_mxn"], reverse=True)
        top = alertas[0]
        grave = any(o["accion"] == "bloquear" or o["perdida_mxn"] >= UMBRAL_GRAVE_MXN for o in alertas)
        resumen = f"Orden {top['orden_id']}: {top['causa']}"[:170]
        if len(alertas) > 1:
            resumen += f" (+{len(alertas) - 1} más)"
        return ("grave" if grave else "menor"), resumen
    if sin_decision:
        return "menor", f"No pude decidir sobre la orden {sin_decision[0]['orden_id']}; requiere revisión humana."
    return None, ""


def _tarjeta_voz(id_orden, pedido, salida):
    """Lo que muestra la ventana flotante cuando termina una orden dada por voz: la misma información
    que el chat (tarjetas por orden), más el nivel de gravedad para el color del robot."""
    r = salida.get("resultado")
    nivel = _clasificar(r["ordenes"])[0] if r else None
    return {"id": f"{INICIO}-{id_orden}", "pedido": pedido, "mensaje": salida.get("mensaje", ""),
            "resultado": r, "nivel": nivel, "cancelado": bool(salida.get("cancelado"))}


def _guardar_voz(item):
    historial_voz.append(item)
    del historial_voz[:-MAX_HISTORIAL_VOZ]


def _detalle_sin_bandera(con, orden_id, ids):
    """Respuesta a "busca la orden 3" cuando esa orden está bien (o no se pudo revisar): en vez de decir
    solo "no tiene banderas", cuenta cómo está."""
    if orden_id not in ids:
        return f"La orden {orden_id} no existe (hay órdenes del {min(ids)} al {max(ids)})."
    h = revisar_orden(con, orden_id)
    if h.get("datos_faltantes"):
        return (f"La orden #{orden_id} ({h['cliente']}, {h['fecha']}) no se pudo revisar por completo: "
                f"falta {', '.join(h['datos_faltantes'])}.")
    return (f"La orden #{orden_id} ({h['cliente']}, {h['fecha']}) no tiene banderas: margen {_pct(h['margen_pct'])}, "
            f"facturado ${sum(h['facturas']):,.2f} de ${h['monto_esperado']:,.2f} esperados, "
            f"stock disponible {h['stock_disponible']}.")


def _armar(base, ordenes, gasto, cancelado=False, guardadas=False, nota=""):
    """Resumen + tarjetas + mensaje de texto. `guardadas`: decisiones leídas de la bitácora, sin llamar a Grok."""
    alertas = [o for o in ordenes if o["accion"] == "alertar"]
    ignoradas = [o for o in ordenes if o["accion"] == "ignorar"]
    perdida_total = round(sum(o["perdida_mxn"] for o in alertas), 2)
    cuentas = (f"{_plural(len(alertas), 'requiere alerta', 'requieren alerta')} "
               f"y {_plural(len(ignoradas), 'se puede ignorar', 'se pueden ignorar')}.")
    if guardadas:
        resumen = (f"{base} Ya estaban investigadas: muestro "
                   f"{_plural(len(ordenes), 'decisión guardada', 'decisiones guardadas')} "
                   f"(sin gastar créditos): {cuentas}")
    else:
        resumen = f"{base} Investigué {_plural(len(ordenes), 'orden', 'órdenes')}: {cuentas}"
    if cancelado:
        resumen += " Consulta detenida: las órdenes restantes no se investigaron."
    resumen += nota
    if gasto and not guardadas:              # lo que costó de verdad esta consulta (costo reportado por Grok)
        resumen += f" Costo de esta consulta: US${gasto:.4f}."

    resultado = {"resumen": resumen, "ordenes": ordenes,
                 "perdida_total_mxn": perdida_total, "gasto_usd": gasto}
    lineas = [resumen, ""]
    for o in ordenes:
        lineas.append(f"Orden {o['orden_id']} ({o['cliente']}) - {o['accion'].upper()}: {o['causa']}")
    lineas.append(f"\nPérdida estimada en alertas: ${perdida_total:,.2f} MXN")
    return {"mensaje": "\n".join(lineas), "resultado": resultado, "cancelado": cancelado}


def _corrida_en_curso():
    """Mientras la corrida larga trabaja, el chat no gasta la llave: se mezclaría con el consumo que se mide.
    AUDIBOT_PERMITIR_CHAT=1 lo desactiva."""
    if os.environ.get("AUDIBOT_PERMITIR_CHAT"):
        return False
    return estado_corrida("corrida")["estado"] in ("en curso", "en pausa")


def ejecutar_auditoria_uci(texto):
    """Datos reales (UCI Online Retail II): audita líneas de la cola con el agente de agent/uci.py.
    Entiende periodos ("agosto de 2010", "del 10 al 20 de agosto"), facturas ("factura C531179") y cantidades."""
    gasto_inicial = llm.gasto_total
    if not os.path.exists(RUTA_UCI):
        return {"mensaje": "No encuentro los datos reales (data/uci.db). Cárgalos con db/cargar_uci.py.",
                "resultado": None}
    c = sqlite3.connect(RUTA_UCI)
    try:
        ultimo = c.execute("SELECT MAX(fecha) FROM lineas").fetchone()[0]
    finally:
        c.close()
    pedido = interpretar(texto, date.fromisoformat(str(ultimo)[:10]))
    factura = chat_uci.factura_pedida(texto)

    def avance(total=None, hechas=None, mensaje=None, enviado=None):
        if total is not None:
            progreso["total"] = total
        if hechas is not None:
            progreso["hechas"] = hechas
        if enviado is not None:
            progreso["enviado"] = enviado
        if mensaje:
            estado["mensaje"] = mensaje

    r = chat_uci.ejecutar_uci(texto, cancelar_evento, RUTA_UCI, DIR_CHAT_UCI, LIMITE_POR_DEFECTO,
                              LIMITE_MAXIMO, ESPERA_ANTES_DE_ENVIAR, avance,
                              desde=pedido["desde"], hasta=pedido["hasta"], factura=factura)
    donde = (f" de la factura {factura}" if factura
             else f" en {pedido['etiqueta']}" if pedido["etiqueta"] else "")
    base = (f"Datos reales (UCI Online Retail II): la cola tiene {r['cola']} líneas"
            + (f"; hay {r['en_filtro']}{donde}" if donde else "") + f"; quedan {r['pendientes']} por revisar.")
    ordenes = r["ordenes"]
    if not ordenes:
        if r["tope"]:
            return {"mensaje": f"Tope de gasto alcanzado (US${llm.MAX_BUDGET_USD:.2f}): no hice más llamadas a Grok.",
                    "resultado": None}
        if r["fatal"]:
            return {"mensaje": f"No pude continuar: {r['fatal']}", "resultado": None}
        if r["cancelado"]:
            return {"mensaje": "Consulta detenida. No se envió nada a Grok.", "resultado": None, "cancelado": True}
        if r["en_filtro"] == 0:
            return {"mensaje": f"No hay líneas en la cola de auditoría{donde}. La cola es una muestra de "
                               f"{r['cola']} líneas de los datos reales, que llegan hasta {str(ultimo)[:10]}.",
                    "resultado": None}
        if r["guardadas"]:                                  # ya estaban revisadas: se muestran sin gastar
            return _armar(base, r["guardadas"], 0, guardadas=True)
        return {"mensaje": f"No quedan líneas por revisar{donde} ({r['hechas_antes']} ya revisadas desde el chat).",
                "resultado": None}
    if r["pidio"] and r["pidio"] > r["limite"]:
        base += f" (Máximo {r['limite']} por consulta, para cuidar los créditos.)"
    nivel, resumen_alerta = _clasificar(ordenes)
    estado["alerta"] = {"nivel": nivel, "resumen": resumen_alerta} if nivel else None
    nota = " Los montos de UCI están en libras, así que no estimo pérdidas en MXN."
    if r["tope"]:
        nota += " Tope de gasto alcanzado: las líneas restantes no se investigaron."
    if r["fatal"]:
        nota += f" Me detuve: {r['fatal']}"
    return _armar(base, ordenes, round(llm.gasto_total - gasto_inicial, 4), r["cancelado"], nota=nota)


def ejecutar_auditoria(texto):
    """Misma lógica del script de consola. Números en el texto = ids de órdenes.

    Devuelve {"mensaje": texto_plano, "resultado": datos_estructurados | None, "cancelado": bool}.
    """
    if es_pedido_corrida(texto):             # solo lectura: funciona incluso durante la corrida larga
        return {"mensaje": texto_corrida(texto), "resultado": None}
    if _corrida_en_curso():
        return {"mensaje": "Hay una corrida larga en curso. Para no mezclar su consumo de la llave, el chat no "
                           "investiga ahora. Puedes ver el avance con «Ver corrida».", "resultado": None}
    explicito = chat_uci.es_pedido_sintetico(texto)
    if os.path.exists(RUTA_UCI) and not explicito:         # los datos reales (UCI) son la base por defecto
        return ejecutar_auditoria_uci(texto)
    aviso_simulado = "" if explicito else (" (Uso la base simulada porque no encuentro los datos reales "
                                           "en data/uci.db.)")
    gasto_inicial = llm.gasto_total   # para reportar solo el gasto de ESTA consulta

    con = sqlite3.connect(RUTA_BD)
    try:
        hoy = date.fromisoformat(con.execute("SELECT MAX(fecha) FROM ordenes").fetchone()[0])
        pedido = interpretar(texto, hoy)
        print("TEXTO RECIBIDO:", repr(texto))
        print("CANTIDAD INTERPRETADA:", pedido["cantidad"])
        pedidas = pedido["ids"]
        ids = [r[0] for r in con.execute("SELECT id FROM ordenes ORDER BY id")]
        con_bandera = [h for h in (revisar_orden(con, i) for i in ids) if _hay_hallazgo(h)]
        base = (f"Revisé {_plural(len(ids), 'orden', 'órdenes')} con las reglas y "
                f"{len(con_bandera)} tienen banderas.") + aviso_simulado

        fmin, fmax = con.execute("SELECT MIN(fecha), MAX(fecha) FROM ordenes").fetchone()
        if not pedidas and pedido["desde"]:
            if pedido["hasta"] < fmin or pedido["desde"] > fmax:
                return {"mensaje": f"No hay datos para {pedido['etiqueta']}: la base va del {fmin} al {fmax}.",
                        "resultado": None}
            if pedido["desde"] < fmin or pedido["hasta"] > fmax:
                base += f" (La base solo tiene datos del {fmin} al {fmax}.)"

        if pedidas:
            # Pidió órdenes concretas ("la orden 55"): se investigan esas.
            seleccion = [h for h in con_bandera if h["orden_id"] in pedidas]
            ids_set = set(ids)
            notas = [_detalle_sin_bandera(con, i, ids_set)
                     for i in sorted(pedidas - {h["orden_id"] for h in seleccion})]
            if not seleccion:
                return {"mensaje": f"{base}\n" + "\n".join(notas), "resultado": None}
            if notas:
                base += " " + " ".join(notas)
        else:
            en_periodo = [h for h in con_bandera if pedido["pasa"](h["fecha"])]
            donde = f" en {pedido['etiqueta']}" if pedido["etiqueta"] else ""
            if pedido["etiqueta"]:
                base += f" En {pedido['etiqueta']}: {len(en_periodo)}."

            guardadas = {}
            for r in _leer_log(RUTA_LOG):
                if r.get("decision") is not None or not r.get("error"):   # las que fallaron por error se reintentan
                    guardadas[r["orden"]] = r.get("decision")             # queda la decisión más reciente de cada orden
            pendientes = [h for h in en_periodo if h["orden_id"] not in guardadas]
            ya_hechas = len(en_periodo) - len(pendientes)
            if not pendientes:
                if not en_periodo:
                    return {"mensaje": f"{base}\nNo hay órdenes con banderas{donde}.", "resultado": None}
                # Todas se investigaron antes: se muestran sus decisiones guardadas (gratis y al instante).
                mostrar = en_periodo[:MAX_GUARDADAS]
                if len(en_periodo) > MAX_GUARDADAS:
                    base += f" (Muestro {MAX_GUARDADAS} de {len(en_periodo)}.)"
                return _armar(base, [_tarjeta(h, guardadas[h["orden_id"]]) for h in mostrar], 0, guardadas=True)
            if ya_hechas:
                base += f" ({_plural(ya_hechas, 'ya estaba investigada', 'ya estaban investigadas')} antes.)"

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
        tope = False
        fatal = None
        progreso["total"] = len(seleccion)
        estado["mensaje"] = f"Investigando {_plural(len(seleccion), 'orden', 'órdenes')} con Grok…"

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
            err = err_usuario = None
            try:
                d = auditar_orden(con, h, cancelar_evento)
            except Cancelado:
                cancelado = True
                break
            except ErrorLLM as e:
                codigo = e.info["codigo"]
                if codigo == "TOPE_GASTO":
                    tope = True
                    break
                if codigo == "LLAVE_INVALIDA":     # con una llave mala fallarían todas: se para de una vez
                    fatal = e.info["mensaje"]
                    break
                d, err = None, f"{codigo}: {e.info['mensaje']}"     # el código queda en la bitácora
                err_usuario = e.info["mensaje"]                      # la pantalla muestra el texto claro
            except Exception as e:  # noqa: BLE001 - una orden que falla no tumba toda la consulta
                if "Tope de gasto" in str(e):
                    tope = True
                    break
                d, err = None, f"{type(e).__name__}: {e}"[:200]
                err_usuario = "Ocurrió un error inesperado."

            # Esa orden ya se pagó, así que se guarda aunque hayan cancelado durante la llamada.
            registro = {"orden": h["orden_id"], "banderas": h["banderas"], "decision": d}
            if err:
                registro["error"] = err
            with open(RUTA_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(registro, ensure_ascii=False, default=str) + "\n")
            progreso["hechas"] += 1

            tarjeta = _tarjeta(h, d)
            if err:
                tarjeta["causa"] = f"No pude analizar esta orden: {err_usuario} Requiere revisión humana."
            ordenes.append(tarjeta)

            if cancelar_evento.is_set():
                cancelado = True
                break

        if not ordenes:
            if tope:
                return {"mensaje": f"Tope de gasto alcanzado (US${llm.MAX_BUDGET_USD:.2f}): no hice más llamadas a Grok.",
                        "resultado": None}
            if fatal:
                return {"mensaje": f"No pude continuar: {fatal}", "resultado": None}
            return {"mensaje": "Consulta detenida antes de terminar ninguna orden.",
                    "resultado": None, "cancelado": True}

        # El robot avisa según la gravedad de lo que se acaba de investigar.
        nivel, resumen_alerta = _clasificar(ordenes)
        estado["alerta"] = {"nivel": nivel, "resumen": resumen_alerta} if nivel else None

        nota = ""
        if tope:
            nota += " Tope de gasto alcanzado: las órdenes restantes no se investigaron."
        if fatal:
            nota += f" Me detuve: {fatal}"
        return _armar(base, ordenes, round(llm.gasto_total - gasto_inicial, 4), cancelado, nota=nota)
    finally:
        con.close()


_cache_voz = {}


def sintetizar(texto):
    """Audio MP3 de la voz de AudiBot con edge-tts. Lanza ImportError si no está instalado
    (la interfaz entonces usa la voz del navegador)."""
    if texto in _cache_voz:
        return _cache_voz[texto]
    import edge_tts   # aquí adentro: si no está instalado, el resto del servidor sigue funcionando

    async def pedir():
        audio = b""
        async for trozo in edge_tts.Communicate(texto, VOZ_TTS, rate=VOZ_RITMO, pitch=VOZ_TONO).stream():
            if trozo["type"] == "audio":
                audio += trozo["data"]
        return audio

    audio = asyncio.run(pedir())
    if audio:
        _cache_voz[texto] = audio
    return audio



class Handler(BaseHTTPRequestHandler):
    def _json(self, codigo, datos):
        cuerpo = json.dumps(
            datos, ensure_ascii=False, default=str
        ).encode("utf-8")

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
            return self._json(200, _estado_publico())

        if ruta == "/api/corrida":
            nombre = parse_qs(urlparse(self.path).query).get("dir", ["corrida"])[0]
            return self._json(200, estado_corrida(nombre))

        if ruta == "/api/voz/historial":
            return self._json(200, {"items": list(historial_voz)})

        # Voz de AudiBot: exclusivamente Edge TTS.
        if ruta == "/api/tts":
            texto = parse_qs(
                urlparse(self.path).query
            ).get("texto", [""])[0].strip()[:200]

            if not texto:
                return self._json(
                    400, {"error": "No se recibió texto para sintetizar."}
                )

            try:
                audio = sintetizar(texto)
            except Exception as e:
                print(f"[TTS] Error de Edge TTS: {type(e).__name__}: {e}")
                traceback.print_exc()
                return self._json(503, {"error": "Edge TTS no está disponible."})

            if not audio:
                return self._json(
                    503, {"error": "Edge TTS no generó audio."}
                )

            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(audio)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(audio)
            return

        if ruta == "/":
            ruta = "/index.html"

        archivo = os.path.normpath(
            os.path.join(ESTATICOS, ruta.lstrip("/"))
        )
        relativa = os.path.relpath(
            archivo, ESTATICOS
        ).replace("\\", "/")
        ext = os.path.splitext(archivo)[1]

        permitido = (
            relativa in ("index.html", "overlay.html")
            or relativa.startswith(("css/", "js/", "assets/"))
            or (
                relativa.startswith("robot/")
                and ext == ".glb"
            )
        ) and ext in TIPOS

        try:
            dentro = (
                os.path.commonpath([archivo, ESTATICOS])
                == ESTATICOS
            )
        except ValueError:
            dentro = False

        if not dentro or not permitido or not os.path.isfile(archivo):
            print(
                f"  404: buscaba {archivo} "
                f"(dentro={dentro}, permitido={permitido}, "
                f"existe={os.path.isfile(archivo)})"
            )
            self.send_response(404)
            self.end_headers()
            return

        with open(archivo, "rb") as f:
            cuerpo = f.read()

        self.send_response(200)
        self.send_header(
            "Content-Type",
            TIPOS.get(ext, "application/octet-stream")
        )
        self.send_header(
            "Content-Length", str(len(cuerpo))
        )
        self.send_header(
            "Cache-Control",
            "max-age=3600" if ext == ".glb" else "no-store"
        )
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_POST(self):
        ruta = urlparse(self.path).path

        # Solo aceptar peticiones de nuestra propia interfaz.
        origen = self.headers.get("Origin")
        if origen and origen not in (
            f"http://localhost:{PUERTO}",
            f"http://127.0.0.1:{PUERTO}"
        ):
            return self._json(
                403, {"error": "Origen no permitido."}
            )

        datos = self._leer_json()

        if ruta == "/api/ack":
            return self._json(200, {"ok": True})

        if ruta == "/api/alerta/confirmar":
            estado["alerta"] = None
            estado["resultado_voz"] = None
            return self._json(200, {"ok": True})

        if ruta == "/api/detener":
            if estado["estado"] not in (
                "trabajando", "deteniendo"
            ):
                return self._json(
                    409,
                    {"error": "No hay ninguna auditoría en curso."}
                )

            cancelar_evento.set()
            estado["estado"] = "deteniendo"
            return self._json(
                200,
                {
                    "ok": True,
                    "mensaje": "Solicitud de detención recibida."
                }
            )

        if ruta == "/api/orden":
            texto = str(datos.get("texto", "")).strip()

            if not texto:
                return self._json(
                    400, {"error": "La orden está vacía."}
                )

            if not candado.acquire(blocking=False):
                if estado["estado"] == "deteniendo":
                    return self._json(
                        409,
                        {
                            "error": (
                                "Terminando la consulta anterior; "
                                "intenta de nuevo en unos segundos."
                            )
                        }
                    )

                return self._json(
                    409, {"error": "Ya hay una orden en curso."}
                )

            estado.update(
                estado="trabajando",
                alerta=None,
                mensaje="Orden recibida. Preparando la auditoría…"
            )

            es_async = bool(datos.get("async"))

            if es_async:
                estado["resultado_voz"] = None

            contador["n"] += 1
            id_orden = contador["n"]

            progreso.update(
                hechas=0, total=0, enviado=False
            )

            if es_async:
                estado["pedido_voz"] = {
                    "id": f"{INICIO}-{id_orden}",
                    "texto": texto
                }

            salida = {}
            hecho = threading.Event()

            def trabajo():
                try:
                    salida["ok"] = ejecutar_auditoria(texto)

                    if es_async:
                        estado["resultado_voz"] = _tarjeta_voz(
                            id_orden, texto, salida["ok"]
                        )
                        _guardar_voz(estado["resultado_voz"])

                except Exception:
                    traceback.print_exc()          # el detalle técnico queda en la consola del servidor
                    salida["error"] = True

                    if es_async:
                        estado["resultado_voz"] = {
                            "id": f"{INICIO}-{id_orden}",
                            "pedido": texto,
                            "resultado": None,
                            "mensaje": (
                                "Hubo un error y no pude terminar "
                                "la auditoría."
                            ),
                            "nivel": "menor",
                            "cancelado": False
                        }
                        _guardar_voz(estado["resultado_voz"])

                finally:
                    cancelar_evento.clear()
                    estado["pedido_voz"] = None
                    estado["estado"] = "reposo"
                    estado["mensaje"] = (
                        "Hay hallazgos que revisar."
                        if estado.get("alerta")
                        else "Listo para recibir una orden."
                    )
                    candado.release()
                    hecho.set()

            threading.Thread(
                target=trabajo, daemon=True
            ).start()

            # Las órdenes por voz no esperan a que termine la auditoría.
            if es_async:
                return self._json(
                    200, {"ok": True, "id": id_orden}
                )

            while not hecho.wait(0.2):
                if cancelar_evento.is_set():
                    n = progreso["hechas"]

                    if n:
                        msg = (
                            f"Consulta detenida. Alcanzó a investigar "
                            f"{_plural(n, 'orden', 'órdenes')}; "
                            "quedaron guardadas en la bitácora."
                        )
                    elif progreso["enviado"]:
                        msg = (
                            "Consulta detenida. Una llamada a Grok "
                            "ya había salido: su respuesta se descarta "
                            "y se cobra igual."
                        )
                    else:
                        msg = (
                            "Consulta detenida. "
                            "No se envió nada a Grok."
                        )

                    return self._json(
                        200,
                        {
                            "id": id_orden,
                            "mensaje": msg,
                            "resultado": None,
                            "cancelado": True
                        }
                    )

            if "error" in salida:
                return self._json(
                    500,
                    {"error": "No pude completar la auditoría. Vuelve a intentarlo en un momento."}
                )

            return self._json(
                200, {"id": id_orden, **salida["ok"]}
            )

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
    if not os.environ.get("AUDIBOT_NO_BROWSER"):   # la app de escritorio ya trae su propia ventana
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    ThreadingHTTPServer(("127.0.0.1", PUERTO), Handler).serve_forever()