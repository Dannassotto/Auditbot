import json

TOOLS = [
    {"type": "function", "function": {
        "name": "historial_costos",
        "description": "Historial de costos de un producto (cuándo cambió su costo).",
        "parameters": {"type": "object",
                       "properties": {"producto_id": {"type": "integer"}},
                       "required": ["producto_id"]}}},
    {"type": "function", "function": {
        "name": "ordenes_similares",
        "description": "Órdenes del mismo producto, en los 30 días previos a la fecha de la orden dada, con precio menor al costo vigente y sin descuento autorizado.",
        "parameters": {"type": "object",
                       "properties": {"orden_id": {"type": "integer"}},
                       "required": ["orden_id"]}}},
    {"type": "function", "function": {
        "name": "ver_inventario",
        "description": "Stock disponible de un producto.",
        "parameters": {"type": "object",
                       "properties": {"producto_id": {"type": "integer"}},
                       "required": ["producto_id"]}}},
    {"type": "function", "function": {
        "name": "ver_facturas",
        "description": "Lista de montos facturados de una orden (vacía si no tiene; más de uno si está duplicada).",
        "parameters": {"type": "object",
                       "properties": {"orden_id": {"type": "integer"}},
                       "required": ["orden_id"]}}},
    {"type": "function", "function": {
        "name": "consultar_descuento",
        "description": "Revisa si una orden tiene un descuento autorizado (explica un precio bajo).",
        "parameters": {"type": "object",
                       "properties": {"orden_id": {"type": "integer"}},
                       "required": ["orden_id"]}}},
    {"type": "function", "function": {
        "name": "registrar_decision",
        "description": "Registra la decisión final sobre la orden. Llamar una sola vez.",
        "parameters": {"type": "object",
                       "properties": {
                           "orden_id": {"type": "integer"},
                           "accion": {"type": "string",
                                      "enum": ["alertar", "bloquear", "ajustar", "ignorar"]},
                           "causa": {"type": "string"},
                           "evidencia": {"type": "string"},
                           "perdida_estimada_mxn": {"type": "number"}},
                       "required": ["orden_id", "accion", "causa", "evidencia"]}}},
]


def ejecutar(con, nombre, args):
    if nombre == "historial_costos":
        rows = con.execute(
            "SELECT costo_unitario, vigente_desde FROM costos WHERE producto_id=? "
            "ORDER BY vigente_desde", (args["producto_id"],)).fetchall()
        return json.dumps([{"costo": r[0], "desde": r[1]} for r in rows])
    if nombre == "ordenes_similares":
        # Ventana anclada a la fecha de LA ORDEN (no a la fecha de hoy), para que el resultado no cambie con el tiempo.
        rows = con.execute(
            "SELECT o.id, o.fecha, o.precio_unitario FROM ordenes o, "
            "(SELECT producto_id AS pid, fecha AS f, id AS oid FROM ordenes WHERE id=?) ref "
            "WHERE o.producto_id=ref.pid AND o.id<>ref.oid "
            "AND o.fecha BETWEEN date(ref.f,'-30 day') AND ref.f "
            "AND o.precio_unitario < (SELECT costo_unitario FROM costos c "
            "WHERE c.producto_id=o.producto_id AND c.vigente_desde<=o.fecha "
            "ORDER BY c.vigente_desde DESC LIMIT 1) "
            "AND o.id NOT IN (SELECT orden_id FROM descuentos_autorizados)",
            (args["orden_id"],)).fetchall()
        return json.dumps([{"orden": r[0], "fecha": r[1], "precio": r[2]} for r in rows])
    if nombre == "ver_inventario":
        r = con.execute("SELECT disponible FROM inventario WHERE producto_id=?",
                        (args["producto_id"],)).fetchone()
        return json.dumps({"disponible": r[0] if r else None})
    if nombre == "ver_facturas":
        rows = con.execute("SELECT monto_facturado FROM facturas WHERE orden_id=?",
                           (args["orden_id"],)).fetchall()
        return json.dumps({"facturas": [r[0] for r in rows]})
    if nombre == "consultar_descuento":
        r = con.execute(
            "SELECT porcentaje, autorizado_por, motivo FROM descuentos_autorizados "
            "WHERE orden_id=?", (args["orden_id"],)).fetchone()
        return json.dumps({"descuento": None if r is None else
                           {"porcentaje": r[0], "autorizado_por": r[1], "motivo": r[2]}})
    return json.dumps({"error": f"herramienta desconocida: {nombre}"})