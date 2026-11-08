import sqlite3
import sys

cliente, producto = sys.argv[1], sys.argv[2]
con = sqlite3.connect("data/uci.db")
filas = con.execute(
    "SELECT factura, fecha, cantidad, precio FROM lineas "
    "WHERE cliente=? AND producto=? ORDER BY fecha", (cliente, producto)).fetchall()
for f in filas:
    print(f)
print(len(filas), "líneas")