# Resumen de la corrida (logs\corrida)

- Órdenes enviadas al agente: 1430
- Con decisión registrada (ok): 1411 (98.7%) | sin decisión: 19 | con error: 0
- Llamadas a Grok: 4336 (3.0 por orden) | latencia media: 7.4 s
- Tokens: 18,143,010 en total (15,780,288 de entrada + 337,512 de salida; el resto no se desglosa en el registro de la API)
- Costo total: US$32.3342 (incluye llamadas de órdenes fallidas)
- Costo por orden enviada: US$0.0226
- Costo por tarea resuelta (ok): US$0.0229
- Llamadas sin costo reportado por la API: 0
- Primera orden: 2026-11-07 07:28 | última: 2026-11-07 19:40 (12.2 h de reloj, 9.0 h trabajando)

## Por regla

| Regla | Órdenes | ok | alertar | ignorar | sin decisión | error |
|---|---|---|---|---|---|---|
| cancelacion_sin_compra | 285 | 285 | 285 | 0 | 0 | 0 |
| cantidad_negativa | 280 | 280 | 0 | 280 | 0 | 0 |
| compra_repetida_mismo_dia | 287 | 268 | 121 | 147 | 19 | 0 |
| control_limpia | 59 | 59 | 0 | 59 | 0 | 0 |
| linea_repetida_en_factura | 229 | 229 | 24 | 205 | 0 | 0 |
| precio_cero | 290 | 290 | 1 | 289 | 0 | 0 |

## Control (líneas limpias elegidas al azar)

- Grok alertó 0 de 59 (0.0%). Son alertas que las reglas no habían levantado.

## Errores

- Órdenes que terminaron en error: ninguna
- Intentos de llamada fallidos (incluye los que se recuperaron con un reintento): TIMEOUT: 1

## Qué NO mide este resumen

- «ok» significa que Grok registró una decisión, no que sea correcta. UCI no trae respuestas verdaderas: la exactitud se mide aparte, con el set de evaluación de 30 casos.
