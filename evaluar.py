"""Evaluación (S3): compara tus etiquetas humanas de casos.xlsx con las decisiones del agente
y calcula el costo por tarea resuelta, con todos los fallos a la vista.

Uso:
    python evaluar.py data/evaluacion/casos.xlsx logs/evaluacion
Escribe evaluacion.md y evaluacion_casos.csv dentro de la carpeta de la bitácora.

El archivo de casos necesita una fila de encabezados con una columna de posición ("pos") y otra con tu
etiqueta ("etiqueta": alertar, ignorar o dudoso). "motivo" y "regla" son opcionales.
"""
import csv
import json
import os
import sys
import unicodedata
from collections import Counter, defaultdict

NOMBRES = {
    "pos": {"pos", "posicion", "n_caso", "caso"},
    "etiqueta": {"etiqueta", "etiqueta_humana", "mi_etiqueta", "veredicto", "juicio", "humano", "label",
                 "decision_humana", "respuesta_correcta"},
    "motivo": {"motivo", "motivo_humano", "justificacion", "comentario", "razon"},
    "regla": {"regla", "tipo", "categoria"},
}
SINONIMOS = {"alertar": "alertar", "alerta": "alertar", "bloquear": "alertar", "ajustar": "alertar",
             "ignorar": "ignorar", "ignora": "ignorar", "normal": "ignorar",
             "dudoso": "dudoso", "duda": "dudoso", "no_se": "dudoso"}


def _norm(t):
    t = "".join(c for c in unicodedata.normalize("NFD", str(t).strip().lower()) if unicodedata.category(c) != "Mn")
    return t.replace(" ", "_")


def leer_tabla(ruta):
    """Filas como dicts con encabezados normalizados. Acepta .xlsx o .csv; ignora filas vacías."""
    if ruta.lower().endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook
        hoja = load_workbook(ruta, read_only=True, data_only=True).active
        filas = [list(r) for r in hoja.iter_rows(values_only=True)]
    else:
        with open(ruta, newline="", encoding="utf-8-sig") as f:
            filas = list(csv.reader(f))
    if not filas:
        return [], []
    cab = [_norm(c) if c is not None else "" for c in filas[0]]
    out = []
    for fila in filas[1:]:
        if any(c not in (None, "") for c in fila):
            out.append({cab[i]: fila[i] for i in range(min(len(cab), len(fila)))})
    return cab, out


def _columna(cab, clave):
    for nombre in sorted(NOMBRES[clave], key=lambda n: (n != clave, n)):   # primero el nombre exacto ('pos', 'etiqueta')
        if nombre in cab:
            return nombre
    if clave == "etiqueta":      # el encabezado que escribe preparar_evaluacion.py: "esperado (alertar/ignorar/dudoso)"
        return next((c for c in cab if c.startswith("esperado")), None)
    return None


def posiciones_casos(ruta):
    """Posiciones (pos) de la cola que forman el set de evaluación: para worker.py --casos."""
    cab, filas = leer_tabla(ruta)
    col = _columna(cab, "pos")
    if not col:
        raise ValueError(f"No encuentro la columna 'pos' en {ruta}. Encabezados: {cab}")
    return [int(float(f[col])) for f in filas if f.get(col) not in (None, "")]


def cargar_casos(ruta):
    cab, filas = leer_tabla(ruta)
    cpos, cet = _columna(cab, "pos"), _columna(cab, "etiqueta")
    if not cpos or not cet:
        raise ValueError(f"Necesito columnas 'pos' y 'etiqueta'. Encabezados encontrados: {cab}")
    cmot, creg = _columna(cab, "motivo"), _columna(cab, "regla")
    casos, sin_etiquetar = [], []
    for f in filas:
        pos = int(float(f[cpos]))
        bruta = f.get(cet)
        if bruta in (None, ""):
            sin_etiquetar.append(pos)
            continue
        et = SINONIMOS.get(_norm(bruta))
        if et is None:
            raise ValueError(f"Etiqueta no válida en el caso {pos}: {bruta!r} (usa alertar, ignorar o dudoso)")
        casos.append({"pos": pos, "etiqueta": et, "motivo": str(f.get(cmot) or "") if cmot else "",
                      "regla": str(f.get(creg) or "") if creg else ""})
    if sin_etiquetar:
        raise ValueError(f"Faltan etiquetas en los casos: {sin_etiquetar}. Etiquétalos antes de comparar.")
    return casos


