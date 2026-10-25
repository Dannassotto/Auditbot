def costo_vigente(con, producto_id, fecha):
    row = con.execute(
        "SELECT costo_unitario FROM costos WHERE producto_id=? AND vigente_desde<=? "
        "ORDER BY vigente_desde DESC LIMIT 1", (producto_id, fecha)).fetchone()
    return row[0] if row else None


def revisar_orden(con, orden_id):
    """Revisión determinista (sin IA): devuelve la orden y las banderas que levanta."""
    oid, fecha, cliente, pid, cant, dias, precio = con.execute(
        "SELECT id, fecha, cliente, producto_id, cantidad, dias, precio_unitario "
        "FROM ordenes WHERE id=?", (orden_id,)).fetchone()
    banderas = []

    costo = costo_vigente(con, pid, fecha)
    margen = round((precio - costo) / precio * 100, 1)
    if precio < costo:
        banderas.append("margen_negativo")

    disponible = con.execute(
        "SELECT disponible FROM inventario WHERE producto_id=?", (pid,)).fetchone()[0]
    if cant > disponible:
        banderas.append("stock_insuficiente")

    facturas = [r[0] for r in con.execute(
        "SELECT monto_facturado FROM facturas WHERE orden_id=?", (oid,))]
    esperado = round(precio * cant * dias, 2)
    if not facturas:
        banderas.append("sin_factura")
    elif len(facturas) > 1:
        banderas.append("factura_duplicada")
    elif facturas[0] < esperado * 0.95:
        banderas.append("subfacturada")

    return {
        "orden_id": oid, "fecha": fecha, "cliente": cliente, "producto_id": pid,
        "cantidad": cant, "dias": dias, "precio_unitario": precio,
        "costo_vigente": costo, "margen_pct": margen, "stock_disponible": disponible,
        "monto_esperado": esperado, "facturas": facturas, "banderas": banderas,
    }