"""Pruebas de servidor.py con una base temporal y un Grok simulado (sin red ni costo)."""
import json
import os
import sqlite3
from pathlib import Path

import pytest

import servidor
from services.llm import ErrorLLM

SCHEMA = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


@pytest.fixture
def ruta_bd(tmp_path):
    return str(tmp_path / "auditbot.db")


@pytest.fixture
def con(ruta_bd):                       # sustituye al fixture en memoria: el servidor abre su propio archivo
    c = sqlite3.connect(ruta_bd)
    c.executescript(SCHEMA.read_text(encoding="utf-8"))
    c.commit()
    yield c
    c.close()


@pytest.fixture
def srv(monkeypatch, ruta_bd, tmp_path):
    monkeypatch.setattr(servidor, "RUTA_BD", ruta_bd)
    monkeypatch.setattr(servidor, "RUTA_UCI", str(tmp_path / "sin_datos_reales.db"))   # fuerza la base de prueba
    monkeypatch.setattr(servidor, "RUTA_LOG", str(tmp_path / "decisiones.jsonl"))
    monkeypatch.setattr(servidor, "ESPERA_ANTES_DE_ENVIAR", 0)
    servidor.cancelar_evento.clear()
    servidor.progreso.update(hechas=0, total=0, enviado=False)
    return servidor


@pytest.fixture
def ordenes(con, crear_orden):
    crear_orden(id=1)                                  # limpia
    crear_orden(id=2, facturas=[])                     # sin_factura
    crear_orden(id=3, facturas=[], cliente="Otro")     # sin_factura
    crear_orden(id=4, pid=2, costo=None)               # solo datos incompletos
    con.commit()


def decide(accion="alertar"):
    llamadas = []

    def auditar(con, h, cancelar=None):
        llamadas.append(h["orden_id"])
        return {"accion": accion, "causa": "motivo", "evidencia": "x", "perdida_estimada_mxn": 10}
    auditar.llamadas = llamadas
    return auditar


def falla(error):
    def auditar(con, h, cancelar=None):
        raise error
    return auditar


def error_llm(codigo, mensaje):
    return ErrorLLM({"codigo": codigo, "mensaje": mensaje, "reintentable": codigo != "LLAVE_INVALIDA"})


# ---------- el bug de "la orden 55" ----------
def test_pedir_una_orden_concreta_ya_no_revienta(srv, ordenes, monkeypatch):
    monkeypatch.setattr(srv, "auditar_orden", decide())
    r = srv.ejecutar_auditoria("la orden 2")
    assert r["resultado"]["ordenes"][0]["orden_id"] == 2


def test_pedir_varias_concretas(srv, ordenes, monkeypatch):
    aud = decide()
    monkeypatch.setattr(srv, "auditar_orden", aud)
    srv.ejecutar_auditoria("órdenes 2 y 3")
    assert sorted(aud.llamadas) == [2, 3]


def test_pedir_por_cantidad_respeta_el_limite(srv, ordenes, monkeypatch):
    aud = decide()
    monkeypatch.setattr(srv, "auditar_orden", aud)
    srv.ejecutar_auditoria("dame 1 orden")
    assert len(aud.llamadas) == 1


def test_lo_ya_investigado_se_muestra_sin_gastar_creditos(srv, ordenes, monkeypatch):
    aud = decide()
    monkeypatch.setattr(srv, "auditar_orden", aud)
    srv.ejecutar_auditoria("órdenes 2 y 3")
    aud.llamadas.clear()
    r = srv.ejecutar_auditoria("dame 5 órdenes")           # las dos con bandera ya estaban guardadas
    assert aud.llamadas == [] and "sin gastar créditos" in r["mensaje"]


# ---------- datos incompletos ----------
def test_una_orden_solo_con_datos_incompletos_no_va_a_grok(srv, ordenes, monkeypatch):
    aud = decide()
    monkeypatch.setattr(srv, "auditar_orden", aud)
    r = srv.ejecutar_auditoria("la orden 4")
    assert aud.llamadas == [] and "falta costo vigente" in r["mensaje"] and r["resultado"] is None


def test_la_etiqueta_de_datos_incompletos_existe():
    assert servidor.ETIQUETAS["datos_incompletos"] == "Datos incompletos"
    assert servidor._pct(None) == "sin dato" and servidor._pct(12.5) == "12.5%"


# ---------- errores visibles para el usuario ----------
def test_error_de_conexion_se_muestra_claro_y_deja_el_codigo_en_la_bitacora(srv, ordenes, monkeypatch):
    monkeypatch.setattr(srv, "auditar_orden", falla(error_llm("SIN_CONEXION", "No hay conexión con el servicio.")))
    r = srv.ejecutar_auditoria("la orden 2")
    tarjeta = r["resultado"]["ordenes"][0]
    assert "No hay conexión con el servicio." in tarjeta["causa"] and "ErrorLLM" not in tarjeta["causa"]
    assert tarjeta["accion"] == "revisar"
    registro = json.loads(open(srv.RUTA_LOG, encoding="utf-8").read().splitlines()[0])
    assert registro["error"].startswith("SIN_CONEXION") and registro["decision"] is None


