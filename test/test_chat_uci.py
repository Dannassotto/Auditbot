"""Modo datos reales del chat: selección, bitácora, errores claros y protección durante la corrida larga."""
import json
import sqlite3
import threading

import pytest

import servidor
from services import chat_uci
from services.llm import ErrorLLM


@pytest.fixture
def uci(tmp_path):
    ruta = str(tmp_path / "uci.db")
    c = sqlite3.connect(ruta)
    c.executescript("""
        CREATE TABLE lineas (id INTEGER PRIMARY KEY, factura TEXT, producto TEXT, descripcion TEXT, cantidad INTEGER,
                             fecha TEXT, precio REAL, cliente TEXT, pais TEXT, cancelacion INTEGER);
        CREATE TABLE cola (pos INTEGER PRIMARY KEY, linea_id INTEGER, regla TEXT, es_control INTEGER);
    """)
    reglas = ["precio_cero", "precio_cero", "cancelacion_sin_compra", "cancelacion_sin_compra", "control", "control"]
    for i, r in enumerate(reglas, start=1):
        c.execute("INSERT INTO lineas VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (100 + i, f"{50000 + i}", "P", "Taza", 2,
                   "2010-07-01 10:00:00" if i <= 3 else "2010-08-15 09:00:00", 1.5,
                   None if i == 1 else "15796", "UK", 0))
        c.execute("INSERT INTO cola VALUES (?,?,?,?)", (i, 100 + i, r, int(r == "control")))
    c.commit(); c.close()
    return ruta


def auditor(accion="alertar", llamadas=None):
    def f(con, fila, bandera, orden_id=None):
        if llamadas is not None:
            llamadas.append((fila["id"], bandera))
        return {"accion": accion, "causa": f"causa {fila['id']}", "evidencia": "dato: 1"}, ""
    return f


def correr(uci, tmp_path, texto="revisa 4 lineas de UCI", espera=0.0, **kw):
    return chat_uci.ejecutar_uci(texto, threading.Event(), uci, str(tmp_path / "chat"), espera=espera, **kw)


def test_detecta_cuando_piden_datos_reales():
    for t in ("revisa 5 líneas de UCI", "usa los datos reales", "Online Retail"):
        assert chat_uci.es_pedido_uci(t), t
    for t in ("revisa las órdenes de agosto", "orden 11"):
        assert not chat_uci.es_pedido_uci(t), t


def test_cantidad_con_limite():
    assert chat_uci.cantidad_pedida("revisa 5 líneas de uci", 3, 10) == (5, 5)
    assert chat_uci.cantidad_pedida("revisa tres lineas uci", 3, 10) == (3, 3)
    assert chat_uci.cantidad_pedida("revisa 50 lineas uci", 3, 10) == (10, 50)
    assert chat_uci.cantidad_pedida("revisa uci", 3, 10) == (3, None)


def test_reparte_entre_reglas_oculta_el_control_y_escribe_la_bitacora(uci, tmp_path, monkeypatch):
    llamadas = []
    monkeypatch.setattr(chat_uci, "_cargar_auditor", lambda: auditor(llamadas=llamadas))
    r = correr(uci, tmp_path)
    assert len(r["ordenes"]) == 4 and r["cola"] == 6 and r["pendientes"] == 6
    reglas = {b for _, b in llamadas}
    assert reglas == {"precio_cero", "cancelacion_sin_compra", "revision_de_rutina"}   # Grok no ve "control"
    lineas = [json.loads(l) for l in open(tmp_path / "chat" / "ordenes.jsonl", encoding="utf-8")]
    assert len(lineas) == 4 and all(o["resultado"] == "ok" and o["origen"] == "chat" and "pos" in o for o in lineas)


def test_tarjeta_con_la_forma_que_dibuja_la_interfaz(uci, tmp_path, monkeypatch):
    monkeypatch.setattr(chat_uci, "_cargar_auditor", lambda: auditor())
    t = {o["orden_id"]: o for o in correr(uci, tmp_path, "revisa 6 lineas uci", limite_max=10)["ordenes"]}
    assert t[101]["cliente"] == "Sin cliente" and t[102]["cliente"] == "Cliente 15796"
    assert t[101]["banderas"] == ["Precio cero"] and t[105]["banderas"] == ["Revisión de rutina"]
    assert t[101]["fecha"] == "2010-07-01" and t[101]["perdida_mxn"] == 0.0 and t[101]["margen_pct"] is None
    assert t[101]["accion"] == "alertar" and t[101]["causa"] == "causa 101"


def test_no_repite_lo_ya_revisado(uci, tmp_path, monkeypatch):
    llamadas = []
    monkeypatch.setattr(chat_uci, "_cargar_auditor", lambda: auditor(llamadas=llamadas))
    correr(uci, tmp_path, "revisa 4 lineas uci")
    llamadas.clear()
    r = correr(uci, tmp_path, "revisa 4 lineas uci")
    assert len(r["ordenes"]) == 2 and r["hechas_antes"] == 4 and len({i for i, _ in llamadas}) == 2
    assert correr(uci, tmp_path)["ordenes"] == [] and correr(uci, tmp_path)["pendientes"] == 0


