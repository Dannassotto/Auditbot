def costo_vigente(con, producto_id, fecha):
    row = con.execute(
        "SELECT costo_unitario FROM costos WHERE producto_id=? AND vigente_desde<=? "
        "ORDER BY vigente_desde DESC LIMIT 1", (producto_id, fecha)).fetchone()
    return row[0] if row else None


def revisar_orden(con, orden_id):
    """Revisión determinista (sin IA): devuelve la orden y las banderas que levanta.

    Si faltan datos (costo, inventario, precio válido) NO se rompe: se declara con la bandera
    'datos_incompletos' y la lista 'datos_faltantes', y el resto de reglas sigue corriendo.
    """
    oid, fecha, cliente, pid, cant, dias, precio = con.execute(
        "SELECT id, fecha, cliente, producto_id, cantidad, dias, precio_unitario "
        "FROM ordenes WHERE id=?", (orden_id,)).fetchone()
    banderas, faltantes = [], []

    costo = costo_vigente(con, pid, fecha)
    if costo is None:
        faltantes.append("costo vigente")
    if precio is None or precio <= 0:
        faltantes.append("precio válido")

    margen = None
    if costo is not None and precio is not None and precio > 0:
        margen = round((precio - costo) / precio * 100, 1)
        if precio < costo:
            banderas.append("margen_negativo")

    fila = con.execute(
        "SELECT disponible FROM inventario WHERE producto_id=?", (pid,)).fetchone()
    disponible = fila[0] if fila else None
    if disponible is None:
        faltantes.append("inventario")
    elif cant > disponible:
        banderas.append("stock_insuficiente")

    facturas = [r[0] for r in con.execute(
        "SELECT monto_facturado FROM facturas WHERE orden_id=?", (oid,))]
    esperado = round((precio or 0) * cant * dias, 2)
    if not facturas:
        banderas.append("sin_factura")
    elif len(facturas) > 1:
        banderas.append("factura_duplicada")
    elif facturas[0] < esperado * 0.95:
        banderas.append("subfacturada")

    if faltantes:
        banderas.append("datos_incompletos")

    return {
        "orden_id": oid, "fecha": fecha, "cliente": cliente, "producto_id": pid,
        "cantidad": cant, "dias": dias, "precio_unitario": precio,
        "costo_vigente": costo, "margen_pct": margen, "stock_disponible": disponible,
        "monto_esperado": esperado, "facturas": facturas, "banderas": banderas,
        "datos_faltantes": faltantes,
    }