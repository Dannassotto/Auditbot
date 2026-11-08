import json
import os
import runpy
import sqlite3
import sys

# Subcomandos de la corrida larga (datos reales de UCI): una sola entrada al proyecto.
SUBCOMANDOS = {"corrida": "worker", "resumen": "resumen_corrida"}
if len(sys.argv) > 1 and sys.argv[1] in SUBCOMANDOS:
    modulo = SUBCOMANDOS[sys.argv[1]]
    sys.argv = [modulo + ".py"] + sys.argv[2:]
    runpy.run_module(modulo, run_name="__main__")
    raise SystemExit(0)

from engine.margins import revisar_orden
from agent.loop import auditar_orden
from services import llm
from services.llm import ErrorLLM


if len(sys.argv) > 1 and sys.argv[1] == "ordenes":
    PEDIDAS = {int(x) for x in sys.argv[2:]}
    LIMITE = len(PEDIDAS)
else:
    PEDIDAS = None
    LIMITE = int(sys.argv[1]) if len(sys.argv) > 1 else 3

con = sqlite3.connect("data/auditbot.db")
ids = [r[0] for r in con.execute("SELECT id FROM ordenes ORDER BY id")]
hallazgos = [h for h in (revisar_orden(con, i) for i in ids) if h["banderas"]]
print(f"{len(ids)} órdenes revisadas con reglas, {len(hallazgos)} con banderas.")

if PEDIDAS:
    seleccion = [h for h in hallazgos if h["orden_id"] in PEDIDAS]
else:
    hechas = set()
    if os.path.exists("logs/decisiones.jsonl"):
        with open("logs/decisiones.jsonl", encoding="utf-8") as f:
            for linea in f:
                try:
                    r = json.loads(linea)
                except ValueError:
                    continue
                if r.get("decision") is not None or not r.get("error"):   # las que fallaron se reintentan
                    hechas.add(r["orden"])
    pendientes = [h for h in hallazgos if h["orden_id"] not in hechas]

    por_tipo = {}
    for h in pendientes:
        por_tipo.setdefault(tuple(h["banderas"]), []).append(h)
    seleccion = []
    while len(seleccion) < LIMITE and any(por_tipo.values()):
        for tipo in list(por_tipo):
            if por_tipo[tipo] and len(seleccion) < LIMITE:
                seleccion.append(por_tipo[tipo].pop(0))

print(f"Grok investigará {len(seleccion)} órdenes.\n")

os.makedirs("logs", exist_ok=True)
for h in seleccion:
    err = None
    try:
        d = auditar_orden(con, h)
    except ErrorLLM as e:
        if e.info["codigo"] in ("TOPE_GASTO", "LLAVE_INVALIDA"):
            print(f"Me detengo: {e.info['mensaje']}")
            break
        d, err = None, f"{e.info['codigo']}: {e.info['mensaje']}"
    print(f"Orden {h['orden_id']} {h['banderas']} -> {d if not err else err}\n")
    registro = {"orden": h["orden_id"], "banderas": h["banderas"], "decision": d}
    if err:
        registro["error"] = err
    with open("logs/decisiones.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")

print(f"Gasto acumulado (todas las ejecuciones): ${llm.gasto_total:.4f}")