import json
import os
import sqlite3
import sys

from engine.margins import revisar_orden
from agent.loop import auditar_orden
from services import llm


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
                hechas.add(json.loads(linea)["orden"])
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
    d = auditar_orden(con, h)
    print(f"Orden {h['orden_id']} {h['banderas']} -> {d}\n")
    with open("logs/decisiones.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"orden": h["orden_id"], "banderas": h["banderas"],
                            "decision": d}, ensure_ascii=False) + "\n")

print(f"Gasto total: ${llm.gasto_total:.4f}")