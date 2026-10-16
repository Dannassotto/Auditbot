import json
from services.llm import conversar
from agent.prompts import SISTEMA
from agent.tools import TOOLS, ejecutar

MAX_PASOS = 6


class Cancelado(Exception):
    """Pidieron detener la consulta: no se hacen más llamadas a Grok."""


def auditar_orden(con, hallazgo, cancelar=None):
    mensajes = [
        {"role": "system", "content": SISTEMA},
        {"role": "user", "content": "Orden a investigar:\n" +
         json.dumps(hallazgo, ensure_ascii=False)},
    ]
    for _ in range(MAX_PASOS):
        if cancelar is not None and cancelar.is_set():   # antes de CADA llamada nueva a Grok
            raise Cancelado()
        msg = conversar(mensajes, TOOLS)
        if not msg.tool_calls:
            return None  # terminó sin decidir

        mensajes.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [{"id": tc.id, "type": "function",
                            "function": {"name": tc.function.name,
                                         "arguments": tc.function.arguments}}
                           for tc in msg.tool_calls],
        })
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            if tc.function.name == "registrar_decision":
                args["orden_id"] = hallazgo["orden_id"]   
                if args.get("accion") == "ignorar":
                    args["perdida_estimada_mxn"] = 0
                return args
            resultado = ejecutar(con, tc.function.name, args)
            mensajes.append({"role": "tool", "tool_call_id": tc.id, "content": resultado})
    return None