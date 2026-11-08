# Resumen de la corrida (logs\piloto2)

- Órdenes enviadas al agente: 46
- Con decisión registrada (ok): 38 (82.6%) | sin decisión: 0 | con error: 8
- Llamadas a Grok: 117 (2.5 por orden) | latencia media: 7.4 s
- Tokens: 459,205 en total (398,377 de entrada + 8,894 de salida; el resto no se desglosa en el registro de la API)
- Costo total: US$0.8248 (incluye llamadas de órdenes fallidas)
- Costo por orden enviada: US$0.0179
- Costo por tarea resuelta (ok): US$0.0217
- Llamadas sin costo reportado por la API: 0
- Primera orden: 2026-11-07 03:58 | última: 2026-11-07 04:21 (0.4 h de reloj, 0.2 h trabajando)

## Por regla

| Regla | Órdenes | ok | alertar | ignorar | sin decisión | error |
|---|---|---|---|---|---|---|
| cancelacion_sin_compra | 11 | 8 | 8 | 0 | 0 | 3 |
| cantidad_negativa | 12 | 12 | 0 | 12 | 0 | 0 |
| compra_repetida_mismo_dia | 8 | 6 | 4 | 2 | 0 | 2 |
| control_limpia | 1 | 1 | 0 | 1 | 0 | 0 |
| linea_repetida_en_factura | 8 | 6 | 0 | 6 | 0 | 2 |
| precio_cero | 6 | 5 | 0 | 5 | 0 | 1 |

## Control (líneas limpias elegidas al azar)

- Grok alertó 0 de 1 (0.0%). Son alertas que las reglas no habían levantado.

## Errores

- Órdenes que terminaron en error: SIN_CONEXION: 8
- Intentos de llamada fallidos (incluye los que se recuperaron con un reintento): TIMEOUT: 1, SIN_CONEXION: 29

## Qué NO mide este resumen

- «ok» significa que Grok registró una decisión, no que sea correcta. UCI no trae respuestas verdaderas: la exactitud se mide aparte, con el set de evaluación de 30 casos.
