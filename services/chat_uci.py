"""Modo "datos reales (UCI)" del chat: audita líneas de la cola de UCI con agent/uci.py.

Usa el mismo formato de bitácora por orden que worker.py, pero en su propia carpeta (logs/chat_uci/),
para que el chat y la corrida larga no se pisen. Devuelve tarjetas con la forma que ya dibuja la interfaz.
"""
import json
import os
import re
import sqlite3
import time
import unicodedata
from datetime import datetime

from services import llm
from services.llm import ErrorLLM

CAMPOS = ("id", "factura", "producto", "descripcion", "cantidad", "fecha", "precio", "cliente", "pais", "cancelacion")
ETIQUETAS_REGLA = {
    "precio_cero": "Precio cero",
    "compra_repetida_mismo_dia": "Compra repetida el mismo día",
    "cancelacion_sin_compra": "Cancelación sin compra",
    "cantidad_negativa": "Cantidad negativa",
    "linea_repetida_en_factura": "Línea repetida en factura",
    "revision_de_rutina": "Revisión de rutina",
}
NUMEROS = {"un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
           "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10}


def _sin_acentos(t):
    return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")


def es_pedido_sintetico(texto):
    """Solo si lo piden a propósito se usa la base simulada ("con la base simulada", "demo")."""
    return bool(re.search(r"\b(sintetic[oa]s?|simulad[oa]s?|demo)\b", _sin_acentos(texto)))


def factura_pedida(texto):
    """"revisa la factura 531179" / "factura C531179" -> "531179" / "C531179" (o None)."""
    m = re.search(r"\b(?:factura|orden|invoice)\s*(?:numero|num\.?|no\.?|#)?\s*#?\s*(c?\d{5,7})\b",
                  _sin_acentos(texto))
    return m.group(1).upper() if m else None


def es_pedido_uci(texto):
    """¿El usuario pide trabajar con los datos reales? ("revisa 5 líneas de UCI", "datos reales")."""
    return bool(re.search(r"\b(uci|retail|datos reales|lineas reales|ordenes reales)\b", _sin_acentos(texto)))


def cantidad_pedida(texto, defecto, maximo):
    """(cantidad aplicada, cantidad pedida o None)."""
    m = re.search(r"\b(\d+|" + "|".join(NUMEROS) + r")\s+(?:lineas?|ordenes?|casos?|compras?|transacciones?)\b",
                  _sin_acentos(texto))
    pidio = None
    if m:
        pidio = int(m.group(1)) if m.group(1).isdigit() else NUMEROS[m.group(1)]
    return max(1, min(pidio or defecto, maximo)), pidio


def _cargar_auditor():
    from agent.uci import auditar_linea           # import tardío: el chat sin UCI sigue funcionando
    return auditar_linea


def _hechas(ruta):
    hechas = set()
    if os.path.exists(ruta):
        for linea in open(ruta, encoding="utf-8"):
            try:
                o = json.loads(linea)
            except ValueError:
                continue
            if o.get("resultado") in ("ok", "sin_decision") and "pos" in o:
                hechas.add(o["pos"])
    return hechas


def _tarjeta(fila, regla, decision, err_usuario, nota):
    d = decision or {}
    cliente = fila.get("cliente")
    causa = d.get("causa")
    if not causa:
        causa = (f"No pude analizar esta línea: {err_usuario} Requiere revisión humana." if err_usuario
                 else (nota or "El agente no llegó a una decisión; requiere revisión humana."))
    return {"orden_id": fila["id"], "fecha": str(fila.get("fecha"))[:10],
            "cliente": f"Cliente {cliente}" if cliente not in (None, "") else "Sin cliente",
            "banderas": [ETIQUETAS_REGLA.get(regla, regla)], "margen_pct": None,
            "accion": d.get("accion") or "revisar", "causa": causa,
            "evidencia": d.get("evidencia", ""), "perdida_mxn": 0.0}


def _ultimas(ruta):
    d = {}
    if os.path.exists(ruta):
        for linea in open(ruta, encoding="utf-8"):
            try:
                o = json.loads(linea)
            except ValueError:
                continue
            if "pos" in o:
                d[o["pos"]] = o
    return d


def ejecutar_uci(texto, cancelar, db, carpeta, limite_defecto=3, limite_max=10, espera=0.0, avance=None,
                 desde=None, hasta=None, factura=None):
    """desde/hasta (AAAA-MM-DD, inclusivos) y factura filtran la cola; sin filtros se reparte entre reglas."""
    avance = avance or (lambda **k: None)
    os.makedirs(carpeta, exist_ok=True)
    ruta = os.path.join(carpeta, "ordenes.jsonl")
    con = sqlite3.connect(db)
    try:
        try:
            con.execute("CREATE INDEX IF NOT EXISTS ix_lineas_factura ON lineas(factura)")
        except sqlite3.Error:
            pass
        cola = con.execute("SELECT pos, linea_id, regla, es_control FROM cola ORDER BY pos").fetchall()
        sql = "SELECT c.pos, c.linea_id, c.regla, c.es_control FROM cola c JOIN lineas l ON l.id = c.linea_id"
        cond, par = [], []
        if desde:
            cond.append("l.fecha >= ?"); par.append(desde)
        if hasta:
            cond.append("l.fecha < date(?, '+1 day')"); par.append(hasta)
        if factura:
            cond.append("l.factura = ?"); par.append(factura)
        candidatas = (con.execute(sql + " WHERE " + " AND ".join(cond) + " ORDER BY c.pos", par).fetchall()
                      if cond else cola)
        hechas = _hechas(ruta)
        pendientes = [c for c in candidatas if c[0] not in hechas]
        limite, pidio = cantidad_pedida(texto, limite_defecto, limite_max)
        out = {"ordenes": [], "guardadas": [], "tope": False, "fatal": None, "cancelado": False,
               "cola": len(cola), "en_filtro": len(candidatas), "hechas_antes": len(hechas),
               "pendientes": len(pendientes), "limite": limite, "pidio": pidio}
        if not pendientes:
            if candidatas:                                  # todo lo pedido ya estaba revisado: se muestra gratis
                ult = _ultimas(ruta)
                for pos, lid, regla, es_control in candidatas[:10]:
                    rec = ult.get(pos)
                    row = con.execute(f"SELECT {','.join(CAMPOS)} FROM lineas WHERE id=?", (lid,)).fetchone()
                    if rec and row:
                        bandera = "revision_de_rutina" if es_control else regla
                        out["guardadas"].append(_tarjeta(dict(zip(CAMPOS, row)), bandera,
                                                         rec.get("decision"), None, rec.get("nota")))
            return out
        por = {}
        for c in pendientes:                              # reparte entre reglas, como el chat con la base simulada
            por.setdefault(c[2], []).append(c)
        sel = []
        while len(sel) < limite and any(por.values()):
            for k in list(por):
                if por[k] and len(sel) < limite:
                    sel.append(por[k].pop(0))
        avance(total=len(sel), hechas=0, mensaje=f"Investigando {len(sel)} líneas reales con Grok…")
        if cancelar.wait(espera):                         # ventana para arrepentirse: aún no se envió nada
            out["cancelado"] = True
            return out
        auditar = _cargar_auditor()
        for pos, lid, regla, es_control in sel:
            if cancelar.is_set():
                out["cancelado"] = True
                break
            row = con.execute(f"SELECT {','.join(CAMPOS)} FROM lineas WHERE id=?", (lid,)).fetchone()
            if row is None:
                continue
            fila = dict(zip(CAMPOS, row))
            bandera = "revision_de_rutina" if es_control else regla     # Grok no sabe cuáles son de control
            oid, g0, t0 = f"uci-{lid}", llm.gasto_total, time.time()
            rec = {"hora": datetime.now().isoformat(), "pos": pos, "linea_id": lid, "orden_id": oid,
                   "regla": regla, "es_control": es_control, "origen": "chat"}
            decision = nota = err_usuario = None
            avance(enviado=True)
            try:
                decision, nota = auditar(con, fila, bandera, orden_id=oid)
                rec.update(resultado="ok", decision=decision) if decision else rec.update(resultado="sin_decision", nota=nota)
            except ErrorLLM as e:
                codigo = e.info["codigo"]
                if codigo == "TOPE_GASTO":
                    out["tope"] = True
                    break
                if codigo == "LLAVE_INVALIDA":            # con una llave mala fallarían todas
                    out["fatal"] = e.info["mensaje"]
                    break
                rec.update(resultado="error", codigo=codigo, error=e.info["mensaje"])
                err_usuario = e.info["mensaje"]
            except Exception as e:  # noqa: BLE001 - una línea que falla no tumba la consulta
                rec.update(resultado="error", codigo="EXCEPCION", error=f"{type(e).__name__}: {e}"[:300])
                err_usuario = "Ocurrió un error inesperado."
            rec.update(costo_usd=round(llm.gasto_total - g0, 6), duracion_s=round(time.time() - t0, 2))
            with open(ruta, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            out["ordenes"].append(_tarjeta(fila, bandera, decision, err_usuario, nota))
            avance(hechas=len(out["ordenes"]))
        return out
    finally:
        con.close()