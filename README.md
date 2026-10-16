# AuditBot Agent

Agente autónomo de IA (núcleo de razonamiento: Grok) que encuentra el dinero que
las PyMEs B2B pierden sin darse cuenta.

## El problema

En empresas de renta y venta de equipo, lo cotizado, el inventario real y lo
facturado casi nunca coinciden. Se vende por debajo del costo cuando el proveedor
sube precios, se promete stock que no existe, se factura menos de lo acordado o
se cobra dos veces. Estos errores se descubren tarde, en el cierre de mes, o
nunca, porque auditar cada orden a mano es inviable y los sistemas actuales solo
registran datos sin investigar por qué algo no cuadra.

## Cómo funciona

1. **Reglas deterministas (sin IA):** revisan todas las órdenes y levantan
   banderas (margen negativo, stock insuficiente, sin factura, subfacturada,
   factura duplicada). No gastan tokens.
2. **Grok investiga** solo las órdenes con bandera, usando herramientas sobre la
   base de datos: historial de costos, órdenes similares, inventario, facturas y
   descuentos autorizados.
3. **Decide:** alertar o ignorar (falsa alarma), con causa, evidencia y pérdida
   estimada en MXN.
4. **Control de costos:** reintentos automáticos, tope de gasto, tope de órdenes
   por consulta (3 por defecto, 5 como máximo) y registro del costo real de cada
   llamada.

## Ejemplo (entrada -> salida)

Entrada, escrita en la interfaz web: `del 10 al 20 de agosto`

Salida: un resumen ("Revisé 1008 órdenes con las reglas y 75 tienen banderas. En
el periodo del 10 al 20 de agosto de 2026: 4. Investigué 3 órdenes: ...") y una
tarjeta por orden con la decisión de Grok, su causa, la pérdida estimada y la
evidencia desplegable.

Qué entiende la interfaz (no hace falta aprender un formato):

| Escribes | Qué hace |
|---|---|
| `18 de agosto` | solo las órdenes de ese día |
| `del 10 al 20 de agosto` | ese rango de fechas |
| `agosto` / `agosto de 2026` | el mes completo |
| `hoy`, `ayer`, `la semana`, `el último mes` | relativo al último día con datos |
| `orden 55` / `órdenes 3, 5 y 8` | esas órdenes concretas |
| `5 órdenes de agosto` | el mes, investigando hasta 5 |

La respuesta siempre dice qué periodo entendió. "Hoy" es el último día con datos
de la base (2026-10-14), no la fecha del sistema.

Ejemplo de decisión: una orden con margen negativo se ignora cuando tiene un
descuento autorizado (contrato marco por volumen); otra se marca con alerta
porque el costo del producto subió de $80 a $98 y la cotización no se actualizó.

## Estructura

```
servidor.py          Servidor local + interfaz web (python servidor.py)
index.html, css/, js/  Interfaz de chat
main.py              Alternativa por consola: reglas + agente
db/                  Esquema SQL y generador de datos simulados (determinista)
engine/margins.py    Reglas deterministas (sin IA)
agent/               Prompt, herramientas y ciclo de razonamiento
services/llm.py      Conexión a Grok, reintentos, tope de gasto y bitácora
logs/                Bitácora: decisiones y costo de cada llamada
```

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

O por consola: `python main.py 3` (Grok investiga 3 órdenes con bandera) o
`python main.py ordenes 71 20`. Las reglas siempre revisan todas las órdenes.

## Bitácora

- `logs/decisiones.jsonl`: cada decisión del agente con causa y evidencia.
- `logs/llamadas.jsonl`: tokens y costo real de cada llamada a Grok.

Grok solo se invoca sobre órdenes con bandera, por lo que el consumo está ligado
a trabajo real del agente. En las primeras corridas: 105 llamadas y US$0.68 para
15 decisiones, aprox. US$0.045 por orden investigada (medido en `llamadas.jsonl`).

## Datos

Simulados y 100 % deterministas (fecha base fija 2026-10-14, sin azar): 1,008
órdenes del 2026-08-15 al 2026-10-14 de un negocio ficticio de renta y venta de
equipo de construcción. Las reglas levantan 75 banderas: errores plantados y 15
casos legítimos que parecen error (descuentos autorizados, repartidos entre
todos los clientes), para medir si el agente distingue. Las tablas
`errores_sembrados` y `casos_legitimos` guardan las respuestas correctas y el
agente no las consulta.

## Estado: S1

El agente corre end-to-end contra Grok: reglas, investigación con herramientas
y decisión registrada. Se probó en 15 decisiones sobre 10 órdenes distintas de
los 5 tipos de error.

Limitaciones conocidas, que se corrigen en los siguientes hitos:

- Todavía no se mide precisión ni falsas alarmas con un set de evaluación.
- En alguna orden sin factura, Grok afirmó que la operación era válida sin
  evidencia suficiente.
- En facturas duplicadas, el monto reportado es dinero cobrado de más al
  cliente, no una pérdida de margen, y debe medirse por separado.
- Las acciones solo se registran en la bitácora; aún no bloquean ni ajustan
  órdenes en la base.

## Siguientes pasos

Más herramientas y manejo de errores (S2), set de evaluación de 30+ casos y
reporte de costo por tarea (S3), y corrida de 1,000 unidades de trabajo en 12
horas desatendido (S4).

## Licencia

MIT