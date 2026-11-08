# listener.py
# AudiBot: reconocimiento de voz con Vosk y comunicacion con Electron.
#
# Protocolo:
#   - stdout: eventos JSON, uno por linea (wake, orden, confirmado, rechazado, timeout, parcial, oido)
#   - stderr: mensajes de diagnostico
#   - stdin : Electron puede mandar "pausa N" para silenciar el microfono N segundos

import argparse
import difflib
import json
import os
import queue
import re
import sys
import threading
import time
import unicodedata

SAMPLE_RATE = 16000
BLOQUE_AUDIO = 8000

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELO_GRANDE = os.path.join(BASE_DIR, "model-grande")
MODELO_PEQUENO = os.path.join(BASE_DIR, "model")

MODELO_PREDETERMINADO = (
    MODELO_GRANDE if os.path.isdir(MODELO_GRANDE)
    else MODELO_PEQUENO
)

# Variantes observadas al pronunciar AudiBot.
# Se comprueban solo con transcripciones finales.
WAKE_VARIANTES = (
    "audi bot",
    "audio bot",
    "audiobot",
    "autobot",
    "audi booth",
    "audi good",
    "abdi bot",
    "haiti bot",
    "audi por",
    "au bout",
    "audi hollywood",
    "about ipod",
    "aut ipod",
    "oye audibot",
    "hola audibot",
)

SI = {
    "si", "correcto", "confirmo", "confirmar",
    "dale", "de acuerdo", "adelante", "hazlo", "acepto",
}

NO = {
    "no", "cancelar", "cancela", "rechazo",
    "rechazar", "detente", "para", "negativo",
}

# Similitud minima para la coincidencia aproximada de la palabra de activacion.
UMBRAL_WAKE = 0.78

# Vosk devuelve el texto sin tildes ni enie. Aqui se restauran las palabras que usas.
# Clave: como la escribe Vosk (minusculas, sin tilde). Valor: como debe quedar.
# Anade las tuyas mirando las lineas "[oigo] ..." de la terminal.
# Evita palabras ambiguas (como "esta"/"esta" o "mas"/"mas"), porque cambiarian frases correctas.
CORRECCIONES = {
    "ordenes": "órdenes",
    "numero": "número",
    "numeros": "números",
    "ano": "año",
    "anos": "años",
    "dia": "día",
    "dias": "días",
    "ultimo": "último",
    "ultima": "última",
    "ultimos": "últimos",
    "ultimas": "últimas",
    "revision": "revisión",
    "informacion": "información",
    "facturacion": "facturación",
    "codigo": "código",
    "telefono": "teléfono",
    "articulo": "artículo",
    "tambien": "también",
    "miercoles": "miércoles",
    "sabado": "sábado",
    "septiembre": "septiembre",
}


def restaurar_acentos(texto):
    """Devuelve el texto con las tildes y enies restauradas segun CORRECCIONES."""
    return " ".join(CORRECCIONES.get(p, p) for p in (texto or "").split())


