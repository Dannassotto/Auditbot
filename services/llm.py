import os
import json
import time
from datetime import datetime
from dotenv import load_dotenv
from openai import (
    OpenAI, RateLimitError, APITimeoutError, APIConnectionError,
    AuthenticationError, APIStatusError,
)

load_dotenv()

client = OpenAI(
    base_url=os.getenv("BASE_URL"),
    api_key=os.getenv("API_KEY"),
    timeout=60,
    max_retries=0,   # los reintentos los controlamos nosotros
)

MODEL = "grok-4.6"
TICKS_PER_USD = 10_000_000_000
MAX_BUDGET_USD = 5.0  # tope de esta sesión
RUTA_LLAMADAS = "logs/llamadas.jsonl"
RUTA_ERRORES = "logs/errores.jsonl"


class ErrorLLM(Exception):
    """Error ya clasificado. e.info = {codigo, mensaje, reintentable}."""
    def __init__(self, info):
        super().__init__(info["mensaje"])
        self.info = info


def clasificar_error(e):
    if isinstance(e, APITimeoutError):  # va antes: es subclase de APIConnectionError
        return {"codigo": "TIMEOUT", "mensaje": "El modelo tardó demasiado en responder.", "reintentable": True}
    if isinstance(e, APIConnectionError):
        return {"codigo": "SIN_CONEXION", "mensaje": "No hay conexión con el servicio.", "reintentable": True}
    if isinstance(e, RateLimitError):
        return {"codigo": "LIMITE_VELOCIDAD", "mensaje": "Demasiadas solicitudes. Reintentando en unos segundos.", "reintentable": True}
    if isinstance(e, AuthenticationError):
        return {"codigo": "LLAVE_INVALIDA", "mensaje": "La API_KEY no es válida. Revisa tu archivo .env.", "reintentable": False}
    if isinstance(e, APIStatusError) and e.status_code >= 500:
        return {"codigo": "ERROR_SERVIDOR", "mensaje": "El servicio de Grok tuvo un problema.", "reintentable": True}
    return {"codigo": "DESCONOCIDO", "mensaje": "Ocurrió un error inesperado.", "reintentable": False}


def _cargar_gasto():
    """Recupera lo gastado antes, para que el tope sobreviva a reinicios."""
    total = 0.0
    try:
        with open(RUTA_LLAMADAS, encoding="utf-8") as f:
            for linea in f:
                try:
                    total += json.loads(linea).get("costo_usd", 0.0)
                except ValueError:
                    pass
    except FileNotFoundError:
        pass
    return total


gasto_total = _cargar_gasto()


def _escribir(ruta, registro):
    os.makedirs("logs", exist_ok=True)
    with open(ruta, "a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")


def _registrar_error(info, orden_id, intento, detalle):
    _escribir(RUTA_ERRORES, {
        "hora": datetime.now().isoformat(),
        "orden_id": orden_id,
        "codigo": info["codigo"],
        "reintentable": info["reintentable"],
        "intento": intento,
        "detalle": detalle[:500],
    })


def _registrar(r, costo, sin_costo, latencia, orden_id):
    _escribir(RUTA_LLAMADAS, {
        "hora": datetime.now().isoformat(),
        "orden_id": orden_id,
        "tokens_entrada": r.usage.prompt_tokens,
        "tokens_salida": r.usage.completion_tokens,
        "tokens": r.usage.total_tokens,
        "latencia_s": round(latencia, 2),
        "costo_usd": round(costo, 6),
        "costo_no_reportado": sin_costo,
        "gasto_acumulado": round(gasto_total, 6),
    })


def _llamar(orden_id, reintentos, **kwargs):
    global gasto_total
    if gasto_total >= MAX_BUDGET_USD:
        info = {"codigo": "TOPE_GASTO", "mensaje": f"Tope de gasto alcanzado: ${gasto_total:.4f}", "reintentable": False}
        _registrar_error(info, orden_id, 0, info["mensaje"])
        raise ErrorLLM(info)

    for intento in range(1, reintentos + 1):
        t0 = time.time()
        try:
            r = client.chat.completions.create(model=MODEL, **kwargs)
        except Exception as e:
            info = clasificar_error(e)
            _registrar_error(info, orden_id, intento, repr(e))
            if not info["reintentable"] or intento == reintentos:
                raise ErrorLLM(info) from e
            time.sleep(2 ** (intento - 1))
            continue

        ticks = getattr(r.usage, "cost_in_usd_ticks", None)
        sin_costo = ticks is None
        costo = (ticks or 0) / TICKS_PER_USD
        gasto_total += costo
        _registrar(r, costo, sin_costo, time.time() - t0, orden_id)
        return r


def preguntar(mensajes, max_tokens=300, reintentos=3, orden_id=None):
    """Llama a Grok con reintentos, registra el costo y respeta el tope."""
    r = _llamar(orden_id, reintentos, messages=mensajes, max_tokens=max_tokens)
    return r.choices[0].message.content


def conversar(mensajes, tools, max_tokens=2000, reintentos=3, orden_id=None):
    """Como preguntar(), pero deja que Grok llame herramientas."""
    r = _llamar(orden_id, reintentos, messages=mensajes, tools=tools, max_tokens=max_tokens)
    return r.choices[0].message