def _jsonl(ruta):
    out = []
    if os.path.exists(ruta):
        for linea in open(ruta, encoding="utf-8-sig"):
            try:
                out.append(json.loads(linea))
            except ValueError:
                pass
    return out


def cargar_decisiones(carpeta):
    """Última entrada de cada orden en la bitácora del worker."""
    d = {}
    for o in _jsonl(os.path.join(carpeta, "ordenes.jsonl")):
        if "pos" in o:
            d[o["pos"]] = o
    return d


def costos(carpeta):
    llam = _jsonl(os.path.join(carpeta, "llamadas.jsonl"))
    return {"llamadas": len(llam), "tokens": sum(x.get("tokens") or 0 for x in llam),
            "usd": sum(float(x.get("costo_usd") or 0) for x in llam),
            "sin_costo": sum(1 for x in llam if x.get("costo_no_reportado"))}


def _accion_agente(rec):
    if not rec:
        return "no_ejecutado"
    if rec.get("resultado") == "ok":
        a = (rec.get("decision") or {}).get("accion")
        return SINONIMOS.get(_norm(a), "otra") if a else "sin_resultado"
    return "sin_resultado"


def evaluar(casos, decisiones, costo, todas_las_tareas=None):
    filas = []
    for c in casos:
        rec = decisiones.get(c["pos"])
        ag = _accion_agente(rec)
        if c["etiqueta"] == "dudoso":
            v = "dudoso (no puntúa)"
        elif ag == c["etiqueta"]:
            v = "acierto"
        elif ag in ("sin_resultado", "no_ejecutado"):
            v = "sin resultado"
        elif c["etiqueta"] == "ignorar":
            v = "falsa alarma"
        else:
            v = "alerta omitida"
        dec = (rec or {}).get("decision") or {}
        filas.append({**c, "agente": ag, "veredicto": v,
                      "regla_bitacora": "control" if (rec or {}).get("es_control") else (rec or {}).get("regla", ""),
                      "causa_agente": str(dec.get("causa", ""))[:250], "codigo_error": (rec or {}).get("codigo", ""),
                      "costo_usd": (rec or {}).get("costo_usd", 0), "segundos": (rec or {}).get("duracion_s", 0)})
    punt = [f for f in filas if f["etiqueta"] != "dudoso"]
    ac = sum(f["veredicto"] == "acierto" for f in punt)
    resueltos = [f for f in punt if f["agente"] in ("alertar", "ignorar")]
    ac_res = sum(f["veredicto"] == "acierto" for f in resueltos)
    tareas = todas_las_tareas if todas_las_tareas is not None else sum(
        1 for r in decisiones.values() if r.get("resultado") == "ok")
    return {
        "filas": filas, "n": len(filas), "dudosos": len(filas) - len(punt), "puntuables": len(punt),
        "aciertos": ac, "exactitud": ac / len(punt) if punt else 0.0,
        "exactitud_sobre_resueltos": ac_res / len(resueltos) if resueltos else 0.0,
        "sin_resultado": sum(f["veredicto"] == "sin resultado" for f in punt),
        "falsas_alarmas": sum(f["veredicto"] == "falsa alarma" for f in punt),
        "alertas_omitidas": sum(f["veredicto"] == "alerta omitida" for f in punt),
        "tareas_resueltas": tareas, "costo": costo,
        "usd_por_tarea_resuelta": costo["usd"] / tareas if tareas else 0.0,
        "usd_por_acierto": costo["usd"] / ac if ac else 0.0,
    }


