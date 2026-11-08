"""Set de evaluación: 30 líneas de la cola (semilla fija) para etiquetar A MANO antes de ver lo que decide Grok."""
import os
import random
import sqlite3

from openpyxl import Workbook

SEMILLA, POR_REGLA, N_CONTROL = 7, 5, 5
con = sqlite3.connect("data/uci.db")
rng = random.Random(SEMILLA)

# pos > 5: las 5 primeras ya las viste en el piloto, así que el etiquetado no sería ciego.
elegidos = []
for (regla,) in con.execute("SELECT DISTINCT regla FROM cola ORDER BY regla").fetchall():
    pos = sorted(r[0] for r in con.execute("SELECT pos FROM cola WHERE regla=? AND pos>5", (regla,)))
    n = N_CONTROL if regla == "control_limpia" else POR_REGLA
    elegidos += rng.sample(pos, min(n, len(pos)))

wb = Workbook()
ws = wb.active
ws.title = "casos"
ws.append(["caso", "pos", "regla_vista", "factura", "producto", "descripcion", "cantidad", "fecha", "precio",
           "cliente", "pais", "compras_previas_mismo_producto", "facturas_mismo_dia_misma_compra",
           "lineas_en_la_factura", "precio_mediana_producto", "esperado (alertar/ignorar/dudoso)", "motivo"])

for k, pos in enumerate(sorted(elegidos), 1):
    lid, regla = con.execute("SELECT linea_id, regla FROM cola WHERE pos=?", (pos,)).fetchone()
    _, fac, prod, desc, cant, fecha, precio, cli, pais, _c = con.execute(
        "SELECT id, factura, producto, descripcion, cantidad, fecha, precio, cliente, pais, cancelacion "
        "FROM lineas WHERE id=?", (lid,)).fetchone()
    previas = con.execute("SELECT COUNT(*) FROM lineas WHERE cliente=? AND producto=? AND cancelacion=0 AND fecha<?",
                          (cli, prod, fecha)).fetchone()[0] if cli else "sin cliente"
    mismo_dia = con.execute(
        "SELECT COUNT(DISTINCT factura) FROM lineas WHERE cliente=? AND producto=? AND cantidad=? AND precio=? "
        "AND substr(fecha,1,10)=substr(?,1,10) AND cancelacion=0",
        (cli, prod, cant, precio, fecha)).fetchone()[0] if cli else "sin cliente"
    precios = sorted(r[0] for r in con.execute(
        "SELECT precio FROM lineas WHERE producto=? AND cancelacion=0 AND cantidad>0 AND precio>0", (prod,)))
    mediana = precios[len(precios) // 2] if precios else "sin ventas"
    n_lin = con.execute("SELECT COUNT(*) FROM lineas WHERE factura=?", (fac,)).fetchone()[0]
    vista = "revision_de_rutina" if regla == "control_limpia" else regla     # igual que lo ve Grok
    ws.append([k, pos, vista, fac, prod, desc, cant, fecha, precio, cli, pais, previas, mismo_dia, n_lin, mediana, "", ""])

for col, ancho in zip("ABCDEFGHIJKLMNOPQ", (6, 7, 26, 11, 11, 34, 9, 19, 8, 9, 14, 16, 16, 12, 14, 24, 40)):
    ws.column_dimensions[col].width = ancho
os.makedirs("data/evaluacion", exist_ok=True)
wb.save("data/evaluacion/casos.xlsx")
print("Listo: data/evaluacion/casos.xlsx (30 casos). Llena las columnas 'esperado' y 'motivo'.")