
"""Verificación independiente de una bitácora de corrida.

Calcula los totales directamente desde los archivos JSONL.
Distingue tareas resueltas, pendientes de reintento y agotadas.

Uso:
    python verificar_bitacora.py logs\\piloto2
    python verificar_bitacora.py --hay-errores logs\\corrida
"""

import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime

ESPERADOS = ("tokens", "latencia_s", "costo_usd")
MAX_INTENTOS = 4  # 1 intento inicial + 3 reintentos


def leer(ruta):
    out = []

    if os.path.exists(ruta):
        with open(ruta, encoding="utf-8-sig") as f:
            for linea in f:
                try:
                    out.append(json.loads(linea))
                except ValueError:
                    pass

    return out


def _t(s):
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def verificar(carpeta):
    ords = leer(os.path.join(carpeta, "ordenes.jsonl"))
    llam = leer(os.path.join(carpeta, "llamadas.jsonl"))
    errs = leer(os.path.join(carpeta, "errores.jsonl"))
    evs = leer(os.path.join(carpeta, "eventos.jsonl"))

    r = {}

    # ---------------------------------------------------------
    # RESULTADOS E INTENTOS POR TAREA
    # ---------------------------------------------------------

    por_pos = defaultdict(list)

    for o in ords:
        if "pos" in o:
            por_pos[o["pos"]].append(o)

    finales = {
        p: lst[-1]
        for p, lst in por_pos.items()
    }

    intentos_por_pos = {
        p: len(lst)
        for p, lst in por_pos.items()
    }

    r["registros_ordenes"] = len(ords)
    r["ordenes_unicas"] = len(finales)

    r["finales"] = dict(
        Counter(
            o.get("resultado", "?")
            for o in finales.values()
        )
    )

    r["ordenes_con_error"] = sum(
        1 for o in finales.values()
        if o.get("resultado") == "error"
    )

    r["ordenes_sin_decision"] = sum(
        1 for o in finales.values()
        if o.get("resultado") == "sin_decision"
    )

    r["ordenes_fallidas"] = (
        r["ordenes_con_error"] + r["ordenes_sin_decision"]
    )

    # Tareas pendientes que todavía tienen intentos disponibles.
    r["pendientes_reintento"] = sum(
        1
        for p, o in finales.items()
        if o.get("resultado") == "error"
        and intentos_por_pos[p] < MAX_INTENTOS
    )

    r["reintentos_agotados"] = sum(
        1
        for p, o in finales.items()
        if o.get("resultado") == "error"
        and intentos_por_pos[p] >= MAX_INTENTOS
    )

    # Tareas recuperadas después de un error o sin_decision.
    r["recuperadas"] = sum(
        1
        for lst in por_pos.values()
        if lst[-1].get("resultado") == "ok"
        and any(
            x.get("resultado") in ("error", "sin_decision")
            for x in lst[:-1]
        )
    )

    r["segundos_todos_los_intentos"] = round(
        sum(o.get("duracion_s") or 0 for o in ords),
        1
    )

    r["segundos_solo_ultimos"] = round(
        sum(o.get("duracion_s") or 0 for o in finales.values()),
        1
    )

    # ---------------------------------------------------------
    # LLAMADAS A GROK
    # ---------------------------------------------------------

    r["llamadas"] = len(llam)

    r["campos_llamadas"] = (
        sorted(set().union(*[set(x) for x in llam]))
        if llam else []
    )

    r["faltan_campos"] = [
        c for c in ESPERADOS
        if llam and c not in r["campos_llamadas"]
    ]

    r["tokens"] = sum(
        x.get("tokens")
        if isinstance(x.get("tokens"), (int, float))
        else (
            (x.get("tokens_entrada") or 0)
            + (x.get("tokens_salida") or 0)
        )
        for x in llam
    )

    lat = sorted(
        x["latencia_s"]
        for x in llam
        if isinstance(x.get("latencia_s"), (int, float))
    )

    r["latencia"] = (
        {
            "media": round(statistics.mean(lat), 2),
            "mediana": round(statistics.median(lat), 2),
            "p95": lat[int(0.95 * (len(lat) - 1))],
            "max": lat[-1],
            "n": len(lat)
        }
        if lat else None
    )

    r["costo_usd"] = round(
        sum(float(x.get("costo_usd") or 0) for x in llam),
        6
    )

    r["llamadas_sin_costo"] = sum(
        1 for x in llam
        if x.get("costo_no_reportado")
    )

    n_ok = sum(
        1 for o in finales.values()
        if o.get("resultado") == "ok"
    )

    r["ordenes_resueltas"] = n_ok

    r["usd_por_orden"] = (
        r["costo_usd"] / len(finales)
        if finales else 0.0
    )

    r["usd_por_orden_resuelta"] = (
        r["costo_usd"] / n_ok
        if n_ok else 0.0
    )

    # ---------------------------------------------------------
    # ERRORES DE LLAMADAS A GROK
    # ---------------------------------------------------------

    r["intentos_fallidos"] = len(errs)

    r["intentos_por_codigo"] = dict(
        Counter(
            e.get("codigo", "?")
            for e in errs
        )
    )

    # ---------------------------------------------------------
    # EVENTOS Y DURACIÓN
    # ---------------------------------------------------------

    segmentos = []
    inicio = None

    for e in evs:
        h = _t(e.get("hora"))

        if not h:
            continue

        if e.get("evento") == "inicio":

            # Si hubo reinicio sin evento fin, cerramos
            # el segmento anterior con el último registro disponible.
            if inicio is not None:
                anteriores = [
                    _t(x.get("hora"))
                    for x in ords + llam + errs
                    if _t(x.get("hora")) is not None
                    and inicio <= _t(x.get("hora")) <= h
                ]

                cierre = max(anteriores, default=inicio)

                if cierre >= inicio:
                    segmentos.append((inicio, cierre))

            inicio = h

        elif e.get("evento") == "fin" and inicio is not None:
            segmentos.append((inicio, h))
            inicio = None

    todos_tiempos = [
        _t(x.get("hora"))
        for x in ords + llam + errs + evs
        if _t(x.get("hora")) is not None
    ]

    ultimo = max(todos_tiempos, default=None)

    r["segmentos_cerrados"] = len(segmentos)
    r["ejecucion_sin_cierre"] = inicio is not None

    if inicio is not None and ultimo is not None:
        if ultimo >= inicio:
            segmentos.append((inicio, ultimo))

    r["horas_activas"] = round(
        sum(
            (b - a).total_seconds()
            for a, b in segmentos
        ) / 3600,
        2
    )

    tiempos_eventos = [
        _t(e.get("hora"))
        for e in evs
        if _t(e.get("hora")) is not None
    ]

    primero = min(tiempos_eventos, default=None)

    r["horas_de_reloj"] = (
        round((ultimo - primero).total_seconds() / 3600, 2)
        if primero and ultimo else 0.0
    )

    r["reinicios"] = max(
        0,
        sum(1 for e in evs if e.get("evento") == "inicio") - 1
    )

    r["pausas_por_errores"] = sum(
        1 for e in evs
        if e.get("evento") == "pausa_por_errores_seguidos"
    )

    r["paradas"] = dict(
        Counter(
            e["evento"]
            for e in evs
            if e.get("evento") in (
                "stop_manual",
                "tope_gasto",
                "limite_horas",
                "parada_por_error",
                "interrumpido"
            )
        )
    )

    # ---------------------------------------------------------
    # DETECCIÓN DEL LÍMITE DE PRESUPUESTO
    # ---------------------------------------------------------

    # Puede registrarse de dos formas:
    # 1. evento = tope_gasto
    # 2. evento = parada_por_error y codigo = TOPE_GASTO

    r["tope_gasto_alcanzado"] = any(
        e.get("evento") == "tope_gasto"
        or (
            e.get("evento") == "parada_por_error"
            and e.get("codigo") == "TOPE_GASTO"
        )
        for e in evs
    )

    return r


