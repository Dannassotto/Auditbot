"""Resumen de la corrida desde la bitácora. Uso: python resumen_corrida.py [carpeta]"""
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime

DIR = sys.argv[1] if len(sys.argv) > 1 else "logs/corrida"


def leer(nombre):
    ruta, filas = os.path.join(DIR, nombre), []
    if os.path.exists(ruta):
        for l in open(ruta, encoding="utf-8"):
            try:
                filas.append(json.loads(l))
            except ValueError:
                pass
    return filas


ords = {}
for o in leer("ordenes.jsonl"):
    ords[o["pos"]] = o                     # la última entrada de cada orden
llam, errs = leer("llamadas.jsonl"), leer("errores.jsonl")
if not ords:
    sys.exit(f"No hay órdenes registradas en {DIR}")

n = len(ords)
res = Counter(o["resultado"] for o in ords.values())
ok = res["ok"]
t_in = sum(x.get("tokens_entrada", 0) for x in llam)
t_out = sum(x.get("tokens_salida", 0) for x in llam)
t_tot = sum(x.get("tokens", 0) for x in llam)
costo = sum(x.get("costo_usd", 0) for x in llam)
sin_costo = sum(1 for x in llam if x.get("costo_no_reportado"))
lat = [x["latencia_s"] for x in llam if "latencia_s" in x]
horas = sorted(datetime.fromisoformat(o["hora"]) for o in ords.values())
activo = sum(o.get("duracion_s", 0) for o in ords.values())

L = [f"# Resumen de la corrida ({DIR})", "",
     f"- Órdenes enviadas al agente: {n}",
     f"- Con decisión registrada (ok): {ok} ({ok / n:.1%}) | sin decisión: {res['sin_decision']} | con error: {res['error']}",
     f"- Llamadas a Grok: {len(llam)} ({len(llam) / n:.1f} por orden) | latencia media: {sum(lat) / max(len(lat), 1):.1f} s",
     f"- Tokens: {t_tot:,} en total ({t_in:,} de entrada + {t_out:,} de salida; el resto no se desglosa en el registro de la API)",
     f"- Costo total: US${costo:.4f} (incluye llamadas de órdenes fallidas)",
     f"- Costo por orden enviada: US${costo / n:.4f}",
     f"- Costo por tarea resuelta (ok): US${costo / max(ok, 1):.4f}",
     f"- Llamadas sin costo reportado por la API: {sin_costo}",
     f"- Primera orden: {horas[0]:%Y-%m-%d %H:%M} | última: {horas[-1]:%Y-%m-%d %H:%M} "
     f"({(horas[-1] - horas[0]).total_seconds() / 3600:.1f} h de reloj, {activo / 3600:.1f} h trabajando)",
     "", "## Por regla", "", "| Regla | Órdenes | ok | alertar | ignorar | sin decisión | error |", "|---|---|---|---|---|---|---|"]
por = defaultdict(Counter)
for o in ords.values():
    c = por[o["regla"]]
    c["n"] += 1
    c[o["resultado"]] += 1
    c[(o.get("decision") or {}).get("accion", "-")] += 1
for r, c in sorted(por.items()):
    L.append(f"| {r} | {c['n']} | {c['ok']} | {c['alertar']} | {c['ignorar']} | {c['sin_decision']} | {c['error']} |")

ctl = [o for o in ords.values() if o["es_control"] and o["resultado"] == "ok"]
if ctl:
    al = sum(1 for o in ctl if o["decision"]["accion"] == "alertar")
    L += ["", "## Control (líneas limpias elegidas al azar)", "",
          f"- Grok alertó {al} de {len(ctl)} ({al / len(ctl):.1%}). Son alertas que las reglas no habían levantado."]

L += ["", "## Errores", ""]
ce = Counter(o.get("codigo") for o in ords.values() if o["resultado"] == "error")
L.append("- Órdenes que terminaron en error: " + (", ".join(f"{k}: {v}" for k, v in ce.items()) or "ninguna"))
ci = Counter(e.get("codigo") for e in errs)
L.append("- Intentos de llamada fallidos (incluye los que se recuperaron con un reintento): "
         + (", ".join(f"{k}: {v}" for k, v in ci.items()) or "ninguno"))
L += ["", "## Qué NO mide este resumen", "",
      "- «ok» significa que Grok registró una decisión, no que sea correcta. UCI no trae respuestas verdaderas: "
      "la exactitud se mide aparte, con el set de evaluación de 30 casos."]
txt = "\n".join(L)
print(txt)
open(os.path.join(DIR, "resumen.md"), "w", encoding="utf-8").write(txt + "\n")