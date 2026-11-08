import csv
import json
import sqlite3

import exportar_bitacora as eb


def escribir(d, nombre, lineas):
    (d / nombre).write_text("\n".join(json.dumps(x) for x in lineas) + "\n", encoding="utf-8")


def montar(tmp_path, con_orden_id=True):
    db = str(tmp_path / "uci.db")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE lineas (id INTEGER PRIMARY KEY, factura TEXT, producto TEXT, descripcion TEXT, cantidad INTEGER, "
              "fecha TEXT, precio REAL, cliente TEXT, pais TEXT, cancelacion INTEGER)")
    c.execute("INSERT INTO lineas VALUES (10,'C553024','23190','EXERCISE BOOKS',-1,'2011-05-12 19:28:00',1.65,'13136','United Kingdom',1)")
    c.execute("INSERT INTO lineas VALUES (11,'510869','90035C','',-24,'2010-06-04 09:55:00',0.0,NULL,'United Kingdom',0)")
    c.commit(); c.close()
    escribir(tmp_path, "ordenes.jsonl", [
        {"hora": "2026-11-07T08:00:00", "pos": 1, "linea_id": 10, "orden_id": "uci-10", "regla": "cancelacion_sin_compra",
         "es_control": 0, "resultado": "error", "codigo": "SIN_CONEXION", "error": "No hay conexión", "costo_usd": 0, "duracion_s": 5},
        {"hora": "2026-11-07T08:10:00", "pos": 1, "linea_id": 10, "orden_id": "uci-10", "regla": "cancelacion_sin_compra",
         "es_control": 0, "resultado": "ok", "decision": {"accion": "alertar", "causa": "sin compra previa", "evidencia": "x"},
         "costo_usd": 0.02, "duracion_s": 30},
        {"hora": "2026-11-07T08:11:00", "pos": 2, "linea_id": 11, "orden_id": "uci-11", "regla": "precio_cero",
         "es_control": 1, "resultado": "sin_decision", "nota": "agotó pasos", "costo_usd": 0.03, "duracion_s": 40}])
    oid = (lambda v: {"orden_id": v}) if con_orden_id else (lambda v: {})
    escribir(tmp_path, "llamadas.jsonl", [
        {**oid("uci-10"), "tokens_entrada": 900, "tokens_salida": 100, "tokens": 1000, "costo_usd": 0.01},
        {**oid("uci-10"), "tokens": 500, "costo_usd": 0.01}, {**oid("uci-11"), "tokens": 700, "costo_usd": 0.03}])
    escribir(tmp_path, "errores.jsonl", [{"orden_id": "uci-10", "codigo": "SIN_CONEXION"}] * 2)
    return str(tmp_path), db


def test_una_fila_por_intento_con_la_entrada_real_y_la_recuperacion(tmp_path):
    carpeta, db = montar(tmp_path)
    filas, info = eb.construir(carpeta, db)
    assert len(filas) == 3 and info["con_entradas"] and info["con_id_en_llamadas"]
    primero, segundo, tercero = filas
    assert (primero["intento"], primero["es_ultimo_intento"], primero["exito"], primero["recuperada"]) == (1, "no", "no", "no")
    assert (segundo["intento"], segundo["es_ultimo_intento"], segundo["exito"], segundo["recuperada"]) == (2, "si", "si", "si")
    assert segundo["entrada_factura"] == "C553024" and segundo["entrada_precio"] == 1.65 and segundo["entrada_cliente"] == "13136"
    assert segundo["accion"] == "alertar" and segundo["tokens"] == 1500 and segundo["llamadas_grok"] == 2
    assert segundo["intentos_api_fallidos"] == 2
    assert tercero["resultado"] == "sin_decision" and tercero["exito"] == "no" and tercero["es_control"] == "si"
    assert tercero["entrada_cliente"] is None or tercero["entrada_cliente"] == ""


def test_si_las_llamadas_no_traen_orden_id_los_tokens_quedan_vacios_y_avisa(tmp_path, capsys):
    carpeta, db = montar(tmp_path, con_orden_id=False)
    filas, info = eb.construir(carpeta, db)
    assert filas[1]["tokens"] == "" and info["con_id_en_llamadas"] is False
    eb.main([carpeta, db])
    assert "no traen orden_id" in capsys.readouterr().out


def test_sin_la_base_las_columnas_de_entrada_quedan_vacias_y_avisa(tmp_path, capsys):
    carpeta, _ = montar(tmp_path)
    eb.main([carpeta, str(tmp_path / "no_existe.db")])
    assert "no pude leer las líneas" in capsys.readouterr().out
    filas = list(csv.DictReader(open(tmp_path / "bitacora.csv", encoding="utf-8-sig")))
    assert len(filas) == 3 and filas[1]["entrada_factura"] == "" and filas[1]["accion"] == "alertar"


def test_el_csv_tiene_todas_las_columnas_y_se_lee_de_vuelta(tmp_path):
    carpeta, db = montar(tmp_path)
    eb.main([carpeta, db])
    lector = csv.DictReader(open(tmp_path / "bitacora.csv", encoding="utf-8-sig"))
    assert lector.fieldnames == eb.COLUMNAS
    assert sum(1 for _ in lector) == 3