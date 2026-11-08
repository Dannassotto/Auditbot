"""Agente de auditoría sobre UCI Online Retail II (datos reales: sin costos, inventario ni facturas)."""
import json

from services.llm import conversar

MAX_PASOS = 6
MAX_CHARS = 3000   # tope por resultado de herramienta, para acotar los tokens

SISTEMA_UCI = """Eres AuditBot, auditor de ventas de una tienda mayorista en línea de regalos del Reino Unido (datos reales de 2009 a 2011).
Recibes UNA línea de factura que una regla automática marcó para revisión. Investiga con las herramientas y decide.

Importante:
- No hay costos, inventario ni facturas de cobro: solo líneas de venta (factura, producto, cantidad, fecha, precio, cliente). Una factura que empieza con "C" es una cancelación.
- Los datos empiezan el 2009-12-01. Menciona que la compra podría ser anterior al archivo solo si la línea está a menos de 3 meses de esa fecha; después es poco probable y no lo uses como excusa.
- Usa solo lo que devuelvan las herramientas. No inventes datos.
- Llama registrar_decision UNA vez. Acción "alertar" = merece revisión humana; "ignorar" = tiene una explicación normal.

Criterios sugeridos por bandera:
- precio_cero: ¿muestra, regalo o ajuste interno, o error de captura? Mira la descripción, si hay cliente, la cantidad y historial_precios. Con cliente y cantidad alta suele merecer alerta; sin cliente suele ser un ajuste.
- cantidad_negativa: salida que no es cancelación. Sin cliente suele ser un ajuste de inventario. Con cliente, busca con compras_cliente si hay una venta original.
- cancelacion_sin_compra: busca con compras_cliente (cliente, producto, hasta=fecha de la línea) una compra previa de ese producto. Si no aparece, alerta y menciona que la compra podría ser anterior al archivo.
- compra_repetida_mismo_dia: usa compras_cliente en ese día. Misma cantidad y precio en facturas distintas y un cliente que no suele repetir así = alerta. Un mayorista que repite pedidos con frecuencia = ignorar.
- linea_repetida_en_factura: usa lineas_de_factura. Repetir una línea suele ser normal; alerta solo si hay muchas repeticiones o el monto es grande.
- revision_de_rutina: sin sospecha específica. Revisa que la línea sea normal (historial_precios, compras_cliente). Ignora si todo es normal; alerta solo si encuentras algo concreto.

Formato:
- causa: UNA frase con el motivo y la cifra clave.
- evidencia: una línea por dato con el formato "dato: valor". Sin adornos."""


