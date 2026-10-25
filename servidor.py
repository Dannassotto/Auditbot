"""Servidor local para la interfaz de AudiBot.

Sirve el HTML/CSS/JS y expone los endpoints que usa app.js:
  GET  /api/estado   -> {"estado": "reposo" | "trabajando" | "deteniendo", "alerta": {...} | null,
                          "mensaje", "hechas", "total", "llamadas", "gasto_acumulado", ...}
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
import json
import os
import re
import traceback
import sqlite3
import threading
import unicodedata
import webbrowser
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from openai import audio

RAIZ = os.path.dirname(os.path.abspath(__file__))
os.chdir(RAIZ)  # las rutas data/auditbot.db y logs/ son relativas a la raíz

from engine.margins import revisar_orden  # noqa: E402
from agent.loop import auditar_orden, Cancelado  # noqa: E402
from services import llm  # noqa: E402

PUERTO = int(os.environ.get("PORT", "5000"))
LIMITE_POR_DEFECTO = 3     # cuántas órdenes investiga si no dices una cantidad
LIMITE_MAXIMO = 10          # tope por consulta, para cuidar los créditos
MAX_GUARDADAS = 10         # cuántas decisiones ya guardadas se muestran de una vez (no cuestan créditos)
UMBRAL_GRAVE_MXN = 500    # a partir de esta pérdida el robot marca "alerta grave"
ESPERA_ANTES_DE_ENVIAR = 2.0   # segundos para arrepentirse: si pulsas Detener aquí, no se envía NADA a Grok (0 = sin espera)
RUTA_BD = "data/auditbot.db"
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

INICIO = int(__import__('time').time())   # distingue las órdenes de esta ejecución de las de una anterior
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


def _estado_publico():
    llamadas, gasto = _gasto_acumulado()
    e = dict(estado)
    e.update(voz_ultimo=historial_voz[-1]["id"] if historial_voz else None,
             hechas=progreso["hechas"], total=progreso["total"], llamadas=llamadas,
             gasto_acumulado=round(gasto, 4), gasto_sesion=round(llm.gasto_total, 4))
    return e


def _plural(n, singular, plural):
    return f"{n} {singular if n == 1 else plural}"


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
    """Respuesta a "busca la orden 3" cuando esa orden está bien: en vez de decir solo "no tiene banderas",
    cuenta cómo está."""
    if orden_id not in ids:
        return f"La orden {orden_id} no existe (hay órdenes del {min(ids)} al {max(ids)})."
    h = revisar_orden(con, orden_id)
    return (f"La orden #{orden_id} ({h['cliente']}, {h['fecha']}) no tiene banderas: margen {h['margen_pct']}%, "
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

    resultado = {"resumen": resumen, "ordenes": ordenes,
                 "perdida_total_mxn": perdida_total, "gasto_usd": gasto}
    lineas = [resumen, ""]
    for o in ordenes:
        lineas.append(f"Orden {o['orden_id']} ({o['cliente']}) - {o['accion'].upper()}: {o['causa']}")
    lineas.append(f"\nPérdida estimada en alertas: ${perdida_total:,.2f} MXN")
    return {"mensaje": "\n".join(lineas), "resultado": resultado, "cancelado": cancelado}


def ejecutar_auditoria(texto):
    """Misma lógica del script de consola. Números en el texto = ids de órdenes.

    Devuelve {"mensaje": texto_plano, "resultado": datos_estructurados | None, "cancelado": bool}.
    """
    gasto_inicial = llm.gasto_total   # para reportar solo el gasto de ESTA consulta

    con = sqlite3.connect(RUTA_BD)
    try:
        hoy = date.fromisoformat(con.execute("SELECT MAX(fecha) FROM ordenes").fetchone()[0])
        pedido = interpretar(texto, hoy)
        print("TEXTO RECIBIDO:", repr(texto))
        print("CANTIDAD INTERPRETADA:", pedido["cantidad"])
        pedidas = pedido["ids"]
        ids = [r[0] for r in con.execute("SELECT id FROM ordenes ORDER BY id")]
        con_bandera = [h for h in (revisar_orden(con, i) for i in ids) if h["banderas"]]
        base = (f"Revisé {_plural(len(ids), 'orden', 'órdenes')} con las reglas y "
                f"{len(con_bandera)} tienen banderas.")

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

                        print(f"[DEBUG] Cantidad solicitada: {pedido['cantidad']}")
        print(f"[DEBUG] Límite aplicado: {limite}")
        print(f"[DEBUG] Órdenes con banderas: {len(en_periodo)}")
        print(f"[DEBUG] Órdenes pendientes: {len(pendientes)}")
        print(f"[DEBUG] Órdenes seleccionadas: {len(seleccion)}")

        os.makedirs("logs", exist_ok=True)
        ordenes = []
        cancelado = False
        tope = False
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
            err = None
            try:
                d = auditar_orden(con, h, cancelar_evento)
            except Cancelado:
                cancelado = True
                break
            except Exception as e:  # noqa: BLE001 - una orden que falla no tumba toda la consulta
                if "Tope de gasto" in str(e):
                    tope = True
                    break
                d, err = None, f"{type(e).__name__}: {e}"[:200]

            # Esa orden ya se pagó, así que se guarda aunque hayan cancelado durante la llamada.
            registro = {"orden": h["orden_id"], "banderas": h["banderas"], "decision": d}
            if err:
                registro["error"] = err
            with open(RUTA_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(registro, ensure_ascii=False, default=str) + "\n")
            progreso["hechas"] += 1

            tarjeta = _tarjeta(h, d)
            if err:
                tarjeta["causa"] = f"No pude completar el análisis ({err}). Requiere revisión humana."
            ordenes.append(tarjeta)

            if cancelar_evento.is_set():
                cancelado = True
                break

        if not ordenes:
            if tope:
                return {"mensaje": f"Tope de gasto alcanzado (US${llm.MAX_BUDGET_USD:.2f}): no hice más llamadas a Grok.",
                        "resultado": None}
            return {"mensaje": "Consulta detenida antes de terminar ninguna orden.",
                    "resultado": None, "cancelado": True}

        # El robot avisa según la gravedad de lo que se acaba de investigar.
        nivel, resumen_alerta = _clasificar(ordenes)
        estado["alerta"] = {"nivel": nivel, "resumen": resumen_alerta} if nivel else None

        nota = " Tope de gasto alcanzado: las órdenes restantes no se investigaron." if tope else ""
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

                except Exception as e:
                    salida["error"] = str(e)

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
                    {
                        "error": (
                            f"Falló la auditoría: {salida['error']}"
                        )
                    }
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