def test_error_inesperado_no_filtra_detalles_tecnicos(srv, ordenes, monkeypatch):
    monkeypatch.setattr(srv, "auditar_orden", falla(KeyError("secreto_interno")))
    tarjeta = srv.ejecutar_auditoria("la orden 2")["resultado"]["ordenes"][0]
    assert "secreto_interno" not in tarjeta["causa"] and "inesperado" in tarjeta["causa"]


def test_tope_de_gasto_se_explica(srv, ordenes, monkeypatch):
    monkeypatch.setattr(srv, "auditar_orden", falla(error_llm("TOPE_GASTO", "Tope de gasto alcanzado: $5.0010")))
    assert "Tope de gasto" in srv.ejecutar_auditoria("la orden 2")["mensaje"]


def test_llave_invalida_para_de_una_vez_y_no_se_confunde_con_el_tope(srv, ordenes, monkeypatch):
    aud_llamadas = []

    def auditar(con, h, cancelar=None):
        aud_llamadas.append(h["orden_id"])
        raise error_llm("LLAVE_INVALIDA", "La API_KEY no es válida. Revisa tu archivo .env.")
    monkeypatch.setattr(srv, "auditar_orden", auditar)
    r = srv.ejecutar_auditoria("órdenes 2 y 3")
    assert "API_KEY" in r["mensaje"] and "Tope de gasto" not in r["mensaje"]
    assert aud_llamadas == [2]                         # no insiste con las demás


def test_una_orden_que_falla_no_tumba_a_las_demas(srv, ordenes, monkeypatch):
    def auditar(con, h, cancelar=None):
        if h["orden_id"] == 2:
            raise error_llm("TIMEOUT", "El modelo tardó demasiado en responder.")
        return {"accion": "alertar", "causa": "ok", "evidencia": "", "perdida_estimada_mxn": 5}
    monkeypatch.setattr(srv, "auditar_orden", auditar)
    acciones = {o["orden_id"]: o["accion"] for o in srv.ejecutar_auditoria("órdenes 2 y 3")["resultado"]["ordenes"]}
    assert acciones == {2: "revisar", 3: "alertar"}


# ---------- panel de la corrida ----------
def escribir(ruta, lineas):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text("\n".join(json.dumps(x) for x in lineas) + "\n", encoding="utf-8")


