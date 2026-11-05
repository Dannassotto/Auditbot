"""Pruebas del bucle del agente (agent/loop.py) con Grok simulado: sin red ni costo."""
import json
import threading
from types import SimpleNamespace

import pytest

from agent import loop


def llamada(nombre, **args):
    return SimpleNamespace(id=f"call_{nombre}", function=SimpleNamespace(
        name=nombre, arguments=json.dumps(args)))


def respuesta(*tool_calls, content=""):
    return SimpleNamespace(content=content, tool_calls=list(tool_calls))


class GrokFalso:
    """Devuelve respuestas en orden y cuenta cuántas veces se le llamó."""
    def __init__(self, *respuestas):
        self.respuestas = list(respuestas)
        self.llamadas = 0
        self.mensajes_vistos = []

    def __call__(self, mensajes, tools):
        self.llamadas += 1
        self.mensajes_vistos.append(list(mensajes))
        return self.respuestas.pop(0) if self.respuestas else respuesta(llamada("historial_costos"))


HALLAZGO = {"orden_id": 11, "banderas": ["margen_negativo"]}


@pytest.fixture
def herramientas(monkeypatch):
    usadas = []
    monkeypatch.setattr(loop, "ejecutar", lambda con, n, a: usadas.append((n, a)) or '{"ok": true}')
    return usadas


def test_decision_de_alerta_se_devuelve_con_el_id_de_la_orden(monkeypatch, herramientas):
    grok = GrokFalso(respuesta(llamada("registrar_decision", accion="alertar",
                                       causa="x", perdida_estimada_mxn=52)))
    monkeypatch.setattr(loop, "conversar", grok)
    d = loop.auditar_orden(None, HALLAZGO)
    assert d["accion"] == "alertar" and d["perdida_estimada_mxn"] == 52
    assert d["orden_id"] == 11 and grok.llamadas == 1


def test_el_id_de_orden_lo_fija_el_codigo_no_el_modelo(monkeypatch, herramientas):
    grok = GrokFalso(respuesta(llamada("registrar_decision", accion="alertar", orden_id=999)))
    monkeypatch.setattr(loop, "conversar", grok)
    assert loop.auditar_orden(None, HALLAZGO)["orden_id"] == 11


def test_ignorar_fuerza_perdida_cero(monkeypatch, herramientas):
    grok = GrokFalso(respuesta(llamada("registrar_decision", accion="ignorar",
                                       causa="descuento", perdida_estimada_mxn=500)))
    monkeypatch.setattr(loop, "conversar", grok)
    assert loop.auditar_orden(None, HALLAZGO)["perdida_estimada_mxn"] == 0


def test_usa_herramienta_antes_de_decidir(monkeypatch, herramientas):
    grok = GrokFalso(
        respuesta(llamada("descuentos_autorizados", orden_id=11)),
        respuesta(llamada("registrar_decision", accion="alertar")),
    )
    monkeypatch.setattr(loop, "conversar", grok)
    d = loop.auditar_orden(None, HALLAZGO)
    assert d["accion"] == "alertar" and grok.llamadas == 2
    assert herramientas == [("descuentos_autorizados", {"orden_id": 11})]
    # el resultado de la herramienta se le devuelve al modelo en el segundo turno
    assert any(m.get("role") == "tool" for m in grok.mensajes_vistos[1])


def test_respuesta_sin_herramientas_termina_sin_decision(monkeypatch, herramientas):
    grok = GrokFalso(respuesta(content="no sé"))
    monkeypatch.setattr(loop, "conversar", grok)
    assert loop.auditar_orden(None, HALLAZGO) is None and grok.llamadas == 1


def test_tope_de_pasos_corta_el_bucle(monkeypatch, herramientas):
    """Si el modelo nunca decide, se detiene tras MAX_PASOS llamadas (control de costo)."""
    grok = GrokFalso()   # siempre pide otra herramienta
    monkeypatch.setattr(loop, "conversar", grok)
    assert loop.auditar_orden(None, HALLAZGO) is None
    assert grok.llamadas == loop.MAX_PASOS


def test_cancelar_antes_de_empezar_no_llama_a_grok(monkeypatch, herramientas):
    grok = GrokFalso()
    monkeypatch.setattr(loop, "conversar", grok)
    ev = threading.Event(); ev.set()
    with pytest.raises(loop.Cancelado):
        loop.auditar_orden(None, HALLAZGO, cancelar=ev)
    assert grok.llamadas == 0


def test_cancelar_a_mitad_detiene_nuevas_llamadas(monkeypatch, herramientas):
    ev = threading.Event()
    grok = GrokFalso()
    original = grok.__call__

    def llamar_y_cancelar(mensajes, tools):
        r = original(mensajes, tools)
        ev.set()                      # el usuario cancela tras la primera llamada
        return r

    monkeypatch.setattr(loop, "conversar", llamar_y_cancelar)
    with pytest.raises(loop.Cancelado):
        loop.auditar_orden(None, HALLAZGO, cancelar=ev)
    assert grok.llamadas == 1