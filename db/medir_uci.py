"""Mide cuántas líneas de UCI levantan cada regla. Solo lectura sobre data/uci.db."""
import sqlite3
from collections import defaultdict
from statistics import median

con = sqlite3.connect("data/uci.db")
q = lambda s: con.execute(s).fetchone()[0]
PRODUCTO_REAL = "producto GLOB '[0-9][0-9][0-9][0-9][0-9]*'"   # excluye POST, M, D, BANK CHARGES, ADJUST...

print("Creando índice (una sola vez)...")
con.execute("CREATE INDEX IF NOT EXISTS ix_cli_prod ON lineas(cliente, producto)")
con.commit()

print("\n=== Reglas simples ===")
print("Códigos que no son producto (POST, M, D...):", q(f"SELECT COUNT(*) FROM lineas WHERE NOT ({PRODUCTO_REAL})"))
print("precio_cero (producto real):                 ", q(f"SELECT COUNT(*) FROM lineas WHERE precio<=0 AND {PRODUCTO_REAL}"))
print("cantidad_negativa sin ser cancelación:       ", q(f"SELECT COUNT(*) FROM lineas WHERE cantidad<0 AND cancelacion=0 AND {PRODUCTO_REAL}"))
print("cancelaciones (producto real):               ", q(f"SELECT COUNT(*) FROM lineas WHERE cancelacion=1 AND {PRODUCTO_REAL}"))

sin_previa = q(f"""SELECT COUNT(*) FROM lineas c WHERE c.cancelacion=1 AND c.cliente IS NOT NULL AND {PRODUCTO_REAL.replace('producto','c.producto')}
    AND NOT EXISTS (SELECT 1 FROM lineas o WHERE o.cliente=c.cliente AND o.producto=c.producto
                    AND o.cancelacion=0 AND o.fecha<c.fecha)""")
print("cancelación SIN compra previa del cliente:   ", sin_previa)

dup = q(f"""SELECT COUNT(*) FROM (SELECT 1 FROM lineas
    WHERE cancelacion=0 AND cliente IS NOT NULL AND cantidad>0 AND precio>0 AND {PRODUCTO_REAL}
    GROUP BY cliente, producto, cantidad, precio, substr(fecha,1,10), factura HAVING COUNT(*)>1)""")
print("línea repetida dentro de la misma factura:   ", dup)
dup2 = q(f"""SELECT COUNT(*) FROM (SELECT 1 FROM lineas
    WHERE cancelacion=0 AND cliente IS NOT NULL AND cantidad>0 AND precio>0 AND {PRODUCTO_REAL}
    GROUP BY cliente, producto, cantidad, precio, substr(fecha,1,10) HAVING COUNT(DISTINCT factura)>1)""")
print("misma compra en facturas distintas, mismo día:", dup2)

print("\n=== Atípicos estadísticos (z robusto >= 3.5, productos con >= 8 ventas) ===")
por_prod = defaultdict(list)
for pid, lid, cant, precio in con.execute(
        f"SELECT producto, id, cantidad, precio FROM lineas WHERE cancelacion=0 AND cantidad>0 AND precio>0 AND {PRODUCTO_REAL}"):
    por_prod[pid].append((lid, cant, precio))


def z(v, med, mad, piso_rel, piso_abs=1e-9):
    return (v - med) / max(1.4826 * mad, piso_rel * abs(med), piso_abs)


n_precio = n_cant = 0
for pid, g in por_prod.items():
    if len(g) < 8:
        continue
    mp = median(x[2] for x in g); dp = median(abs(x[2] - mp) for x in g)
    mc = median(x[1] for x in g); dc = median(abs(x[1] - mc) for x in g)
    for _, c, p in g:
        if abs(z(p, mp, dp, 0.05)) >= 3.5:
            n_precio += 1
        if z(c, mc, dc, 0.25, 1.0) >= 3.5:
            n_cant += 1
print("precio_atipico:  ", n_precio)
print("cantidad_atipica:", n_cant)
print("\nListo.")