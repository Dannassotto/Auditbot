"""Carga UCI Online Retail II (xlsx) a data/uci.db sin pandas. No toca data/auditbot.db."""
import glob
import os
import sqlite3
import sys
from datetime import datetime

from openpyxl import load_workbook

ENTRADA = glob.glob("data/uci/*.xlsx") + glob.glob("database/*.xlsx") + glob.glob("engine/database/*.xlsx")
SALIDA = "data/uci.db"
COLUMNAS = {"Invoice": "factura", "StockCode": "producto", "Description": "descripcion",
            "Quantity": "cantidad", "InvoiceDate": "fecha", "Price": "precio",
            "Customer ID": "cliente", "Country": "pais"}

if not ENTRADA:
    sys.exit("No encontré ningún .xlsx. Revisa con: dir data\\uci")

os.makedirs("data", exist_ok=True)
if os.path.exists(SALIDA):
    os.remove(SALIDA)
con = sqlite3.connect(SALIDA)
con.execute("""CREATE TABLE lineas (
    id INTEGER PRIMARY KEY, factura TEXT, producto TEXT, descripcion TEXT,
    cantidad INTEGER, fecha TEXT, precio REAL, cliente TEXT, pais TEXT, cancelacion INTEGER)""")


def texto(v):
    return None if v is None else str(v).strip()


def cliente(v):
    if v is None or v == "":
        return None
    try:
        return str(int(float(v)))
    except (TypeError, ValueError):
        return str(v)


def fecha(v):
    return v.strftime("%Y-%m-%d %H:%M:%S") if isinstance(v, datetime) else texto(v)


print("Abriendo", ENTRADA[0], "(tarda varios minutos, es normal)...")
wb = load_workbook(ENTRADA[0], read_only=True, data_only=True)
print("Hojas:", wb.sheetnames)

n = 0
for hoja in wb.worksheets:
    filas = hoja.iter_rows(values_only=True)
    encabezado = [str(c).strip() if c is not None else "" for c in next(filas)]
    print(f"Hoja '{hoja.title}' columnas:", encabezado)
    faltan = [c for c in COLUMNAS if c not in encabezado]
    if faltan:
        sys.exit(f"Faltan columnas esperadas: {faltan}. Pégame la línea de columnas de arriba.")
    ix = {c: encabezado.index(c) for c in COLUMNAS}

    lote = []
    for f in filas:
        if f is None or all(c is None for c in f):
            continue
        n += 1
        factura = texto(f[ix["Invoice"]])
        lote.append((n, factura, texto(f[ix["StockCode"]]), texto(f[ix["Description"]]),
                     f[ix["Quantity"]], fecha(f[ix["InvoiceDate"]]), f[ix["Price"]],
                     cliente(f[ix["Customer ID"]]), texto(f[ix["Country"]]),
                     1 if (factura or "").upper().startswith("C") else 0))
        if len(lote) >= 50_000:
            con.executemany("INSERT INTO lineas VALUES (?,?,?,?,?,?,?,?,?,?)", lote)
            con.commit()
            lote = []
            print(f"  {n:,} líneas cargadas...")
    if lote:
        con.executemany("INSERT INTO lineas VALUES (?,?,?,?,?,?,?,?,?,?)", lote)
        con.commit()
wb.close()

con.execute("CREATE INDEX ix_lineas_producto ON lineas(producto)")
con.execute("CREATE INDEX ix_lineas_cliente ON lineas(cliente)")
con.commit()

q = lambda sql: con.execute(sql).fetchone()[0]
print("\n=== Resumen de UCI ===")
print("Líneas totales:        ", q("SELECT COUNT(*) FROM lineas"))
print("Rango de fechas:       ", con.execute("SELECT MIN(fecha), MAX(fecha) FROM lineas").fetchone())
print("Cancelaciones (C...):  ", q("SELECT COUNT(*) FROM lineas WHERE cancelacion=1"))
print("Cantidad <= 0:         ", q("SELECT COUNT(*) FROM lineas WHERE cantidad<=0"))
print("Precio <= 0:           ", q("SELECT COUNT(*) FROM lineas WHERE precio<=0"))
print("Sin cliente:           ", q("SELECT COUNT(*) FROM lineas WHERE cliente IS NULL"))
print("Productos distintos:   ", q("SELECT COUNT(DISTINCT producto) FROM lineas"))
print("Clientes distintos:    ", q("SELECT COUNT(DISTINCT cliente) FROM lineas"))
con.close()
print("\nListo:", SALIDA)