"""Arma la cola de trabajo de la corrida sobre data/uci.db. Solo sqlite3. Reproducible (semilla fija)."""
import random
import sqlite3

SEMILLA = 42
CUPOS = {"precio_cero": 400, "cantidad_negativa": 400, "cancelacion_sin_compra": 400,
         "compra_repetida_mismo_dia": 400, "linea_repetida_en_factura": 300}
N_CONTROL = 80
DESDE_CANCELACIONES = "2010-06-01"
REAL = "producto GLOB '[0-9][0-9][0-9][0-9][0-9]*'"      # excluye POST, M, D, BANK CHARGES...
REAL_C = REAL.replace("producto", "c.producto")

con = sqlite3.connect("data/uci.db")
ids = lambda sql: sorted(r[0] for r in con.execute(sql))

candidatas = {
    "precio_cero": ids(f"SELECT id FROM lineas WHERE precio<=0 AND {REAL}"),
    "cantidad_negativa": ids(f"SELECT id FROM lineas WHERE cantidad<0 AND cancelacion=0 AND {REAL}"),
    "cancelacion_sin_compra": ids(f"""SELECT c.id FROM lineas c
        WHERE c.cancelacion=1 AND c.cliente IS NOT NULL AND c.fecha>='{DESDE_CANCELACIONES}' AND {REAL_C}
        AND NOT EXISTS (SELECT 1 FROM lineas o WHERE o.cliente=c.cliente AND o.producto=c.producto
                        AND o.cancelacion=0 AND o.fecha<c.fecha)"""),
    "compra_repetida_mismo_dia": ids(f"""SELECT MAX(id) FROM lineas
        WHERE cancelacion=0 AND cliente IS NOT NULL AND cantidad>0 AND precio>0 AND {REAL}
        GROUP BY cliente, producto, cantidad, precio, substr(fecha,1,10)
        HAVING COUNT(DISTINCT factura)>1"""),
    "linea_repetida_en_factura": ids(f"""SELECT MAX(id) FROM lineas
        WHERE cancelacion=0 AND cliente IS NOT NULL AND cantidad>0 AND precio>0 AND {REAL}
        GROUP BY factura, producto, cantidad, precio HAVING COUNT(*)>1"""),
}

rng = random.Random(SEMILLA)
elegidas = {}          # id -> regla (si una línea cae en dos reglas, gana la primera)
marcadas = set()
for regla, lista in candidatas.items():
    marcadas.update(lista)
    muestra = rng.sample(lista, min(CUPOS[regla], len(lista)))
    for i in muestra:
        elegidas.setdefault(i, regla)

limpias = [i for i in ids(f"""SELECT id FROM lineas WHERE cancelacion=0 AND cantidad>0 AND precio>0
                              AND cliente IS NOT NULL AND {REAL}""") if i not in marcadas]
for i in rng.sample(limpias, N_CONTROL):
    elegidas[i] = "control_limpia"

cola = list(elegidas.items())
rng.shuffle(cola)

con.execute("DROP TABLE IF EXISTS cola")
con.execute("CREATE TABLE cola (pos INTEGER PRIMARY KEY, linea_id INTEGER, regla TEXT, es_control INTEGER)")
con.executemany("INSERT INTO cola VALUES (?,?,?,?)",
                [(n + 1, i, r, 1 if r == "control_limpia" else 0) for n, (i, r) in enumerate(cola)])
con.commit()

print("Candidatas por regla (antes del cupo):")
for r, l in candidatas.items():
    print(f"  {r:28s} {len(l):>7,}")
print(f"\nCola total: {len(cola):,} líneas")
for r, n in con.execute("SELECT regla, COUNT(*) FROM cola GROUP BY regla ORDER BY 2 DESC"):
    print(f"  {r:28s} {n:>5,}")
con.close()