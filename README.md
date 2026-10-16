# AuditBot Agent

Agente de IA (razonamiento con Grok) que encuentra el dinero que las PyMEs B2B
pierden sin darse cuenta.

## El problema

En empresas de renta y venta de equipo, lo cotizado, el inventario y lo facturado
casi nunca coinciden: se vende bajo costo cuando el proveedor sube precios, se
promete stock que no existe, se factura menos de lo acordado o se cobra dos veces.
Auditar cada orden a mano es inviable, así que estos errores se descubren tarde
o nunca.

## Cómo funciona

1. **Reglas deterministas (sin IA)** revisan todas las órdenes y levantan banderas:
   margen negativo, stock insuficiente, sin factura, subfacturada y factura
   duplicada. No gastan tokens.
2. **Grok investiga** solo las órdenes con bandera, con tres herramientas:
   historial de costos, órdenes similares y descuentos autorizados.
3. **Decide** alertar o ignorar (falsa alarma), con causa, evidencia y pérdida
   estimada en MXN.
4. **Control de costos:** reintentos, tope de gasto, máximo 5 órdenes por consulta
   y registro del costo real de cada llamada.

## Ejemplo (entrada -> salida)

- `orden 11` -> **Alerta**: margen negativo sin descuento autorizado (precio 30 vs
  costo vigente 31), pérdida estimada de 52 MXN.
- `orden 1` -> **Sin acción**: el margen negativo está cubierto por un descuento
  autorizado del 15% (contrato marco por volumen).

La interfaz también entiende periodos: `agosto`, `del 10 al 20 de agosto`, `hoy`,
`3 órdenes de agosto` u `órdenes 3, 5 y 8`. "Hoy" es el último día con datos de la
base (2026-10-14).

## Cómo correrlo

```
pip install -r requirements.txt
```

Copia `.env.example` a `.env` y escribe tu llave:

```
API_KEY=pk_tu_llave
BASE_URL=https://api.reto.pltk.mx/v1
```

Luego:

```
python db/seed.py      # crea la base con datos simulados (no usa Grok)
python servidor.py     # interfaz web en http://127.0.0.1:5000
```

Por consola: `python main.py 3` o `python main.py ordenes 71 20`.

## Estructura

```
servidor.py            Servidor local + interfaz web
index.html, css/, js/  Interfaz de chat
main.py                Alternativa por consola
db/                    Esquema SQL y generador de datos
engine/margins.py      Reglas deterministas (sin IA)
agent/                 Prompt, herramientas y ciclo de razonamiento
services/llm.py        Conexión a Grok, reintentos y tope de gasto
```

## Bitácora y costos

- `logs/decisiones.jsonl`: cada decisión con su causa y evidencia.
- `logs/llamadas.jsonl`: tokens y costo real de cada llamada a Grok.

Grok solo se invoca sobre órdenes con bandera, así que el consumo va ligado a
trabajo real del agente. Con el prompt actual cada orden investigada cuesta
aproximadamente US$0.01.

## Datos

Simulados y deterministas (fecha base fija 2026-10-14, sin azar): 1,008 órdenes
del 2026-08-15 al 2026-10-14 de un negocio ficticio de equipo de construcción.
Las reglas levantan 75 banderas: 60 errores plantados y 15 casos legítimos con
descuento autorizado, para medir si el agente distingue. Las tablas
`errores_sembrados` y `casos_legitimos` guardan las respuestas correctas y el
agente no las consulta.

## Estado: S1

El agente corre de principio a fin contra Grok: reglas, investigación con
herramientas y decisión registrada, probado en los cinco tipos de error.

Pendiente para los siguientes hitos:

- Medir precisión y falsas alarmas con un set de evaluación (S3).
- Las acciones solo se registran; todavía no bloquean ni ajustan órdenes.

## Licencia

MIT