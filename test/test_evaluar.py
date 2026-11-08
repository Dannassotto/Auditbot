import json

import pytest

import evaluar


def tabla(tmp_path, filas, nombre="casos.csv"):
    ruta = tmp_path / nombre
    ruta.write_text("\n".join(filas) + "\n", encoding="utf-8")
    return str(ruta)


def bitacora(tmp_path, ordenes, llamadas=()):
    (tmp_path / "ordenes.jsonl").write_text("\n".join(json.dumps(o) for o in ordenes), encoding="utf-8")
    (tmp_path / "llamadas.jsonl").write_text("\n".join(json.dumps(x) for x in llamadas), encoding="utf-8")
    return str(tmp_path)


def dec(pos, accion, regla="precio_cero", costo=0.02, **extra):
    return {"pos": pos, "resultado": "ok", "regla": regla, "decision": {"accion": accion, "causa": f"c{pos}"},
            "costo_usd": costo, "duracion_s": 20, **extra}


def test_lee_encabezados_con_acentos_y_mayusculas_y_etiquetas_sinonimas(tmp_path):
    ruta = tabla(tmp_path, ["Posición,Etiqueta,Motivo", "10,Alertar,sin compra", "11, IGNORAR ,normal", "12,dudoso,"])
    assert [(c["pos"], c["etiqueta"]) for c in evaluar.cargar_casos(ruta)] == [(10, "alertar"), (11, "ignorar"), (12, "dudoso")]
    assert evaluar.posiciones_casos(ruta) == [10, 11, 12]


def test_lee_xlsx(tmp_path):
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active
    ws.append(["pos", "regla", "etiqueta"]); ws.append([5, "control", "ignorar"]); ws.append([6, "precio_cero", "alertar"])
    ruta = str(tmp_path / "casos.xlsx"); wb.save(ruta)
    assert [c["pos"] for c in evaluar.cargar_casos(ruta)] == [5, 6]


def test_falta_de_etiquetas_o_etiqueta_invalida_da_un_error_claro(tmp_path):
    with pytest.raises(ValueError, match=r"Faltan etiquetas.*\[2\]"):
        evaluar.cargar_casos(tabla(tmp_path, ["pos,etiqueta", "1,alertar", "2,"]))
    with pytest.raises(ValueError, match="no válida"):
        evaluar.cargar_casos(tabla(tmp_path, ["pos,etiqueta", "1,quizas"]))
    with pytest.raises(ValueError, match="Encabezados"):
        evaluar.cargar_casos(tabla(tmp_path, ["a,b", "1,2"]))


def test_evaluacion_completa_con_aciertos_y_los_tres_tipos_de_fallo(tmp_path):
    casos = [{"pos": p, "etiqueta": e, "motivo": "", "regla": ""} for p, e in
             [(1, "alertar"), (2, "alertar"), (3, "ignorar"), (4, "ignorar"), (5, "alertar"), (6, "dudoso")]]
    d = {1: dec(1, "alertar"), 2: dec(2, "ignorar"), 3: dec(3, "alertar"), 4: dec(4, "ignorar", es_control=True),
         5: {"pos": 5, "resultado": "error", "codigo": "TIMEOUT"}, 6: dec(6, "alertar")}
    r = evaluar.evaluar(casos, d, {"usd": 0.10, "tokens": 5000, "llamadas": 12, "sin_costo": 0})
    assert (r["n"], r["puntuables"], r["dudosos"]) == (6, 5, 1)
    assert r["aciertos"] == 2 and r["exactitud"] == pytest.approx(0.4)
    assert (r["alertas_omitidas"], r["falsas_alarmas"], r["sin_resultado"]) == (1, 1, 1)
    assert r["exactitud_sobre_resueltos"] == pytest.approx(0.5)          # 2 de 4 con resultado
    v = {f["pos"]: f["veredicto"] for f in r["filas"]}
    assert v == {1: "acierto", 2: "alerta omitida", 3: "falsa alarma", 4: "acierto", 5: "sin resultado",
                 6: "dudoso (no puntúa)"}
    assert r["usd_por_tarea_resuelta"] == pytest.approx(0.10 / 5)         # 5 órdenes con decisión ok
    assert r["usd_por_acierto"] == pytest.approx(0.05)


def test_un_caso_no_ejecutado_cuenta_como_sin_resultado(tmp_path):
    r = evaluar.evaluar([{"pos": 9, "etiqueta": "alertar", "motivo": "", "regla": ""}], {}, {"usd": 0, "tokens": 0, "llamadas": 0, "sin_costo": 0})
    assert r["filas"][0]["veredicto"] == "sin resultado" and r["exactitud"] == 0


def test_main_escribe_informe_y_csv_con_los_fallos_a_la_vista(tmp_path, capsys):
    casos = tabla(tmp_path, ["pos,etiqueta,motivo", "1,alertar,", "2,ignorar,compra legítima"])
    carpeta = bitacora(tmp_path, [dec(1, "alertar"), dec(2, "alertar", regla="control", es_control=True)],
                       [{"tokens": 100, "costo_usd": 0.03, "costo_no_reportado": True}])
    evaluar.main([casos, carpeta])
    md = (tmp_path / "evaluacion.md").read_text(encoding="utf-8")
    assert "Casos donde falló (1)" in md and "Caso 2" in md and "falsa alarma" in md and "compra legítima" in md
    assert "Costo por tarea resuelta" in md and "no reportaron costo" in md
    assert (tmp_path / "evaluacion_casos.csv").exists()


def test_la_ultima_entrada_de_cada_orden_gana(tmp_path):
    carpeta = bitacora(tmp_path, [{"pos": 1, "resultado": "error", "codigo": "TIMEOUT"}, dec(1, "alertar")])
    assert evaluar.cargar_decisiones(carpeta)[1]["resultado"] == "ok"