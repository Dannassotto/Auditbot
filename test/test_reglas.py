"""Pruebas de las reglas deterministas (engine/margins.py). No usan IA ni red."""
import pytest

from engine.margins import costo_vigente, revisar_orden


def banderas(con, oid):
    return revisar_orden(con, oid)["banderas"]


# ---------- orden limpia ----------
def test_orden_correcta_no_levanta_banderas(con, crear_orden):
    assert banderas(con, crear_orden()) == []


# ---------- margen negativo ----------
def test_margen_negativo_precio_menor_que_costo(con, crear_orden):
    oid = crear_orden(precio=30, costo=31)           # caso de la Orden 11 del README
    r = revisar_orden(con, oid)
    assert "margen_negativo" in r["banderas"]
    assert r["margen_pct"] < 0


def test_margen_positivo_no_marca(con, crear_orden):
    assert "margen_negativo" not in banderas(con, crear_orden(precio=31, costo=30))


def test_precio_igual_al_costo_no_marca(con, crear_orden):
    oid = crear_orden(precio=30, costo=30)           # borde: margen exactamente 0
    r = revisar_orden(con, oid)
    assert r["margen_pct"] == 0.0
    assert "margen_negativo" not in r["banderas"]


def test_calculo_del_margen_porcentual(con, crear_orden):
    assert revisar_orden(con, crear_orden(precio=100, costo=80))["margen_pct"] == 20.0


# ---------- costo vigente ----------
def test_costo_vigente_usa_el_mas_reciente_anterior_a_la_fecha(con, crear_orden):
    crear_orden(costo=50)                                         # vigente desde 2026-01-01
    con.execute("INSERT INTO costos VALUES (1, 60, '2026-08-01')")
    con.execute("INSERT INTO costos VALUES (1, 99, '2026-12-01')")  # futuro: se ignora
    assert costo_vigente(con, 1, "2026-09-01") == 60
    assert costo_vigente(con, 1, "2026-07-01") == 50


def test_costo_vigente_sin_historial_devuelve_none(con):
    assert costo_vigente(con, 999, "2026-09-01") is None


# ---------- inventario ----------
def test_stock_insuficiente(con, crear_orden):
    assert "stock_insuficiente" in banderas(con, crear_orden(cant=15, disponible=12))


def test_stock_justo_no_marca(con, crear_orden):
    assert "stock_insuficiente" not in banderas(con, crear_orden(cant=12, disponible=12))


def test_stock_cero_con_pedido_marca(con, crear_orden):
    assert "stock_insuficiente" in banderas(con, crear_orden(cant=1, disponible=0))


# ---------- sin factura ----------
def test_sin_factura(con, crear_orden):
    assert "sin_factura" in banderas(con, crear_orden(facturas=[]))


def test_con_factura_no_marca_sin_factura(con, crear_orden):
    assert "sin_factura" not in banderas(con, crear_orden())


# ---------- subfacturación (umbral: factura < 95 % de lo esperado) ----------
# esperado = precio * cant * dias = 100 * 2 * 3 = 600  -> umbral 570
@pytest.mark.parametrize("facturado, marca", [
    (560.00, True),     # claramente por debajo
    (569.99, True),     # justo bajo el umbral
    (570.00, False),    # exactamente 95 %: no marca (comparación estricta)
    (599.99, False),    # diferencia de centavos: tolerada
    (600.00, False),    # exacta
    (650.00, False),    # sobrefacturada: esta regla no la marca
])
def test_subfacturacion_umbral(con, crear_orden, facturado, marca):
    oid = crear_orden(precio=100, cant=2, dias=3, facturas=[facturado])
    assert ("subfacturada" in banderas(con, oid)) is marca


def test_monto_esperado_se_redondea_a_dos_decimales(con, crear_orden):
    r = revisar_orden(con, crear_orden(precio=33.333, cant=1, dias=3, facturas=[100.0]))
    assert r["monto_esperado"] == 100.0


# ---------- factura duplicada ----------
def test_factura_duplicada_mismos_montos(con, crear_orden):
    assert "factura_duplicada" in banderas(con, crear_orden(facturas=[100.0, 100.0]))


def test_tres_facturas_marca_duplicada(con, crear_orden):
    assert "factura_duplicada" in banderas(con, crear_orden(facturas=[100.0, 100.0, 100.0]))


def test_dos_facturas_con_montos_distintos_TAMBIEN_marca(con, crear_orden):
    """Documenta el comportamiento actual: la regla cuenta facturas (>1), no compara montos.
    Si decides que montos distintos (p. ej. pagos parciales) no son duplicado, cambia la
    regla y este test."""
    assert "factura_duplicada" in banderas(con, crear_orden(facturas=[40.0, 60.0]))


def test_con_varias_facturas_no_se_evalua_subfacturacion(con, crear_orden):
    """Por el 'elif', una orden duplicada y subfacturada solo recibe factura_duplicada."""
    oid = crear_orden(precio=100, facturas=[10.0, 10.0])
    b = banderas(con, oid)
    assert "factura_duplicada" in b and "subfacturada" not in b


# ---------- combinaciones ----------
def test_una_orden_puede_acumular_varias_banderas(con, crear_orden):
    oid = crear_orden(precio=30, costo=31, cant=15, disponible=12, facturas=[])
    assert set(banderas(con, oid)) == {"margen_negativo", "stock_insuficiente", "sin_factura"}


def test_resultado_incluye_los_campos_que_consume_el_agente(con, crear_orden):
    r = revisar_orden(con, crear_orden())
    for campo in ("orden_id", "fecha", "cliente", "costo_vigente", "margen_pct",
                  "stock_disponible", "monto_esperado", "facturas", "banderas"):
        assert campo in r


# ---------- fragilidades actuales (datos incompletos) ----------
def test_producto_sin_costo_registrado_hoy_lanza_TypeError(con, crear_orden):
    """Hoy una orden sin costo vigente rompe la revisión completa (costo=None)."""
    oid = crear_orden(costo=None)
    with pytest.raises(TypeError):
        revisar_orden(con, oid)


def test_precio_cero_hoy_lanza_ZeroDivisionError(con, crear_orden):
    oid = crear_orden(precio=0, facturas=[])
    with pytest.raises(ZeroDivisionError):
        revisar_orden(con, oid)