def normalizar(texto):
    """Normaliza el texto para facilitar las comparaciones."""
    texto = (texto or "").lower().strip()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(
        c for c in texto
        if unicodedata.category(c) != "Mn"
    )
    texto = re.sub(r"[^\w\s]", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def buscar_wake(texto):
    """
    Devuelve (encontrado, texto_restante).

    1. Busca las variantes exactas de WAKE_VARIANTES.
    2. Si no hay, prueba coincidencia aproximada con una palabra o con
       dos palabras consecutivas unidas (Vosk suele separar "audi bot").
    """
    limpio = normalizar(texto)

    if not limpio:
        return False, ""

    variantes = sorted(
        (normalizar(v) for v in WAKE_VARIANTES),
        key=len,
        reverse=True,
    )

    for variante in variantes:
        patron = r"(?<!\w)" + re.escape(variante) + r"(?!\w)"
        coincidencia = re.search(patron, limpio)

        if coincidencia:
            restante = (
                limpio[:coincidencia.start()]
                + " "
                + limpio[coincidencia.end():]
            )
            return True, re.sub(r"\s+", " ", restante).strip()

    # Coincidencia aproximada: una palabra o dos palabras unidas.
    palabras = limpio.split()
    candidatos = [(i, i + 1, p) for i, p in enumerate(palabras)]
    candidatos += [
        (i, i + 2, palabras[i] + palabras[i + 1])
        for i in range(len(palabras) - 1)
    ]

    mejor = None  # (similitud, ini, fin)

    for ini, fin, candidato in candidatos:
        if not 5 <= len(candidato) <= 10:
            continue

        similitud = difflib.SequenceMatcher(
            None, candidato, "audibot"
        ).ratio()

        if similitud >= UMBRAL_WAKE and (mejor is None or similitud > mejor[0]):
            mejor = (similitud, ini, fin)

    if mejor:
        _, ini, fin = mejor
        restante = " ".join(palabras[:ini] + palabras[fin:])
        return True, restante.strip()

    return False, ""


def es_si(texto):
    limpio = normalizar(texto)
    return limpio in SI or limpio.startswith("si ")


def es_no(texto):
    limpio = normalizar(texto)
    return limpio in NO or limpio.startswith("no ")


class Detector:
    """Gestiona activacion, recepcion de orden y confirmacion."""

    def __init__(self, emitir, ventana=12, ventana_confirmar=8):
        self.emitir = emitir
        self.ventana = ventana
        self.ventana_confirmar = ventana_confirmar

        self.estado = "reposo"
        self.orden_pendiente = ""
        self.ultima_actividad = time.monotonic()

    def tocar(self):
        """Reinicia el contador de inactividad (p. ej. al terminar una pausa)."""
        self.ultima_actividad = time.monotonic()

    def activar(self):
        self.estado = "orden"
        self.orden_pendiente = ""
        self.ultima_actividad = time.monotonic()

        self.emitir(
            "wake",
            mensaje="AudiBot activado",
        )

    def recibir_orden(self, texto):
        # Se restauran las tildes y enies antes de enviar la orden a Electron y al servidor.
        texto = restaurar_acentos((texto or "").strip())

        if not texto:
            return

        self.orden_pendiente = texto
        self.estado = "confirmacion"
        self.ultima_actividad = time.monotonic()

        self.emitir(
            "orden",
            texto=texto,
            orden=texto,
            mensaje=texto,
        )

    def procesar(self, texto, parcial=False):
        texto = (texto or "").strip()

        if not texto:
            return

        # IMPORTANTE:
        # No activar con resultados parciales. Vosk puede transcribir
        # primero "autobot" y despues cambiar el resultado final.
        if parcial:
            if self.estado == "orden":
                self.emitir("parcial", texto=restaurar_acentos(texto))
            return

        self.ultima_actividad = time.monotonic()

        # 1. Esperando confirmacion de la orden.
        if self.estado == "confirmacion":
            if es_si(texto):
                orden = self.orden_pendiente
                self.estado = "reposo"
                self.orden_pendiente = ""

                self.emitir(
                    "confirmado",
                    texto=orden,
                    orden=orden,
                    mensaje="Orden confirmada",
                )
                return

            if es_no(texto):
                orden = self.orden_pendiente
                self.estado = "reposo"
                self.orden_pendiente = ""

                self.emitir(
                    "rechazado",
                    texto=orden,
                    orden=orden,
                    mensaje="Orden cancelada",
                )
                return

            # Permitir iniciar una nueva orden diciendo AudiBot.
            encontrado, restante = buscar_wake(texto)

            if encontrado:
                self.activar()

                if restante:
                    self.recibir_orden(restante)

            return

        # 2. Ya se activo AudiBot; ahora esperamos una orden.
        if self.estado == "orden":
            encontrado, restante = buscar_wake(texto)

            if encontrado:
                # Si la transcripcion final solo contiene la palabra
                # de activacion, seguir esperando la instruccion.
                if not restante:
                    return

                texto = restante

            self.recibir_orden(texto)
            return

        # 3. En reposo, buscar la palabra de activacion.
        encontrado, restante = buscar_wake(texto)

        if not encontrado:
            return

        self.activar()

        # Admite "AudiBot, revisa las ordenes de agosto".
        if restante:
            self.recibir_orden(restante)

    def revisar_timeout(self):
        ahora = time.monotonic()
        transcurrido = ahora - self.ultima_actividad

        if self.estado == "orden" and transcurrido >= self.ventana:
            self.estado = "reposo"
            self.orden_pendiente = ""

            self.emitir(
                "timeout",
                mensaje="No se recibio ninguna orden a tiempo",
            )

        elif (
            self.estado == "confirmacion"
            and transcurrido >= self.ventana_confirmar
        ):
            orden = self.orden_pendiente
            self.estado = "reposo"
            self.orden_pendiente = ""

            self.emitir(
                "timeout",
                texto=orden,
                orden=orden,
                mensaje="La confirmacion ha caducado",
            )


def main():
    # En Windows, con la salida redirigida, Python usa cp1252 y las tildes se rompen.
    for flujo in (sys.stdout, sys.stderr, sys.stdin):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        description="AudiBot: reconocimiento de voz"
    )

    parser.add_argument(
        "--model",
        default=MODELO_PREDETERMINADO,
        help="Ruta del modelo Vosk",
    )
    parser.add_argument(
        "--device",
        default=None,
        help="Dispositivo de microfono (numero o nombre)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Mostrar informacion de diagnostico",
    )
    parser.add_argument(
        "--ventana",
        type=float,
        default=12,
        help="Segundos para recibir una orden despues de activar",
    )
    parser.add_argument(
        "--confirmar",
        type=float,
        default=8,
        help="Segundos para confirmar una orden",
    )

    args = parser.parse_args()

    try:
        import sounddevice as sd
        from vosk import Model, KaldiRecognizer, SetLogLevel
    except ImportError as error:
        print(
            f"[ERROR] Falta una dependencia: {error}",
            file=sys.stderr,
            flush=True,
        )
        return 1

    SetLogLevel(0 if args.debug else -1)
    ruta_modelo = os.path.abspath(args.model)

    def debug(mensaje):
        if args.debug:
            print(
                f"[DEBUG] {mensaje}",
                file=sys.stderr,
                flush=True,
            )

    def emitir(evento, **datos):
        # Los eventos JSON van por stdout.
        # Los mensajes de diagnostico van por stderr.
        salida = {
            "evento": evento,
            "tipo": evento,
            "event": evento,
            **datos,
        }

        try:
            # ensure_ascii=True: las tildes viajan como \u00f3, que Electron decodifica bien
            # sea cual sea la codificacion de la consola de Windows.
            print(
                json.dumps(salida, ensure_ascii=True),
                flush=True,
            )
        except (BrokenPipeError, OSError):
            pass

    if not os.path.isdir(ruta_modelo):
        print(
            f"[ERROR] No existe la carpeta del modelo: {ruta_modelo}",
            file=sys.stderr,
            flush=True,
        )
        return 1

    print(
        f"[listener] Cargando modelo: {ruta_modelo}",
        file=sys.stderr,
        flush=True,
    )

    try:
        modelo = Model(ruta_modelo)
    except Exception as error:
        print(
            f"[ERROR] No se pudo cargar el modelo Vosk: {error}",
            file=sys.stderr,
            flush=True,
        )
        print(
            "[ERROR] Comprueba el modelo y su compatibilidad.",
            file=sys.stderr,
            flush=True,
        )
        return 1

    debug("Modelo cargado correctamente")

    recognizer = KaldiRecognizer(modelo, SAMPLE_RATE)
    audio_queue = queue.Queue()
    pausa_hasta = [0.0]
    pausa_lock = threading.Lock()

    dispositivo = args.device
    if dispositivo is not None and str(dispositivo).isdigit():
        dispositivo = int(dispositivo)

    def callback_audio(indata, frames, tiempo, status):
        if status and args.debug:
            print(
                f"[AUDIO] {status}",
                file=sys.stderr,
                flush=True,
            )

        audio_queue.put(bytes(indata))

    def leer_entrada():
        # Electron puede enviar "pausa 2" por stdin.
        try:
            for linea in sys.stdin:
                linea = linea.strip()

                if not linea:
                    continue

                partes = linea.split(maxsplit=1)
                comando = partes[0].lower()

                if comando == "pausa":
                    try:
                        segundos = (
                            float(partes[1])
                            if len(partes) > 1
                            else 1.5
                        )
                        segundos = max(0.0, min(segundos, 60.0))

                        with pausa_lock:
                            pausa_hasta[0] = max(
                                pausa_hasta[0],
                                time.monotonic() + segundos,
                            )

                        debug(f"Microfono en pausa durante {segundos} s")

                    except ValueError:
                        debug(f"Duracion de pausa no valida: {linea}")

                elif comando == "reanudar":
                    # Sustituye la pausa en curso: el microfono vuelve a escuchar
                    # pasados "segundos" (cola corta para no captar el eco final).
                    try:
                        segundos = (
                            float(partes[1])
                            if len(partes) > 1
                            else 0.0
                        )
                        segundos = max(0.0, min(segundos, 60.0))

                        with pausa_lock:
                            pausa_hasta[0] = time.monotonic() + segundos

                        debug(f"Microfono se reanuda en {segundos} s")

                    except ValueError:
                        debug(f"Duracion de reanudar no valida: {linea}")
        except (OSError, ValueError):
            pass

    threading.Thread(
        target=leer_entrada,
        daemon=True,
    ).start()

    detector = Detector(
        emitir,
        ventana=args.ventana,
        ventana_confirmar=args.confirmar,
    )

    estaba_en_pausa = False

    try:
        with sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=BLOQUE_AUDIO,
            device=dispositivo,
            dtype="int16",
            channels=1,
            callback=callback_audio,
        ):
            print(
                "[listener] AudiBot escuchando...",
                file=sys.stderr,
                flush=True,
            )

            while True:
                try:
                    datos_audio = audio_queue.get(timeout=0.25)
                except queue.Empty:
                    detector.revisar_timeout()
                    continue

                with pausa_lock:
                    en_pausa = time.monotonic() < pausa_hasta[0]

                if en_pausa:
                    # Se descarta el audio: es la voz del propio robot.
                    estaba_en_pausa = True
                    continue

                if estaba_en_pausa:
                    # Al salir de la pausa: limpiar lo que quedo a medias,
                    # vaciar el audio acumulado y reiniciar el tiempo de espera
                    # para que la pausa no se coma la ventana de la orden.
                    estaba_en_pausa = False
                    recognizer.Reset()
                    while not audio_queue.empty():
                        try:
                            audio_queue.get_nowait()
                        except queue.Empty:
                            break
                    detector.tocar()
                    debug("Fin de la pausa: te escucho")
                    continue

                if recognizer.AcceptWaveform(datos_audio):
                    resultado = json.loads(recognizer.Result())
                    texto = resultado.get("text", "").strip()

                    if texto:
                        # Lo que Vosk ha entendido, siempre visible en la terminal.
                        print(
                            f"[oigo] {texto}",
                            file=sys.stderr,
                            flush=True,
                        )
                        emitir("oido", texto=texto)
                        detector.procesar(texto, parcial=False)
                else:
                    resultado = json.loads(recognizer.PartialResult())
                    texto_parcial = resultado.get("partial", "").strip()

                    if texto_parcial:
                        detector.procesar(
                            texto_parcial,
                            parcial=True,
                        )

                detector.revisar_timeout()

    except KeyboardInterrupt:
        print(
            "\n[listener] AudiBot detenido.",
            file=sys.stderr,
            flush=True,
        )
        return 0

    except Exception as error:
        print(
            f"[ERROR] Fallo en el microfono o reconocimiento: {error}",
            file=sys.stderr,
            flush=True,
        )

        if args.debug:
            import traceback
            traceback.print_exc()

        return 1


if __name__ == "__main__":
    raise SystemExit(main())