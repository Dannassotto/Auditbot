"""Genera la base de datos de AudiBot. Es 100% determinista: sin azar y con fecha fija,
así que cada corrida produce EXACTAMENTE los mismos datos (mismos ids, fechas y montos)."""
import os
import sqlite3
from datetime import date, timedelta

DB = "data/auditbot.db"
N_ORDENES = 1000
DIAS_HISTORIA = 60
HOY = date(2026, 10, 14)   # fija: la base no cambia con el día en que la generes

# (nombre, tipo, costo, precio_lista, stock)
PRODUCTOS = [
    ("Andamio modular", "renta", 80, 95, 120),
    ("Revolvedora 1 saco", "renta", 150, 185, 40),
    ("Rotomartillo", "renta", 60, 78, 60),
    ("Cemento gris 50kg", "venta", 210, 245, 500),
    ("Varilla 3/8", "venta", 135, 160, 800),
    ("Casco de seguridad", "venta", 55, 72, 300),
    ("Compactadora", "renta", 320, 400, 12),
    ("Cable calibre 12", "venta", 22, 30, 1000),
]
SUBEN_COSTO = {1: 98, 8: 31}   # el costo sube hace 15 días y nadie actualiza los precios

CLIENTES = ["Constructora Lomas", "Obras del Norte", "Ing. Ramírez",
            "Casa y Muro SA", "Grupo Montaña", "Remodelaciones Pérez"]
FACTORES_PRECIO = [0.97, 1.00, 1.03, 1.05, 1.00, 1.02]   # precio = lista x factor, en pesos enteros
FACTORES_SUBFACTURA = [0.80, 0.85, 0.90]

# ---- Casos etiquetados, elegidos a mano (id de orden). Son la "respuesta correcta" del set de evaluación ----
DESCUENTO_AUTORIZADO = [1, 50, 101, 151, 201, 250, 301, 351, 401, 450, 501, 551, 601, 650, 701]
SIN_FACTURA = [71, 225, 378, 532, 685, 836]
SUBFACTURADA = [20, 122, 173, 327, 481, 634, 786, 935]
FACTURA_DUPLICADA = [276, 429, 583, 737, 885]
# Stock insuficiente: 8 rentas de compactadora (stock 12) por 15-20 unidades; ids 1001-1008.
STOCK_INSUFICIENTE = [(15, 0), (16, 1), (17, 2), (18, 3), (19, 4), (20, 5), (15, 7), (18, 10)]  # (cantidad, días atrás)

os.makedirs("data", exist_ok=True)
if os.path.exists(DB):
    os.remove(DB)
con = sqlite3.connect(DB)
con.executescript(open("db/schema.sql", encoding="utf-8").read())

for i, (nombre, tipo, costo, lista, stock) in enumerate(PRODUCTOS, start=1):
    con.execute("INSERT INTO productos VALUES (?,?,?,?)", (i, nombre, tipo, lista))
    con.execute("INSERT INTO inventario VALUES (?,?)", (i, stock))
    con.execute("INSERT INTO costos VALUES (?,?,?)",
                (i, costo, str(HOY - timedelta(days=DIAS_HISTORIA + 30))))
    if i in SUBEN_COSTO:
        con.execute("INSERT INTO costos VALUES (?,?,?)",
                    (i, SUBEN_COSTO[i], str(HOY - timedelta(days=15))))


def costo_vigente(pid, fecha):
    return con.execute(
        "SELECT costo_unitario FROM costos WHERE producto_id=? AND vigente_desde<=? "
        "ORDER BY vigente_desde DESC LIMIT 1", (pid, str(fecha))).fetchone()[0]


# ---- 1. órdenes normales (patrones fijos, sin azar) ----
ordenes = []
for i in range(1, N_ORDENES + 1):
    pid = (i * 5) % 8 + 1
    _, tipo, _, lista, _ = PRODUCTOS[pid - 1]
    if pid == 7:
        cantidad = 1 + (i * 3) % 5
    elif tipo == "renta":
        cantidad = 1 + (i * 7) % 15
    else:
        cantidad = 5 + (i * 13) % 96
    ordenes.append({
        "pid": pid, "cantidad": cantidad,
        "dias": 1 + (i * 11) % 30 if tipo == "renta" else 1,
        "fecha": HOY - timedelta(days=(i * 17) % (DIAS_HISTORIA + 1)),
        "precio": float(round(lista * FACTORES_PRECIO[(i * 5) % 6])),
        "cliente": CLIENTES[i % 6],
        "marca": None,
    })

