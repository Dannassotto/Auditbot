"""Anomalías que NINGUNA de las cinco reglas fijas detecta (ni margen < 0 ni subfacturación)."""
import pytest

from engine.anomalias import detectar_anomalias, muestra_control
from engine.margins import revisar_orden

NORMALES = list(range(1, 41))


@pytest.fixture
def datos(con, crear_orden):
    precios, cants = [98, 99, 100, 101, 102], [1, 2, 3]
    for i in NORMALES:
        crear_orden(id=i, fecha=f"2026-08-{(i - 1) % 28 + 1:02d}" if i <= 28 else f"2026-09-{i - 28:02d}",
                    cliente=f"C{i % 8}", precio=precios[i % 5], cant=cants[i % 3],
                    costo=60, disponible=1000)
    crear_orden(id=100, cliente="Otro", precio=60, costo=60, disponible=1000)            # margen ~0, no negativo
    crear_orden(id=101, cliente="Otro", cant=50, precio=100, costo=60, disponible=1000)  # cantidad enorme
    crear_orden(id=102, cliente="Otro", precio=100, costo=60, disponible=1000, facturas=[120.0])  # sobrefacturada
    crear_orden(id=103, fecha="2026-09-10", cliente="Dup", cant=2, precio=100, costo=60, disponible=1000)
    crear_orden(id=104, fecha="2026-09-11", cliente="Dup", cant=2, precio=100, costo=60, disponible=1000)
    for k in range(6):                                                                    # cliente con precios bajos
        crear_orden(id=110 + k, fecha=f"2026-09-{12 + k}", cliente="Cliente Z", cant=1,
                    precio=82, costo=60, disponible=1000)
    crear_orden(id=120, pid=2, cliente="Otro", costo=None, disponible=10)                 # sin costo
    crear_orden(id=121, pid=3, cliente="Otro", costo=50, disponible=None)                 # sin inventario
    return detectar_anomalias(con)


def tipos(datos, oid):
    return {h["tipo"] for h in datos.get(oid, [])}


def test_las_ordenes_normales_no_se_marcan(datos):
    marcadas = [i for i in NORMALES if i in datos]
    assert marcadas == []


def test_precio_atipico_con_margen_positivo(datos, con):
    assert "precio_atipico" in tipos(datos, 100)
    assert "margen_bajo_atipico" in tipos(datos, 100)
    assert revisar_orden(con, 100)["banderas"] == []      # las reglas fijas no lo ven


def test_cantidad_atipica(datos):
    assert "cantidad_atipica" in tipos(datos, 101)


def test_sobrefacturacion(datos, con):
    assert "sobrefacturada" in tipos(datos, 102)
    assert revisar_orden(con, 102)["banderas"] == []


def test_orden_duplicada_en_ordenes_distintas(datos, con):
    assert "posible_orden_duplicada" in tipos(datos, 104)
    assert "posible_orden_duplicada" not in tipos(datos, 103)   # solo se marca la repetida
    assert revisar_orden(con, 104)["banderas"] == []


def test_cliente_con_precios_sistematicamente_bajos(datos):
    assert all("cliente_precio_bajo_sistematico" in tipos(datos, 110 + k) for k in range(6))


def test_datos_incompletos_se_declaran_no_se_dan_por_buenos(datos):
    assert "datos_incompletos" in tipos(datos, 120)
    assert "datos_incompletos" in tipos(datos, 121)


def test_muestra_control_es_reproducible_y_acotada():
    ids = list(range(1, 501))
    a, b = muestra_control(ids, 0.04, 7), muestra_control(ids, 0.04, 7)
    assert a == b and len(a) == 20 and set(a) <= set(ids)
    assert muestra_control([]) == [] and len(muestra_control([1])) == 1