def informe(r):
    L = ["# Evaluación del agente sobre datos reales (UCI Online Retail II)", "",
         "## Resultado", "",
         f"- Casos evaluados: **{r['n']}** (puntúan {r['puntuables']}; {r['dudosos']} marcados como dudosos y no puntúan)",
         f"- Coinciden con mi etiqueta humana: **{r['aciertos']} de {r['puntuables']} ({r['exactitud']:.1%})**",
         f"- Si se descartan los casos sin resultado: {r['exactitud_sobre_resueltos']:.1%}",
         f"- Falsas alarmas (el agente alertó y yo ignoraba): **{r['falsas_alarmas']}**",
         f"- Alertas omitidas (el agente ignoró y yo alertaba): **{r['alertas_omitidas']}**",
         f"- Sin resultado (error o sin decisión): **{r['sin_resultado']}**", "",
         "## Matriz de confusión (filas = mi etiqueta, columnas = el agente)", "",
         "| | alertar | ignorar | sin resultado |", "|---|---|---|---|"]
    for et in ("alertar", "ignorar"):
        sub = [f for f in r["filas"] if f["etiqueta"] == et]
        cuenta = Counter("sin resultado" if f["agente"] in ("sin_resultado", "no_ejecutado", "otra") else f["agente"]
                         for f in sub)
        L.append(f"| {et} | {cuenta['alertar']} | {cuenta['ignorar']} | {cuenta['sin resultado']} |")
    L += ["", "## Por regla", "", "| Regla | Casos | Aciertos | Fallos |", "|---|---|---|---|"]
    por = defaultdict(lambda: [0, 0])
    for f in r["filas"]:
        if f["etiqueta"] == "dudoso":
            continue
        k = f["regla_bitacora"] or f["regla"] or "-"
        por[k][0] += 1
        por[k][1] += f["veredicto"] == "acierto"
    for k, (n, a) in sorted(por.items()):
        L.append(f"| {k} | {n} | {a} | {n - a} |")
    fallos = [f for f in r["filas"] if f["veredicto"] in ("falsa alarma", "alerta omitida", "sin resultado")]
    L += ["", f"## Casos donde falló ({len(fallos)})", ""]
    if not fallos:
        L.append("Ninguno en este set (conviene ampliarlo para estar seguros).")
    for f in fallos:
        L += [f"- **Caso {f['pos']}** ({f['regla_bitacora'] or f['regla']}): {f['veredicto']}. "
              f"Humano: {f['etiqueta']}" + (f" ({f['motivo']})" if f["motivo"] else "") +
              f". Agente: {f['agente']}" + (f" — {f['causa_agente']}" if f["causa_agente"] else "") +
              (f" [error {f['codigo_error']}]" if f["codigo_error"] else "")]
    c = r["costo"]
    L += ["", "## Costo", "",
          f"- Tareas resueltas (con decisión registrada): **{r['tareas_resueltas']}**",
          f"- Llamadas a Grok: {c['llamadas']}; tokens: {c['tokens']:,}",
          f"- Costo total: **US$ {c['usd']:.4f}**",
          f"- **Costo por tarea resuelta: US$ {r['usd_por_tarea_resuelta']:.4f}**",
          f"- Costo por acierto (cuenta también lo gastado en errores y fallos): US$ {r['usd_por_acierto']:.4f}"]
    if c["sin_costo"]:
        L.append(f"- Atención: {c['sin_costo']} llamadas no reportaron costo; el total puede estar subestimado.")
    L += ["", "## Límites", "",
          "- El set es pequeño y estratificado (misma cantidad por regla), no refleja la frecuencia natural de los casos.",
          "- Las etiquetas son mías, hechas antes de ver las decisiones del agente; los dudosos no puntúan.",
          "- UCI Online Retail II son transacciones reales de un mayorista británico de artículos de regalo, "
          "no de renta de equipo: el dominio es cercano, no idéntico. No trae costos, inventario ni facturas."]
    return "\n".join(L)


def main(argv=None):
    a = argv if argv is not None else sys.argv[1:]
    if len(a) != 2:
        sys.exit("Uso: python evaluar.py data/evaluacion/casos.xlsx logs/evaluacion")
    ruta_casos, carpeta = a
    try:
        casos = cargar_casos(ruta_casos)
    except ValueError as e:
        sys.exit(f"[evaluar] {e}")
    r = evaluar(casos, cargar_decisiones(carpeta), costos(carpeta))
    texto = informe(r)
    open(os.path.join(carpeta, "evaluacion.md"), "w", encoding="utf-8").write(texto)
    with open(os.path.join(carpeta, "evaluacion_casos.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(r["filas"][0].keys()))
        w.writeheader()
        w.writerows(r["filas"])
    print(texto)


if __name__ == "__main__":
    main()