import json

import verificar_bitacora as vb


def escribir(d, nombre, lineas):
    (d / nombre).write_text("\n".join(json.dumps(x) for x in lineas) + "\n", encoding="utf-8")


def carpeta(tmp_path):
    escribir(tmp_path, "ordenes.jsonl", [
        {"pos": 1, "hora": "2026-11-07T01:00:00", "resultado": "error", "duracion_s": 30},      # falló y se recuperó
        {"pos": 1, "hora": "2026-11-07T01:10:00", "resultado": "ok", "duracion_s": 20},
        {"pos": 2, "hora": "2026-11-07T01:11:00", "resultado": "ok", "duracion_s": 10},
        {"pos": 3, "hora": "2026-11-07T01:12:00", "resultado": "sin_decision", "duracion_s": 5},  # fallida
    ])
    escribir(tmp_path, "llamadas.jsonl", [      # formato real de services/llm.py
        {"hora": "2026-11-07T01:00:10", "tokens_entrada": 900, "tokens_salida": 100, "tokens": 1000, "latencia_s": 2.0, "costo_usd": 0.02},
        {"hora": "2026-11-07T01:10:10", "tokens_entrada": 900, "tokens_salida": 100, "tokens": 1000, "latencia_s": 4.0, "costo_usd": 0.03, "costo_no_reportado": False},
        {"hora": "2026-11-07T01:11:10", "tokens": 500, "latencia_s": 3.0, "costo_usd": 0.0, "costo_no_reportado": True},
    ])
    escribir(tmp_path, "errores.jsonl", [{"codigo": "TIMEOUT"}, {"codigo": "TIMEOUT"}, {"codigo": "SIN_CONEXION"}])
    escribir(tmp_path, "eventos.jsonl", [
        {"hora": "2026-11-07T00:59:00", "evento": "inicio"}, {"hora": "2026-11-07T01:05:00", "evento": "tope_gasto"},
        {"hora": "2026-11-07T01:05:00", "evento": "fin"},
        {"hora": "2026-11-07T01:08:00", "evento": "inicio"}])
    return str(tmp_path)


def test_tokens_y_latencia_se_leen_de_los_campos_reales_sin_contar_doble(tmp_path):
    r = vb.verificar(carpeta(tmp_path))
    assert r["tokens"] == 2500 and r["llamadas"] == 3 and r["faltan_campos"] == []
    assert r["latencia"]["media"] == 3.0 and r["latencia"]["max"] == 4.0
    assert r["costo_usd"] == 0.05 and r["llamadas_sin_costo"] == 1


def test_distingue_intentos_fallidos_de_ordenes_fallidas(tmp_path):
    r = vb.verificar(carpeta(tmp_path))
    assert r["intentos_fallidos"] == 3 and r["intentos_por_codigo"] == {"TIMEOUT": 2, "SIN_CONEXION": 1}
    assert r["ordenes_fallidas"] == 1 and r["recuperadas"] == 1 and r["ordenes_unicas"] == 3
    assert r["finales"] == {"ok": 2, "sin_decision": 1}


def test_el_tiempo_trabajado_incluye_los_intentos_anteriores(tmp_path):
    r = vb.verificar(carpeta(tmp_path))
    assert r["segundos_todos_los_intentos"] == 65 and r["segundos_solo_ultimos"] == 35


def test_horas_a_partir_de_los_eventos_con_reinicio_y_sin_cierre(tmp_path):
    r = vb.verificar(carpeta(tmp_path))
    assert r["reinicios"] == 1 and r["ejecucion_sin_cierre"] and r["paradas"] == {"tope_gasto": 1}
    # 00:59->01:05 (6 min) + 01:08->último registro 01:12:00 (4 min)
    assert r["horas_activas"] == round(10 / 60, 2)


def test_avisa_si_faltan_campos_en_llamadas(tmp_path):
    escribir(tmp_path, "llamadas.jsonl", [{"hora": "2026-11-07T01:00:00", "tokens_in": 5, "costo": 0.1}])
    r = vb.verificar(str(tmp_path))
    assert r["faltan_campos"] == ["tokens", "latencia_s", "costo_usd"]
    assert "ATENCIÓN" in vb.informe(r) and "tokens_in" in vb.informe(r)


def test_hay_errores_solo_cuenta_las_ordenes_que_terminaron_en_error(tmp_path):
    carpeta(tmp_path)
    # la bitácora de ejemplo trae un tope_gasto; se quita para probar solo los reintentos
    escribir(tmp_path, "eventos.jsonl", [{"hora": "2026-11-07T00:59:00", "evento": "inicio"}])
    escribir(tmp_path, "ordenes.jsonl", [{"pos": 1, "resultado": "ok"}, {"pos": 2, "resultado": "ok"}])
    assert vb.hay_errores(str(tmp_path)) is False
    escribir(tmp_path, "ordenes.jsonl", [{"pos": 1, "resultado": "error"}, {"pos": 2, "resultado": "ok"}])
    assert vb.hay_errores(str(tmp_path)) is True
    escribir(tmp_path, "ordenes.jsonl", [{"pos": 1, "resultado": "error"}, {"pos": 1, "resultado": "ok"}])
    assert vb.hay_errores(str(tmp_path)) is False        # se recuperó en un reintento posterior


def test_no_reintenta_si_se_alcanzo_el_tope_de_gasto(tmp_path):
    carpeta(tmp_path)                                    # trae un evento tope_gasto
    escribir(tmp_path, "ordenes.jsonl", [{"pos": 1, "resultado": "error"}])
    assert vb.hay_errores(str(tmp_path)) is False