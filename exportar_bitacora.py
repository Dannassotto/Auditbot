"""Exporta la bitácora de una corrida a UN csv legible: una fila por intento de cada tarea, con hora, entrada
(la línea real de UCI), resultado, éxito, costo, tokens, errores y si se recuperó.

Uso:  python exportar_bitacora.py logs\\corrida [data\\uci.db] [salida.csv]
Por defecto escribe bitacora.csv dentro de la carpeta de la corrida.
"""
import csv
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict

COLUMNAS = ["hora", "intento", "es_ultimo_intento", "pos", "linea_id", "orden_id", "regla", "es_control",
            "entrada_factura", "entrada_producto", "entrada_descripcion", "entrada_cantidad", "entrada_precio",
            "entrada_fecha", "entrada_cliente", "entrada_pais",
            "resultado", "exito", "accion", "causa", "evidencia", "costo_usd", "tokens", "llamadas_grok",
            "intentos_api_fallidos", "codigo_error", "error", "recuperada", "duracion_s", "nota"]


def leer(ruta):
    out = []
    if os.path.exists(ruta):
        for linea in open(ruta, encoding="utf-8-sig"):
            try:
                out.append(json.loads(linea))
            except ValueError:
                pass
    return out


def _entradas(db, ids):
    """linea_id -> campos de la línea real. Si no hay base, las columnas de entrada quedan vacías."""
    if not db or not os.path.exists(db) or not ids:
        return {}
    con = sqlite3.connect(db)
    out, ids = {}, sorted(ids)
    try:
        for i in range(0, len(ids), 500):
            lote = ids[i:i + 500]
            q = ("SELECT id, factura, producto, descripcion, cantidad, fecha, precio, cliente, pais FROM lineas "
                 f"WHERE id IN ({','.join('?' * len(lote))})")
            for r in con.execute(q, lote):
                out[r[0]] = r[1:]
    finally:
        con.close()
    return out


def construir(carpeta, db=None):
    ords = [o for o in leer(os.path.join(carpeta, "ordenes.jsonl")) if "pos" in o]
    llam = leer(os.path.join(carpeta, "llamadas.jsonl"))
    errs = leer(os.path.join(carpeta, "errores.jsonl"))
    tokens, llamadas = defaultdict(int), Counter()
    for x in llam:
        oid = x.get("orden_id")
        if oid:
            tokens[oid] += x.get("tokens") if isinstance(x.get("tokens"), (int, float)) else \
                (x.get("tokens_entrada") or 0) + (x.get("tokens_salida") or 0)
            llamadas[oid] += 1
    fallos = Counter(e.get("orden_id") for e in errs if e.get("orden_id"))
    con_id = any(x.get("orden_id") for x in llam)
    por_pos = defaultdict(list)
    for o in ords:
        por_pos[o["pos"]].append(o)
    entradas = _entradas(db, {o.get("linea_id") for o in ords if o.get("linea_id") is not None})
    filas = []
    for o in sorted(ords, key=lambda x: x.get("hora", "")):
        intentos = por_pos[o["pos"]]
        n = intentos.index(o) + 1
        ultimo = o is intentos[-1]
        d = o.get("decision") or {}
        e = entradas.get(o.get("linea_id"), ("",) * 8)
        oid = o.get("orden_id")
        recuperada = ("si" if ultimo and o.get("resultado") == "ok" and any(
            x.get("resultado") == "error" for x in intentos[:-1]) else "no")
        filas.append({
            "hora": o.get("hora", ""), "intento": n, "es_ultimo_intento": "si" if ultimo else "no",
            "pos": o["pos"], "linea_id": o.get("linea_id", ""), "orden_id": oid or "", "regla": o.get("regla", ""),
            "es_control": "si" if o.get("es_control") else "no",
            "entrada_factura": e[0], "entrada_producto": e[1], "entrada_descripcion": e[2], "entrada_cantidad": e[3],
            "entrada_precio": e[5], "entrada_fecha": e[4], "entrada_cliente": e[6],
            "entrada_pais": e[7],
            "resultado": o.get("resultado", ""), "exito": "si" if o.get("resultado") == "ok" else "no",
            "accion": d.get("accion", ""), "causa": d.get("causa", ""), "evidencia": d.get("evidencia", ""),
            "costo_usd": o.get("costo_usd", 0), "tokens": tokens.get(oid, "") if con_id else "",
            "llamadas_grok": llamadas.get(oid, "") if con_id else "",
            "intentos_api_fallidos": fallos.get(oid, 0) if oid else "",
            "codigo_error": o.get("codigo", ""), "error": o.get("error", ""), "recuperada": recuperada,
            "duracion_s": o.get("duracion_s", ""), "nota": o.get("nota", ""),
        })
    return filas, {"con_id_en_llamadas": con_id, "con_entradas": bool(entradas), "llamadas": len(llam)}


def main(argv=None):
    a = argv if argv is not None else sys.argv[1:]
    if not a:
        sys.exit("Uso: python exportar_bitacora.py logs\\corrida [data\\uci.db] [salida.csv]")
    carpeta = a[0]
    db = a[1] if len(a) > 1 else "data/uci.db"
    salida = a[2] if len(a) > 2 else os.path.join(carpeta, "bitacora.csv")
    filas, info = construir(carpeta, db)
    with open(salida, "w", newline="", encoding="utf-8-sig") as f:       # utf-8-sig: Excel abre bien los acentos
        w = csv.DictWriter(f, fieldnames=COLUMNAS)
        w.writeheader()
        w.writerows(filas)
    print(f"{len(filas)} filas escritas en {salida}")
    if not info["con_entradas"]:
        print("AVISO: no pude leer las líneas de entrada (revisa la ruta de uci.db); esas columnas quedaron vacías.")
    if not info["con_id_en_llamadas"] and info["llamadas"]:
        print("AVISO: las llamadas no traen orden_id, así que tokens por orden quedó vacío (el costo sí viene de la bitácora).")


if __name__ == "__main__":
    main()