def test_error_temporal_se_muestra_claro_y_se_reintenta_despues(uci, tmp_path, monkeypatch):
    def falla(con, fila, bandera, orden_id=None):
        raise ErrorLLM({"codigo": "SIN_CONEXION", "mensaje": "No hay conexión con el servicio.", "reintentable": True})
    monkeypatch.setattr(chat_uci, "_cargar_auditor", lambda: falla)
    r = correr(uci, tmp_path, "revisa 2 lineas uci")
    assert all("No hay conexión" in o["causa"] and o["accion"] == "revisar" for o in r["ordenes"])
    monkeypatch.setattr(chat_uci, "_cargar_auditor", lambda: auditor())
    assert correr(uci, tmp_path, "revisa 2 lineas uci")["hechas_antes"] == 0     # los errores no cuentan como hechos


def test_llave_invalida_y_tope_paran_de_una_vez(uci, tmp_path, monkeypatch):
    for codigo, clave in (("LLAVE_INVALIDA", "fatal"), ("TOPE_GASTO", "tope")):
        llamadas = []

        def falla(con, fila, bandera, orden_id=None, c=codigo, ll=llamadas):
            ll.append(1)
            raise ErrorLLM({"codigo": c, "mensaje": "x", "reintentable": False})
        monkeypatch.setattr(chat_uci, "_cargar_auditor", lambda f=falla: f)
        r = chat_uci.ejecutar_uci("revisa 4 lineas uci", threading.Event(), uci, str(tmp_path / codigo), espera=0)
        assert r[clave] and len(llamadas) == 1 and r["ordenes"] == []


def test_detener_en_la_ventana_inicial_no_envia_nada(uci, tmp_path, monkeypatch):
    llamadas = []
    monkeypatch.setattr(chat_uci, "_cargar_auditor", lambda: auditor(llamadas=llamadas))
    ev = threading.Event(); ev.set()
    r = chat_uci.ejecutar_uci("revisa 3 lineas uci", ev, uci, str(tmp_path / "c"), espera=0.5)
    assert r["cancelado"] and llamadas == []


# ---------- integración con servidor.py ----------
@pytest.fixture
def srv(monkeypatch, uci, tmp_path):
    monkeypatch.setattr(servidor, "RUTA_UCI", uci)
    monkeypatch.setattr(servidor, "DIR_CHAT_UCI", str(tmp_path / "chat"))
    monkeypatch.setattr(servidor, "ESPERA_ANTES_DE_ENVIAR", 0)
    monkeypatch.chdir(tmp_path)                      # sin corrida: estado_corrida lee logs/corrida de aquí
    servidor.cancelar_evento.clear()
    servidor.progreso.update(hechas=0, total=0, enviado=False)
    return servidor


def test_el_chat_enruta_a_datos_reales_y_arma_la_respuesta(srv, monkeypatch):
    monkeypatch.setattr(chat_uci, "_cargar_auditor", lambda: auditor())
    r = srv.ejecutar_auditoria("revisa 3 líneas de UCI")
    assert len(r["resultado"]["ordenes"]) == 3 and "Datos reales" in r["resultado"]["resumen"]
    assert "libras" in r["resultado"]["resumen"] and "sin gastar" not in r["resultado"]["resumen"]


def test_con_toda_la_cola_revisada_muestra_las_decisiones_guardadas_sin_gastar(srv, monkeypatch):
    llamadas = []
    monkeypatch.setattr(chat_uci, "_cargar_auditor", lambda: auditor(llamadas=llamadas))
    srv.ejecutar_auditoria("revisa 10 líneas de UCI")
    llamadas.clear()
    r = srv.ejecutar_auditoria("revisa 3 líneas de UCI")
    assert llamadas == [] and "sin gastar créditos" in r["resultado"]["resumen"]


def test_sin_la_base_real_da_un_mensaje_claro(srv, monkeypatch, tmp_path):
    monkeypatch.setattr(srv, "RUTA_UCI", str(tmp_path / "no_existe.db"))
    assert "data/uci.db" in srv.ejecutar_auditoria_uci("revisa uci")["mensaje"]


def test_mientras_corre_la_corrida_larga_el_chat_no_gasta_la_llave(srv, monkeypatch, tmp_path):
    d = tmp_path / "logs" / "corrida"; d.mkdir(parents=True)
    (d / "ordenes.jsonl").write_text(json.dumps({"pos": 1, "hora": "x", "resultado": "ok"}) + "\n")
    (d / "eventos.jsonl").write_text(json.dumps({"evento": "inicio", "pendientes": 9}) + "\n")
    llamadas = []
    monkeypatch.setattr(chat_uci, "_cargar_auditor", lambda: auditor(llamadas=llamadas))
    r = srv.ejecutar_auditoria("revisa 3 líneas de UCI")
    assert "corrida larga en curso" in r["mensaje"] and llamadas == []
    monkeypatch.setenv("AUDIBOT_PERMITIR_CHAT", "1")
    assert srv.ejecutar_auditoria("revisa 3 líneas de UCI")["resultado"] is not None