def _f(nombre, desc, props, req):
    return {"type": "function", "function": {
        "name": nombre, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": req}}}


TOOLS_UCI = [
    _f("historial_precios",
       "Estadísticas de precio de un producto (cantidad de ventas, mínimo, mediana, máximo), últimas ventas y líneas con precio cero.",
       {"producto": {"type": "string"}}, ["producto"]),
    _f("compras_cliente",
       "Líneas de un cliente (más recientes primero, máximo 20). Se puede filtrar por producto y por rango de fechas (formato AAAA-MM-DD).",
       {"cliente": {"type": "string"}, "producto": {"type": "string"},
        "desde": {"type": "string"}, "hasta": {"type": "string"}}, ["cliente"]),
    _f("lineas_de_factura",
       "Todas las líneas de una factura (máximo 30).",
       {"factura": {"type": "string"}}, ["factura"]),
    _f("registrar_decision",
       "Registra la decisión final sobre la línea. Llamar una sola vez.",
       {"accion": {"type": "string", "enum": ["alertar", "ignorar"]},
        "causa": {"type": "string"}, "evidencia": {"type": "string"}},
       ["accion", "causa", "evidencia"]),
]


def ejecutar_uci(con, nombre, args):
    if nombre == "historial_precios":
        p = str(args.get("producto", ""))
        precios = sorted(r[0] for r in con.execute(
            "SELECT precio FROM lineas WHERE producto=? AND cancelacion=0 AND cantidad>0 AND precio>0", (p,)))
        ceros = con.execute("SELECT COUNT(*) FROM lineas WHERE producto=? AND precio<=0", (p,)).fetchone()[0]
        ultimas = con.execute(
            "SELECT fecha, precio, cantidad FROM lineas WHERE producto=? AND cancelacion=0 AND cantidad>0 "
            "ORDER BY fecha DESC LIMIT 5", (p,)).fetchall()
        desc = con.execute("SELECT descripcion FROM lineas WHERE producto=? AND descripcion IS NOT NULL LIMIT 1", (p,)).fetchone()
        out = {"producto": p, "descripcion": desc[0] if desc else None, "ventas": len(precios), "lineas_precio_cero": ceros}
        if precios:
            out.update(minimo=precios[0], mediana=precios[len(precios) // 2], maximo=precios[-1],
                       ultimas_ventas=[{"fecha": u[0], "precio": u[1], "cantidad": u[2]} for u in ultimas])
    elif nombre == "compras_cliente":
        c = str(args.get("cliente") or "")
        if not c:
            return json.dumps({"error": "cliente vacío: la línea no tiene cliente"})
        sql = "SELECT factura, producto, cantidad, fecha, precio, cancelacion FROM lineas WHERE cliente=?"
        par = [c]
        if args.get("producto"):
            sql += " AND producto=?"; par.append(str(args["producto"]))
        if args.get("desde"):
            sql += " AND fecha>=?"; par.append(str(args["desde"]))
        if args.get("hasta"):
            h = str(args["hasta"])
            sql += " AND fecha<=?"; par.append(h + " 23:59:59" if len(h) == 10 else h)
        sql += " ORDER BY fecha DESC LIMIT 20"
        filas = con.execute(sql, par).fetchall()
        out = {"cliente": c, "lineas": [dict(zip(("factura", "producto", "cantidad", "fecha", "precio", "cancelacion"), r)) for r in filas]}
    elif nombre == "lineas_de_factura":
        filas = con.execute(
            "SELECT producto, descripcion, cantidad, precio, cliente FROM lineas WHERE factura=? LIMIT 30",
            (str(args.get("factura", "")),)).fetchall()
        out = {"factura": args.get("factura"), "lineas": [dict(zip(("producto", "descripcion", "cantidad", "precio", "cliente"), r)) for r in filas]}
    else:
        out = {"error": f"herramienta desconocida: {nombre}"}
    return json.dumps(out, ensure_ascii=False, default=str)[:MAX_CHARS]


def auditar_linea(con, fila, bandera, orden_id=None, cancelar=None):
    """Devuelve (decision, nota). decision es None si Grok no llegó a decidir; nota dice por qué."""
    caso = {**fila, "bandera": bandera}
    mensajes = [{"role": "system", "content": SISTEMA_UCI},
                {"role": "user", "content": "Línea a investigar:\n" + json.dumps(caso, ensure_ascii=False, default=str)}]
    for _ in range(MAX_PASOS):
        if cancelar is not None and cancelar.is_set():
            raise RuntimeError("cancelado")
        msg = conversar(mensajes, TOOLS_UCI, max_tokens=800, orden_id=orden_id)
        if not msg.tool_calls:
            return None, "terminó sin registrar decisión: " + (msg.content or "")[:150]
        mensajes.append({
            "role": "assistant", "content": msg.content or "",
            "tool_calls": [{"id": tc.id, "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                           for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except ValueError:
                args = {}
            if tc.function.name == "registrar_decision":
                if args.get("accion") not in ("alertar", "ignorar"):
                    return None, f"acción inválida: {args.get('accion')!r}"
                return {"linea_id": fila["id"], "accion": args["accion"],
                        "causa": args.get("causa", ""), "evidencia": args.get("evidencia", "")}, None
            mensajes.append({"role": "tool", "tool_call_id": tc.id,
                             "content": ejecutar_uci(con, tc.function.name, args)})
    return None, f"agotó los {MAX_PASOS} pasos sin decidir"