def test_estado_corrida_cuenta_lo_que_hay_en_la_bitacora(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "logs" / "corrida"
    escribir(d / "ordenes.jsonl", [
        {"pos": 1, "hora": "2026-11-06T20:00:00", "regla": "precio_cero", "resultado": "error", "codigo": "TIMEOUT"},
        {"pos": 1, "hora": "2026-11-06T20:01:00", "regla": "precio_cero", "resultado": "ok",
         "decision": {"accion": "alertar", "causa": "x"}, "costo_usd": 0.02},      # la última entrada gana
        {"pos": 2, "hora": "2026-11-06T20:02:00", "regla": "control", "resultado": "ok",
         "decision": {"accion": "ignorar", "causa": "normal"}},
        {"sin_pos": True},
    ])
    escribir(d / "llamadas.jsonl", [{"tokens": 100, "costo_usd": 0.01}, {"tokens": 50, "costo_usd": 0.02}])
    escribir(d / "errores.jsonl", [{"codigo": "TIMEOUT"}])
    escribir(d / "eventos.jsonl", [{"evento": "inicio", "hora": "2026-11-06T19:59:00", "ya_hechas": 0, "pendientes": 10}])
    e = servidor.estado_corrida("corrida")
    assert e["estado"] == "en curso" and e["total"] == 10 and e["hechas"] == 2
    assert e["ok"] == 2 and e["error"] == 0 and e["alertas"] == 1
    assert e["llamadas"] == 2 and e["tokens"] == 150 and e["costo_usd"] == 0.03 and e["intentos_fallidos"] == 1
    assert e["ultimas"][0]["regla"] == "control"       # la más reciente primero


def test_estado_corrida_terminada_y_sin_iniciar(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert servidor.estado_corrida("nada")["estado"] == "sin iniciar"
    escribir(tmp_path / "logs" / "c" / "eventos.jsonl", [{"evento": "inicio", "pendientes": 1}, {"evento": "fin"}])
    assert servidor.estado_corrida("c")["estado"] == "terminada"


def test_estado_corrida_detenida_si_la_bitacora_lleva_tiempo_quieta(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "logs" / "c"
    escribir(d / "ordenes.jsonl", [{"pos": 1, "hora": "x", "resultado": "ok"}])
    escribir(d / "eventos.jsonl", [{"evento": "inicio", "pendientes": 1}])
    viejo = os.path.getmtime(d / "ordenes.jsonl") - 3600
    os.utime(d / "ordenes.jsonl", (viejo, viejo))
    assert servidor.estado_corrida("c")["estado"] == "detenida sin cierre"


def test_estado_corrida_no_sale_de_logs_ni_crea_archivos(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    assert servidor.estado_corrida("../..")["estado"] == "sin iniciar"
    assert servidor.estado_corrida("a/b")["estado"] == "sin iniciar"
    assert not (tmp_path / "data" / "uci.db").exists()


def test_estado_corrida_lee_el_total_de_la_cola_si_aun_no_hay_eventos(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    c = sqlite3.connect(tmp_path / "data" / "uci.db")
    c.execute("CREATE TABLE cola (pos INTEGER)")
    c.executemany("INSERT INTO cola VALUES (?)", [(i,) for i in range(7)])
    c.commit(); c.close()
    assert servidor.estado_corrida("corrida")["total"] == 7


def test_fin_tras_una_parada_no_cuenta_como_terminada(tmp_path, monkeypatch):
    """worker.py escribe 'fin' aunque se haya detenido antes de acabar: el panel debe decir por qué."""
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "logs" / "c"
    escribir(d / "eventos.jsonl", [{"evento": "inicio", "pendientes": 9}, {"evento": "tope_gasto"}, {"evento": "fin"}])
    e = servidor.estado_corrida("c")
    assert e["estado"] == "detenida" and "tope de gasto" in e["motivo"]
    escribir(d / "eventos.jsonl", [{"evento": "inicio", "pendientes": 9},
                                   {"evento": "parada_por_error", "codigo": "LLAVE_INVALIDA"}, {"evento": "fin"}])
    assert "LLAVE_INVALIDA" in servidor.estado_corrida("c")["motivo"]


def test_pausa_por_errores_seguidos_se_ve_como_en_pausa(tmp_path, monkeypatch):
    from datetime import datetime
    monkeypatch.chdir(tmp_path)
    ahora = datetime.now().isoformat()
    escribir(tmp_path / "logs" / "c" / "eventos.jsonl",
             [{"evento": "inicio", "pendientes": 9}, {"evento": "pausa_por_errores_seguidos", "hora": ahora}])
    e = servidor.estado_corrida("c")
    assert e["estado"] == "en pausa" and "errores seguidos" in e["motivo"]


def test_fin_normal_sigue_siendo_terminada(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    escribir(tmp_path / "logs" / "c" / "eventos.jsonl", [{"evento": "inicio", "pendientes": 1}, {"evento": "fin"}])
    e = servidor.estado_corrida("c")
    assert e["estado"] == "terminada" and e["motivo"] is None


# ---------- "ver corrida" desde el chat ----------
def _corrida(tmp_path, nombre, hechas=2, evento_inicio=True):
    d = tmp_path / "logs" / nombre
    escribir(d / "ordenes.jsonl", [
        {"pos": i, "hora": f"2026-11-06T20:0{i}:00", "regla": "precio_cero", "resultado": "ok",
         "decision": {"accion": "alertar", "causa": f"causa {i}"}, "costo_usd": 0.02} for i in range(1, hechas + 1)])
    escribir(d / "llamadas.jsonl", [{"tokens": 1000, "costo_usd": 0.02}] * hechas)
    escribir(d / "eventos.jsonl", [{"evento": "inicio", "hora": "2026-11-06T19:59:00", "ya_hechas": 0, "pendientes": 10}])


def test_reconoce_cuando_piden_ver_la_corrida():
    for t in ("ver corrida", "¿cómo va la corrida?", "avance de la corrida piloto2"):
        assert servidor.es_pedido_corrida(t), t
    for t in ("orden 11", "revisa 3 líneas de UCI en la corrida", "agosto"):
        assert not servidor.es_pedido_corrida(t), t


def test_ver_corrida_muestra_avance_consumo_y_ultimas(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _corrida(tmp_path, "piloto2", hechas=2)
    m = servidor.texto_corrida("ver corrida")
    assert "«piloto2»" in m and "2 de 10 (20%)" in m and "US$0.0400 en 2 llamadas" in m
    assert "2,000 tokens" in m and "US$0.0200 por orden" in m and "precio_cero: alertar" in m


def test_elige_la_carpeta_nombrada_o_la_normal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _corrida(tmp_path, "piloto2", hechas=1)
    _corrida(tmp_path, "corrida", hechas=3)
    assert "«corrida»" in servidor.texto_corrida("ver corrida")                 # la normal por defecto
    assert "«piloto2»" in servidor.texto_corrida("ver corrida piloto2")


def test_sin_corridas_y_pedir_lanzar_dan_mensajes_utiles(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert "Todavía no hay ninguna corrida" in servidor.texto_corrida("ver corrida")
    assert "consola" in servidor.texto_corrida("inicia la corrida")


def test_ver_corrida_funciona_aunque_la_corrida_este_en_curso_y_no_llama_a_grok(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _corrida(tmp_path, "corrida", hechas=2)               # reciente => "en curso", el chat bloquea lo que gasta
    llamadas = []
    monkeypatch.setattr(servidor, "auditar_orden", lambda *a, **k: llamadas.append(1))
    r = servidor.ejecutar_auditoria("ver corrida")
    assert "en curso" in r["mensaje"] and r["resultado"] is None and llamadas == []
    assert "corrida larga en curso" in servidor.ejecutar_auditoria("orden 2")["mensaje"]   # lo que gasta, sigue bloqueado