def hay_errores(carpeta):
    """Indica si quedan tareas reintentables y hay presupuesto disponible."""

    r = verificar(carpeta)

    if r["tope_gasto_alcanzado"]:
        return False

    return r["pendientes_reintento"] > 0


def informe(r):
    L = [
        f"Órdenes únicas: {r['ordenes_unicas']} "
        f"({r['registros_ordenes']} registros, contando reintentos)",

        f"Resultado final por orden: {r['finales']}",

        f"Órdenes resueltas: {r['ordenes_resueltas']}",

        f"Órdenes fallidas: {r['ordenes_fallidas']} "
        f"(error: {r['ordenes_con_error']}; "
        f"sin decisión: {r['ordenes_sin_decision']})",

        f"Pendientes de reintento: {r['pendientes_reintento']}",

        f"Reintentos agotados: {r['reintentos_agotados']}",

        f"Recuperadas tras un intento fallido: {r['recuperadas']}",

        f"Intentos fallidos de llamadas a Grok: "
        f"{r['intentos_fallidos']} {r['intentos_por_codigo']}",

        "",

        f"Llamadas a Grok: {r['llamadas']}; tokens: {r['tokens']:,}",

        f"Costo reportado: US$ {r['costo_usd']:.4f} "
        f"(por orden: {r['usd_por_orden']:.4f}; "
        f"por orden resuelta: {r['usd_por_orden_resuelta']:.4f})"
    ]

    if r["llamadas_sin_costo"]:
        L.append(
            f"ATENCIÓN: {r['llamadas_sin_costo']} llamadas sin costo "
            "reportado: el total puede estar subestimado."
        )

    if r["latencia"]:
        l = r["latencia"]

        L.append(
            f"Latencia por llamada: media {l['media']} s, "
            f"mediana {l['mediana']} s, "
            f"p95 {l['p95']} s, máx {l['max']} s"
        )

    L += [
        "",
        f"Tiempo trabajado sumando todos los intentos: "
        f"{r['segundos_todos_los_intentos']} s "
        f"(solo últimos intentos: {r['segundos_solo_ultimos']} s)",

        f"Horas registradas en segmentos: {r['horas_activas']} h; "
        f"de reloj, del primer al último registro: "
        f"{r['horas_de_reloj']} h; "
        f"reinicios: {r['reinicios']}; "
        f"pausas por errores: {r['pausas_por_errores']}; "
        f"paradas: {r['paradas'] or 'ninguna'}"
    ]

    if r["ejecucion_sin_cierre"]:
        L.append(
            "ATENCIÓN: la última ejecución no tiene evento 'fin' "
            "(sigue corriendo o se interrumpió)."
        )

    if r["faltan_campos"]:
        L += [
            "",
            f"ATENCIÓN: en llamadas.jsonl faltan los campos "
            f"{r['faltan_campos']}. "
            f"Campos disponibles: {r['campos_llamadas']}"
        ]

    if r["tope_gasto_alcanzado"]:
        L.append(
            "ATENCIÓN: se alcanzó el tope de gasto. "
            "No se deben iniciar más reintentos con esta bitácora."
        )

    return "\n".join(L)


if __name__ == "__main__":

    if len(sys.argv) == 3 and sys.argv[1] == "--hay-errores":

        try:
            r = verificar(sys.argv[2])

            pendientes = r["pendientes_reintento"]
            agotadas = r["reintentos_agotados"]

            print(
                f"[verificar] pendientes reintentables: {pendientes}; "
                f"reintentos agotados: {agotadas}"
            )

            if r["tope_gasto_alcanzado"]:
                print(
                    "[verificar] tope de gasto alcanzado; "
                    "no se reintentará."
                )
                sys.exit(0)

            sys.exit(1 if pendientes else 0)

        except Exception as e:
            print(
                f"[verificar] ERROR: {type(e).__name__}: {e}",
                file=sys.stderr
            )
            sys.exit(2)

    if len(sys.argv) != 2:
        sys.exit(
            "Uso: python verificar_bitacora.py logs\\piloto2"
        )

    try:
        print(informe(verificar(sys.argv[1])))
    except Exception as e:
        print(
            f"ERROR al verificar la bitácora: {type(e).__name__}: {e}",
            file=sys.stderr
        )
        sys.exit(2)
