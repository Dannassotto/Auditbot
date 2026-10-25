import os
import json
import time
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    base_url=os.getenv("BASE_URL"),
    api_key=os.getenv("API_KEY"),
)

MODEL = "grok-4.6"
TICKS_PER_USD = 10_000_000_000
MAX_BUDGET_USD = 5.0  # tope de esta sesión
gasto_total = 0.0


def preguntar(mensajes, max_tokens=300, reintentos=3):
    """Llama a Grok con reintentos, registra el costo y respeta el tope."""
    global gasto_total
    if gasto_total >= MAX_BUDGET_USD:
        raise RuntimeError(f"Tope de gasto alcanzado: ${gasto_total:.4f}")

    for intento in range(reintentos):
        try:
            r = client.chat.completions.create(
                model=MODEL,
                messages=mensajes,
                max_tokens=max_tokens,
            )
            costo = getattr(r.usage, "cost_in_usd_ticks", 0) / TICKS_PER_USD
            gasto_total += costo
            _registrar(r, costo)
            return r.choices[0].message.content
        except Exception:
            if intento == reintentos - 1:
                raise
            time.sleep(2 ** intento)  


def _registrar(r, costo):
    os.makedirs("logs", exist_ok=True)
    with open("logs/llamadas.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "hora": datetime.now().isoformat(),
            "tokens": r.usage.total_tokens,
            "costo_usd": round(costo, 6),
            "gasto_acumulado": round(gasto_total, 6),
        }) + "\n")

def conversar(mensajes, tools, max_tokens=2000, reintentos=3):
    """Como preguntar(), pero deja que Grok llame herramientas."""
    global gasto_total
    if gasto_total >= MAX_BUDGET_USD:
        raise RuntimeError(f"Tope de gasto alcanzado: ${gasto_total:.4f}")

    for intento in range(reintentos):
        try:
            r = client.chat.completions.create(
                model=MODEL, messages=mensajes, tools=tools, max_tokens=max_tokens,
            )
            costo = getattr(r.usage, "cost_in_usd_ticks", 0) / TICKS_PER_USD
            gasto_total += costo
            _registrar(r, costo)
            return r.choices[0].message
        except Exception:
            if intento == reintentos - 1:
                raise
            time.sleep(2 ** intento)