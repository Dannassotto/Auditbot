# Evaluación del agente sobre datos reales (UCI Online Retail II)

## Resultado

- Casos evaluados: **30** (puntúan 28; 2 marcados como dudosos y no puntúan)
- Coinciden con mi etiqueta humana: **22 de 28 (78.6%)**
- Si se descartan los casos sin resultado: 81.5%
- Falsas alarmas (el agente alertó y yo ignoraba): **2**
- Alertas omitidas (el agente ignoró y yo alertaba): **3**
- Sin resultado (error o sin decisión): **1**

## Matriz de confusión (filas = mi etiqueta, columnas = el agente)

| | alertar | ignorar | sin resultado |
|---|---|---|---|
| alertar | 5 | 3 | 0 |
| ignorar | 2 | 17 | 1 |

## Por regla

| Regla | Casos | Aciertos | Fallos |
|---|---|---|---|
| cancelacion_sin_compra | 5 | 5 | 0 |
| cantidad_negativa | 5 | 4 | 1 |
| compra_repetida_mismo_dia | 4 | 1 | 3 |
| control | 5 | 5 | 0 |
| linea_repetida_en_factura | 5 | 5 | 0 |
| precio_cero | 4 | 2 | 2 |

## Casos donde falló (6)

- **Caso 92** (compra_repetida_mismo_dia): falsa alarma. Humano: ignorar (1 ud a 1,65 igual a la mediana con importe minimo de 76 lineas en la factura no relevante). Agente: alertar — Cliente 14953 compró el producto 22557 (cantidad 1, precio 1.65) en dos facturas distintas el mismo día y no suele repetir pedidos.
- **Caso 139** (compra_repetida_mismo_dia): falsa alarma. Humano: ignorar (Cliente con 3 compras previas del producto; 2 uds a precio de mediana: recompra normal). Agente: alertar — Cliente 13141 compró 2 uds. de 22423 a 12.75 en dos facturas distintas el mismo día y no suele repetir pedidos.
- **Caso 166** (precio_cero): alerta omitida. Humano: alertar (salida de -24 uds a precio 0, por un valor estimado de 258 (24*10,75)). Agente: ignorar — Ajuste interno sin cliente: cantidad -24 a precio 0 (el producto se vende a 10.75).
- **Caso 235** (compra_repetida_mismo_dia): sin resultado. Humano: ignorar (12 uds a 0.85, igual a la mediana, importe bajo (~£10) en factura de 26 líneas). Agente: sin_resultado
- **Caso 1400** (cantidad_negativa): alerta omitida. Humano: alertar (Ajuste sin cliente, -30 uds, valor ~£508, sobre el umbral; descripción “?”). Agente: ignorar — Ajuste de inventario sin cliente: cantidad -30 a precio 0 en factura 537032.
- **Caso 1589** (precio_cero): alerta omitida. Humano: alertar (Ajuste sin cliente, -77 uds, valor ~£289, sobre el umbral). Agente: ignorar — Ajuste interno sin cliente de 77 unidades a precio 0, no una venta.

## Costo

- Tareas resueltas (con decisión registrada): **29**
- Llamadas a Grok: 90; tokens: 370,506
- Costo total: **US$ 0.6682**
- **Costo por tarea resuelta: US$ 0.0230**
- Costo por acierto (cuenta también lo gastado en errores y fallos): US$ 0.0304

## Límites

- El set es pequeño y estratificado (misma cantidad por regla), no refleja la frecuencia natural de los casos.
- Las etiquetas son mías, hechas antes de ver las decisiones del agente; los dudosos no puntúan.
- UCI Online Retail II son transacciones reales de un mayorista británico de artículos de regalo, no de renta de equipo: el dominio es cercano, no idéntico. No trae costos, inventario ni facturas.