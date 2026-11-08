
"""Worker de la corrida larga sobre la cola de UCI. Sin interfaz, reanudable, con bitácora por orden."""
import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime

try:
    from services import llm
    from services.llm import ErrorLLM
except ImportError as e:
    sys.exit(f"Tu services/llm.py no es la versión con ErrorLLM ({e}). Pégamelo y lo vemos.")

from agent.uci import auditar_linea

CAMPOS = (
    "id", "factura", "producto", "descripcion", "cantidad",
    "fecha", "precio", "cliente", "pais", "cancelacion"
)

MAX_INTENTOS = 4  # 1 intento inicial + 3 reintentos


def escribir(ruta, reg):
    with open(ruta, "a", encoding="utf-8") as f:
        f.write(json.dumps(reg, ensure_ascii=False, default=str) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="logs/corrida", help="carpeta de la bitácora")
    ap.add_argument("--db", default="data/uci.db")
    ap.add_argument("--tope", type=float, default=55.0, help="tope de gasto de ESTA bitácora, en USD")
    ap.add_argument("--max", type=int, default=None, help="máximo de órdenes en esta ejecución")
    ap.add_argument("--horas", type=float, default=None, help="detenerse tras N horas")
    ap.add_argument("--pausa", type=float, default=0.0, help="segundos fijos entre órdenes")
    ap.add_argument("--casos", default=None, help="xlsx/csv con columna pos")
    a = ap.parse_args()

    os.makedirs(a.dir, exist_ok=True)

    r_ord = f"{a.dir}/ordenes.jsonl"
    r_ev = f"{a.dir}/eventos.jsonl"
    stop = f"{a.dir}/STOP"

    llm.RUTA_LLAMADAS = f"{a.dir}/llamadas.jsonl"
    llm.RUTA_ERRORES = f"{a.dir}/errores.jsonl"
    llm.MAX_BUDGET_USD = a.tope
    llm.gasto_total = llm._cargar_gasto()

    def evento(tipo, **d):
        escribir(r_ev, {
            "hora": datetime.now().isoformat(),
            "evento": tipo,
            **d
        })
        print(f"[{tipo}] {d}" if d else f"[{tipo}]")

    try:
        # Evita que Windows suspenda el equipo durante la corrida.
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
    except Exception:
        pass

    con = sqlite3.connect(a.db)
    con.execute(
        "CREATE INDEX IF NOT EXISTS ix_lineas_factura ON lineas(factura)"
    )

    # ---------------------------------------------------------
    # Identificar tareas completadas y contar intentos.
    # Solo "ok" cuenta como tarea resuelta.
    # error y sin_decision pueden reintentarse hasta 3 veces.
    # ---------------------------------------------------------
    intentos = {}
    resueltas = set()

    if os.path.exists(r_ord):
        with open(r_ord, encoding="utf-8") as f:
            for l in f:
                try:
                    o = json.loads(l)
                    pos = o["pos"]
                    resultado = o.get("resultado")

                    intentos[pos] = intentos.get(pos, 0) + 1

                    if resultado == "ok":
                        resueltas.add(pos)

                except (ValueError, KeyError, TypeError):
                    pass

    cola = con.execute(
        "SELECT pos, linea_id, regla, es_control FROM cola ORDER BY pos"
    ).fetchall()

    pendientes = [
        c for c in cola
        if c[0] not in resueltas
        and intentos.get(c[0], 0) < MAX_INTENTOS
    ]

    agotadas = sum(
        1 for c in cola
        if c[0] not in resueltas
        and intentos.get(c[0], 0) >= MAX_INTENTOS
    )

    if agotadas:
        evento("reintentos_agotados", cantidad=agotadas)

    if a.casos:
        from evaluar import posiciones_casos
        solo = set(posiciones_casos(a.casos))
        pendientes = [c for c in pendientes if c[0] in solo]

    if a.max:
        pendientes = pendientes[:a.max]

    evento(
        "inicio",
        modelo=llm.MODEL,
        tope_usd=a.tope,
        gastado_antes=round(llm.gasto_total, 4),
        pendientes=len(pendientes),
        ya_hechas=len(resueltas),
        pausa_s=a.pausa
    )

    inicio, seguidos = time.time(), 0

    try:
        for i, (pos, lid, regla, es_control) in enumerate(pendientes, 1):

            if os.path.exists(stop):
                evento("stop_manual")
                break

            if llm.gasto_total >= llm.MAX_BUDGET_USD:
                evento("tope_gasto", gastado=round(llm.gasto_total, 4))
                break

            if a.horas and time.time() - inicio > a.horas * 3600:
                evento("limite_horas")
                break

            fila_original = con.execute(
                f"SELECT {','.join(CAMPOS)} FROM lineas WHERE id=?",
                (lid,)
            ).fetchone()

            if fila_original is None:
                evento("linea_no_encontrada", pos=pos, linea_id=lid)
                continue

            fila = dict(zip(CAMPOS, fila_original))

            # Grok no sabe cuáles son tareas de control.
            bandera = "revision_de_rutina" if es_control else regla

            oid = f"uci-{lid}"
            g0 = llm.gasto_total
            t0 = time.time()

            rec = {
                "hora": datetime.now().isoformat(),
                "pos": pos,
                "linea_id": lid,
                "orden_id": oid,
                "regla": regla,
                "es_control": es_control,
                "intento": intentos.get(pos, 0) + 1
            }

            parar = False

            try:
                decision, nota = auditar_linea(
                    con,
                    fila,
                    bandera,
                    orden_id=oid
                )

                if decision:
                    rec.update(
                        resultado="ok",
                        decision=decision
                    )
                else:
                    rec.update(
                        resultado="sin_decision",
                        nota=nota
                    )

                seguidos = 0

            except ErrorLLM as e:
                rec.update(
                    resultado="error",
                    codigo=e.info["codigo"],
                    error=e.info["mensaje"]
                )

                seguidos += 1
                parar = e.info["codigo"] in (
                    "LLAVE_INVALIDA",
                    "TOPE_GASTO"
                )

            except Exception as e:
                rec.update(
                    resultado="error",
                    codigo="EXCEPCION",
                    error=f"{type(e).__name__}: {e}"[:300]
                )

                seguidos += 1

            rec.update(
                costo_usd=round(llm.gasto_total - g0, 6),
                duracion_s=round(time.time() - t0, 2)
            )

            escribir(r_ord, rec)

            acc = (
                (rec.get("decision") or {}).get("accion")
                or rec.get("codigo", "-")
            )

            print(
                f"[{i}/{len(pendientes)}] "
                f"{rec['resultado']:<12} "
                f"{acc:<9} "
                f"${rec['costo_usd']:.4f} "
                f"{rec['duracion_s']:>5}s "
                f"{regla}"
            )

            if parar:
                evento(
                    "parada_por_error",
                    codigo=rec["codigo"]
                )
                break

            if seguidos >= 10:
                evento(
                    "pausa_por_errores_seguidos",
                    segundos=300
                )
                time.sleep(300)
                seguidos = 0

            if a.pausa:
                time.sleep(a.pausa)

    except KeyboardInterrupt:
        evento("interrumpido")

    evento(
        "fin",
        gastado=round(llm.gasto_total, 4),
        minutos=round((time.time() - inicio) / 60, 1)
    )

    con.close()

    print(f"\nResumen: python resumen_corrida.py {a.dir}")


if __name__ == "__main__":
    main()
