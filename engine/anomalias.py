"""Detección estadística de anomalías: encuentra lo que las reglas fijas NO conocen.

Compara cada orden contra el comportamiento normal de la propia empresa (sin IA, sin tokens).
Salida: {orden_id: [{"tipo", "score", "detalle"}, ...]} para que Grok investigue esas órdenes.
"""
import random
from collections import defaultdict
from datetime import date
from statistics import median

from engine.margins import costo_vigente

Z_MIN = 3.5            # umbral de z-score robusto (mediana y MAD, aguanta valores extremos)
MIN_HISTORIAL = 8      # órdenes mínimas de un producto para tener una "normalidad" confiable
DIAS_DUPLICADO = 3     # misma compra repetida en esta ventana = posible duplicado
RATIO_CLIENTE = 0.85   # un cliente cuyo precio mediano está bajo el 85 % del normal


def _z(valor, med, mad, piso_rel, piso_abs=1e-9):
    escala = max(1.4826 * mad, piso_rel * abs(med), piso_abs)
    return (valor - med) / escala


def _base(valores):
    med = median(valores)
    return med, median(abs(v - med) for v in valores)


def _cargar(con):
    inv = {r[0] for r in con.execute("SELECT producto_id FROM inventario")}
    fact = defaultdict(list)
    for oid, monto in con.execute("SELECT orden_id, monto_facturado FROM facturas"):
        fact[oid].append(monto)
    filas = []
    for oid, fecha, cli, pid, cant, dias, precio in con.execute(
            "SELECT id, fecha, cliente, producto_id, cantidad, dias, precio_unitario "
            "FROM ordenes ORDER BY id").fetchall():
        costo = costo_vigente(con, pid, fecha)
        filas.append({"id": oid, "fecha": fecha, "cliente": cli, "pid": pid, "cant": cant,
                      "dias": dias, "precio": precio, "costo": costo,
                      "tiene_inv": pid in inv, "facturas": fact.get(oid, [])})
    return filas


def detectar_anomalias(con):
    filas = _cargar(con)
    hallazgos = defaultdict(list)

    def marcar(f, tipo, score, detalle):
        hallazgos[f["id"]].append({"tipo": tipo, "score": round(score, 2), "detalle": detalle})

    # 1) Datos incompletos: no se pueden revisar; se declaran, no se dan por buenos
    validas = []
    for f in filas:
        faltan = []
        if f["costo"] is None: faltan.append("costo vigente")
        if not f["tiene_inv"]: faltan.append("inventario")
        if f["precio"] <= 0 or f["cant"] <= 0 or f["dias"] <= 0:
            faltan.append("precio, cantidad o días no positivos")
        if faltan:
            marcar(f, "datos_incompletos", 0, "No se pudo revisar: falta " + ", ".join(faltan))
        else:
            validas.append(f)

    # 2) Normalidad por producto (precio, cantidad, margen)
    por_prod = defaultdict(list)
    for f in validas:
        por_prod[f["pid"]].append(f)
    mediana_precio = {}
    for pid, grupo in por_prod.items():
        if len(grupo) < MIN_HISTORIAL:
            continue
        mp, dp = _base([g["precio"] for g in grupo])
        mc, dc = _base([g["cant"] for g in grupo])
        mm, dm = _base([(g["precio"] - g["costo"]) / g["precio"] for g in grupo])
        mediana_precio[pid] = mp
        for g in grupo:
            zp = _z(g["precio"], mp, dp, 0.05)
            if abs(zp) >= Z_MIN:
                marcar(g, "precio_atipico", abs(zp),
                       f"Precio {g['precio']:.2f} vs mediana del producto {mp:.2f}")
            zc = _z(g["cant"], mc, dc, 0.25, 1.0)
            if zc >= Z_MIN:
                marcar(g, "cantidad_atipica", zc,
                       f"Cantidad {g['cant']} vs mediana del producto {mc:g}")
            margen = (g["precio"] - g["costo"]) / g["precio"]
            zm = _z(margen, mm, dm, 0.05)
            if zm <= -Z_MIN and margen >= 0:    # el negativo ya lo cubre la regla fija
                marcar(g, "margen_bajo_atipico", abs(zm),
                       f"Margen {margen:.1%} vs mediana del producto {mm:.1%}")

    # 3) Clientes con precios sistemáticamente bajos (patrón, no un solo dato)
    por_cli = defaultdict(list)
    for f in validas:
        if f["pid"] in mediana_precio:
            por_cli[f["cliente"]].append((f, f["precio"] / mediana_precio[f["pid"]]))
    for cli, pares in por_cli.items():
        if len(pares) >= 5:
            mr = median(r for _, r in pares)
            if mr < RATIO_CLIENTE:
                for f, r in pares:
                    if r < 0.95:
                        marcar(f, "cliente_precio_bajo_sistematico", (1 - mr) * 100,
                               f"{cli}: precio mediano al {mr:.0%} del normal en {len(pares)} órdenes")

    # 4) Sobrefacturación (las reglas solo vigilan subfacturación)
    for f in validas:
        if len(f["facturas"]) == 1:
            esperado = round(f["precio"] * f["cant"] * f["dias"], 2)
            if f["facturas"][0] > esperado * 1.05:
                marcar(f, "sobrefacturada", (f["facturas"][0] / esperado - 1) * 100,
                       f"Facturado {f['facturas'][0]:.2f} vs esperado {esperado:.2f}")

    # 5) Compras repetidas en órdenes distintas (duplicado que no es de factura)
    grupos = defaultdict(list)  
    for f in validas:
        grupos[(f["cliente"], f["pid"], f["cant"], f["dias"], f["precio"])].append(f)
    for g in grupos.values():
        g.sort(key=lambda x: x["fecha"])
        for a, b in zip(g, g[1:]):
            dif = (date.fromisoformat(b["fecha"][:10]) - date.fromisoformat(a["fecha"][:10])).days
            if dif <= DIAS_DUPLICADO:
                marcar(b, "posible_orden_duplicada", 1,
                       f"Igual a la orden {a['id']} ({dif} días antes): mismo cliente, producto, cantidad y precio")
    return dict(hallazgos)


def muestra_control(ids_sin_hallazgos, fraccion=0.04, semilla=42):
    """Órdenes limpias al azar (reproducible) que también irán a Grok: mide qué se escapa."""
    ids = sorted(ids_sin_hallazgos)
    if not ids:
        return []
    return sorted(random.Random(semilla).sample(ids, max(1, round(len(ids) * fraccion))))