def test_la_respuesta_muestra_lo_que_costo_la_consulta(srv, monkeypatch):
    """El costo sale de llm.gasto_total (lo que reporta Grok); aquí se simula solo para la prueba."""
    from services import llm

    def auditor_que_gasta(con, fila, bandera, orden_id=None):
        llm.gasto_total += 0.02
        return {"accion": "alertar", "causa": "c", "evidencia": ""}, ""
    monkeypatch.setattr(chat_uci, "_cargar_auditor", lambda: auditor_que_gasta)
    monkeypatch.setattr(llm, "gasto_total", 1.0)
    r = srv.ejecutar_auditoria("revisa 2 líneas de UCI")
    assert "Costo de esta consulta: US$0.0400." in r["resultado"]["resumen"]
    assert r["resultado"]["gasto_usd"] == 0.04


def test_deteccion_de_factura_y_de_modo_simulado():
    assert chat_uci.factura_pedida("revisa la factura C531179") == "C531179"
    assert chat_uci.factura_pedida("orden 531179") == "531179"
    assert chat_uci.factura_pedida("orden 11") is None
    assert chat_uci.es_pedido_sintetico("usa la base simulada") and not chat_uci.es_pedido_sintetico("revisa 3 órdenes")


def test_por_defecto_el_chat_usa_los_datos_reales_aunque_no_digas_uci(srv, monkeypatch):
    monkeypatch.setattr(chat_uci, "_cargar_auditor", lambda: auditor())
    r = srv.ejecutar_auditoria("revisa 3 órdenes")
    assert "Datos reales" in r["resultado"]["resumen"] and len(r["resultado"]["ordenes"]) == 3


def test_filtra_por_periodo_con_las_fechas_reales(srv, monkeypatch):
    llamadas = []
    monkeypatch.setattr(chat_uci, "_cargar_auditor", lambda: auditor(llamadas=llamadas))
    r = srv.ejecutar_auditoria("revisa las líneas de agosto")      # la última fecha de los datos es 2010-08-15
    assert sorted(i for i, _ in llamadas) == [104, 105, 106] and "agosto" in r["resultado"]["resumen"]
    assert "No hay líneas en la cola" in srv.ejecutar_auditoria("revisa las líneas de enero")["mensaje"]


def test_filtra_por_factura_y_la_repeticion_sale_gratis(srv, monkeypatch):
    llamadas = []
    monkeypatch.setattr(chat_uci, "_cargar_auditor", lambda: auditor(llamadas=llamadas))
    r = srv.ejecutar_auditoria("revisa la factura 50003")
    assert [i for i, _ in llamadas] == [103] and "de la factura 50003" in r["resultado"]["resumen"]
    llamadas.clear()
    r2 = srv.ejecutar_auditoria("revisa la factura 50003")
    assert llamadas == [] and "sin gastar créditos" in r2["resultado"]["resumen"]
    assert r2["resultado"]["ordenes"][0]["orden_id"] == 103
    assert "No hay líneas en la cola" in srv.ejecutar_auditoria("factura 999999")["mensaje"]


def test_sin_datos_reales_avisa_que_usa_la_base_simulada(monkeypatch, tmp_path):
    """Un clon sin data/uci.db sigue funcionando, pero lo dice claramente."""
    import sqlite3 as sq
    from pathlib import Path
    ruta = str(tmp_path / "sim.db")
    c = sq.connect(ruta)
    c.executescript((Path(__file__).resolve().parent.parent / "db" / "schema.sql").read_text(encoding="utf-8"))
    c.execute("INSERT INTO productos VALUES (1,'E','renta',100)"); c.execute("INSERT INTO inventario VALUES (1,5)")
    c.execute("INSERT INTO costos VALUES (1,50,'2026-01-01')")
    c.execute("INSERT INTO ordenes VALUES (1,'2026-09-01','C',1,1,1,100)"); c.commit(); c.close()
    monkeypatch.setattr(servidor, "RUTA_BD", ruta)
    monkeypatch.setattr(servidor, "RUTA_UCI", str(tmp_path / "no_hay.db"))
    monkeypatch.chdir(tmp_path)
    r = servidor.ejecutar_auditoria("revisa la orden 1")
    assert "base simulada" in r["mensaje"]


def test_las_pruebas_no_pueden_llamar_al_grok_real():
    from services import llm
    with pytest.raises(AssertionError, match="Grok real"):
        llm.client.chat.completions.create(model="x", messages=[])