# ---- 2. descuentos autorizados: parecen error (precio bajo costo) pero son legítimos ----
for oid in DESCUENTO_AUTORIZADO:
    o = ordenes[oid - 1]
    lista = PRODUCTOS[o["pid"] - 1][3]
    o["precio"] = round(costo_vigente(o["pid"], o["fecha"]) * 0.96, 2)
    o["pct_descuento"] = round((1 - o["precio"] / lista) * 100, 1)   # % real contra el precio de lista
    o["marca"] = "descuento_autorizado"
    o["cliente"] = CLIENTES[oid % 6]                                  # repartidos: el cliente no delata el caso

# ---- 3. errores de facturación en órdenes elegidas a mano ----
for lista_ids, marca in ((SIN_FACTURA, "sin_factura"), (SUBFACTURADA, "subfacturada"),
                         (FACTURA_DUPLICADA, "factura_duplicada")):
    for oid in lista_ids:
        ordenes[oid - 1]["marca"] = marca

# ---- 4. stock insuficiente: se agregan al final (ids 1001-1008) ----
for k, (cantidad, atras) in enumerate(STOCK_INSUFICIENTE):
    ordenes.append({
        "pid": 7, "cantidad": cantidad, "dias": 7,
        "fecha": HOY - timedelta(days=atras),
        "precio": 400.0, "cliente": CLIENTES[k % 6], "marca": "stock_insuficiente",
    })

# ---- insertar órdenes, facturas y respuestas correctas ----
n_sub = 0
for o in ordenes:
    cur = con.execute(
        "INSERT INTO ordenes (fecha, cliente, producto_id, cantidad, dias, precio_unitario) "
        "VALUES (?,?,?,?,?,?)",
        (str(o["fecha"]), o["cliente"], o["pid"], o["cantidad"], o["dias"], o["precio"]))
    oid = cur.lastrowid
    total = round(o["precio"] * o["cantidad"] * o["dias"], 2)
    marca = o["marca"]

    if marca == "descuento_autorizado":
        con.execute("INSERT INTO descuentos_autorizados VALUES (?,?,?,?)",
                    (oid, o["pct_descuento"], "Gerencia comercial", "Contrato marco por volumen"))
        con.execute("INSERT INTO casos_legitimos VALUES (?,?)", (oid, marca))
    elif marca:
        con.execute("INSERT INTO errores_sembrados VALUES (?,?)", (oid, marca))

    if marca == "sin_factura":
        continue
    if marca == "subfacturada":
        total = round(total * FACTORES_SUBFACTURA[n_sub % 3], 2)
        n_sub += 1
    con.execute("INSERT INTO facturas (orden_id, monto_facturado) VALUES (?,?)", (oid, total))
    if marca == "factura_duplicada":
        con.execute("INSERT INTO facturas (orden_id, monto_facturado) VALUES (?,?)", (oid, total))

# ---- margen negativo REAL: precio bajo costo y SIN descuento autorizado ----
for oid, fecha, pid, precio in con.execute(
        "SELECT o.id, o.fecha, o.producto_id, o.precio_unitario FROM ordenes o "
        "WHERE o.id NOT IN (SELECT orden_id FROM descuentos_autorizados)").fetchall():
    if precio < costo_vigente(pid, fecha):
        con.execute("INSERT INTO errores_sembrados VALUES (?,?)", (oid, "margen_negativo"))

con.commit()
print("Base creada en", DB)
print("  rango de fechas:", *con.execute("SELECT MIN(fecha), MAX(fecha) FROM ordenes").fetchone())
print("  órdenes totales:", con.execute("SELECT COUNT(*) FROM ordenes").fetchone()[0])
for tipo, n in con.execute("SELECT tipo, COUNT(*) FROM errores_sembrados GROUP BY tipo"):
    print(f"  error {tipo}: {n}")
print("  casos legítimos (descuento autorizado):",
      con.execute("SELECT COUNT(*) FROM casos_legitimos").fetchone()[0])
con.close()