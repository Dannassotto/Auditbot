    # Reporte S3 y S4: AudiBot sobre datos reales (UCI Online Retail II)


    ## 1. Resumen

    - **Datos reales:** 1,067,371 líneas de un mayorista británico de artículos de regalo (UCI Online Retail II, 2009-12-01 a 2011-12-09). No son datos sintéticos.
    - **Corrida larga (S4):** 1,430 líneas en 12.2 h de reloj (9.0 h de trabajo y 3.2 h de pausa fija), 4,336 llamadas a Grok, 18.1 millones de tokens y **US$32.33**. 1,411 con decisión (98.7%), 19 sin decisión y 0 con error.
    - **Evaluación propia (S3):** 30 casos etiquetados por mí; 28 puntúan y 2 son dudosos. El agente coincide con mi criterio en **22 de 28 (78.6%)**. Fallos: 2 falsas alarmas, 3 alertas omitidas y 1 sin resultado.
    - **Costo por tarea resuelta:** **US$0.0229** en la corrida larga y **US$0.0230** en la evaluación. Por decisión que coincide con mi etiqueta: **US$0.0304**.
    - **Lo que no está probado:** que el agente acierte en general. 30 casos son pocos y las etiquetas son de una sola persona.

    ## 2. Datos y cola de trabajo

    - Las reglas deterministas filtran y solo las líneas marcadas van a Grok. Los atípicos estadísticos de precio y cantidad marcaban 201,106 y 106,087 líneas (una de cada cinco y una de cada diez), así que no filtraban nada y los descarté.
    - La cola quedó en 1,967 líneas (semilla fija 42): precio_cero 400, compra_repetida_mismo_dia 400, cancelacion_sin_compra 400, cantidad_negativa 388, linea_repetida_en_factura 299 y 80 líneas limpias de control, que Grok ve como `revision_de_rutina`.
    - Las cancelaciones cuentan solo desde 2010-06-01, porque antes la compra original puede ser anterior al inicio del archivo. Es un filtro mío, no un hecho del dataset.
    - El agente (`agent/uci.py`) tiene las herramientas `historial_precios`, `compras_cliente` y `lineas_de_factura`, más `registrar_decision`, con un máximo de 6 pasos por línea. UCI no trae costos, inventario ni facturas, y los montos están en libras.

    ## 3. Corrida larga (S4)

    | Dato | Valor |
    |---|---|
    | Líneas procesadas | 1,430 de 1,967 (se cortó por el límite de horas, a propósito) |
    | Con decisión / sin decisión / con error | 1,411 (98.7%) / 19 / 0 |
    | Llamadas a Grok | 4,336 (3.0 por línea) |
    | Latencia por llamada | media 7.4 s, mediana 6.0 s, p95 16.4 s, máx 58.8 s |
    | Tokens | 18,143,010 en total (15,780,288 de entrada y 337,512 de salida; el 11% restante no se desglosa en el registro de la API) |
    | Costo | US$32.3342 (por línea US$0.0226; por línea resuelta US$0.0229) |
    | Tiempo | 07:28 a 19:40: 12.2 h de reloj, 9.0 h de trabajo, 3.2 h de pausa fija |
    | Fallos de llamadas | 1 timeout, recuperado con un reintento |
    | Parada | `limite_horas` (la que pedí) |

    - **Pausa:**  puse --pausa 8 (8 s fijos entre líneas) para llegar a las 12 horas que pide el hito; sin ella habría tardado unas 9 h. Comando: .\run_corrida.bat logs\corrida --horas 12.2 --pausa 8 --tope 55. El evento inicio de eventos.jsonl (07:28:52) confirma pausa_s 8.0, tope_usd 55.0, 1,967 pendientes y gastado_antes 0.0 (bitácora limpia). El límite de 12.2 h causó la parada limite_horas.
    - **Lanzamiento:** con `run_corrida.bat` (vigilante): 0 caídas y 0 pasadas adicionales.
    - **«Desatendida»:** Lancé la corrida a las 07:28 y no intervine.
    - **Verificación independiente:** `verificar_bitacora.py` recalcula todo desde los `.jsonl` y coincide con el resumen: 1,430 órdenes únicas, 4,336 llamadas, 18,143,010 tokens y US$32.3342.

    Decisiones por regla en la corrida larga:

    | Regla | Líneas | Con decisión | Alertar | Ignorar | Sin decisión |
    |---|---|---|---|---|---|
    | cancelacion_sin_compra | 285 | 285 | 285 | 0 | 0 |
    | cantidad_negativa | 280 | 280 | 0 | 280 | 0 |
    | compra_repetida_mismo_dia | 287 | 268 | 121 | 147 | 19 |
    | linea_repetida_en_factura | 229 | 229 | 24 | 205 | 0 |
    | precio_cero | 290 | 290 | 1 | 289 | 0 |
    | control (líneas limpias) | 59 | 59 | 0 | 59 | 0 |

    - **Hallazgo:** en 4 de las 6 reglas el agente actúa casi como una regla fija (alerta todas las cancelaciones; ignora casi todos los precios cero y cantidades negativas). Solo discrimina de verdad en `compra_repetida_mismo_dia`.
    - **Control:** alertó 0 de 59. Eso no prueba que no se le escape nada.
    - **Las 19 líneas sin decisión** terminaron todas por «agotó los 6 pasos sin decidir»: el agente siguió consultando herramientas y alcanzó el máximo de 6 vueltas sin registrar una decisión. Las 19 son de `compra_repetida_mismo_dia` (6.6% de esa regla) y no se reintentaron. Costo total de esas 19

    ## 4. Pruebas previas

    | Corrida | Qué fue | Resultado |
    |---|---|---|
    | Humo (`logs/piloto`) | 5 líneas | 5 de 5 con decisión, 17 llamadas, US$0.1132 |
    | Chat sobre la factura C531179 | 1 línea, verificada a mano | Alertó correctamente: el cliente no tiene compra previa del producto; US$0.0179 |
    | Piloto (`logs/piloto2`), primera tanda | 46 líneas, unos 23 min, detenido a mano | 38 con decisión y 8 con error `SIN_CONEXION` (29 intentos fallidos); US$0.8248 |
    | Piloto, reanudación | Relanzado con el mismo comando | Sí: las 8 órdenes con error SIN_CONEXION de la primera pasada se completaron al reanudar. Resultado final: 46/46 con decisión, 0 errores. Total de la carpeta: US$1.0948 (dos reanudaciones con --max 10 --tope 3)

    - **Cortes de conexión:** Piloto (logs\piloto2): apagué y encendí el WiFi a propósito para probar la recuperación. Resultado: 29 SIN_CONEXION y 1 TIMEOUT (30 intentos fallidos). 8 órdenes terminaron en error en la primera pasada y se completaron al reanudar: 46/46 con decisión, 0 errores finales.
    Corrida de 12 h (logs\corrida): no toqué la conexión ni intervine. Hubo 1 TIMEOUT espontáneo, recuperado con un reintento, y 0 errores finales.

    ## 5. Evaluación propia (S3)

    Set de 30 líneas de la cola (5 por regla y 5 de control), elegidas con semilla fija, sin las primeras 5 del piloto. Mis criterios están en `data/evaluacion/criterios.md`. Las decisiones del agente se corrieron aparte (`logs/evaluacion`) y puse las etiquetas sin mirar esas decisiones. 

    - **Casos:** 30; puntúan 28, y 2 son dudosos y no puntúan.
    - **Coinciden con mi etiqueta:** 22 de 28 (78.6%). Sin contar el caso sin resultado: 22 de 27 (81.5%).
    - **Falsas alarmas:** 2. **Alertas omitidas:** 3. **Sin resultado:** 1.

    | Mi etiqueta \ Agente | alertar | ignorar | sin resultado |
    |---|---|---|---|
    | alertar | 5 | 3 | 0 |
    | ignorar | 2 | 17 | 1 |

    | Regla | Casos | Aciertos | Fallos |
    |---|---|---|---|
    | cancelacion_sin_compra | 5 | 5 | 0 |
    | cantidad_negativa | 5 | 4 | 1 |
    | compra_repetida_mismo_dia | 4 | 1 | 3 |
    | control | 5 | 5 | 0 |
    | linea_repetida_en_factura | 5 | 5 | 0 |
    | precio_cero | 4 | 2 | 2 |

    ### Los 6 casos donde falló

    | Caso | Regla | Fallo | Mi etiqueta | El agente |
    |---|---|---|---|---|
    | 92 | compra_repetida_mismo_dia | falsa alarma | ignorar | alertar: dice que el cliente «no suele repetir pedidos» |
    | 139 | compra_repetida_mismo_dia | falsa alarma | ignorar: 3 compras previas del producto, 2 uds a precio de mediana | alertar: dice lo mismo |
    | 235 | compra_repetida_mismo_dia | sin resultado | ignorar | «agotó los 6 pasos sin decidir», el mismo mecanismo que las 19 de la corrida larga |
    | 166 | precio_cero | alerta omitida | alertar: −24 uds a precio 0, unas £258 | ignorar: «ajuste interno sin cliente» |
    | 1589 | precio_cero | alerta omitida | alertar: −77 uds a precio 0, unas £289 | ignorar: «ajuste interno» |
    | 1400 | cantidad_negativa | alerta omitida | alertar: −30 uds, unas £508, descripción «?» | ignorar: «ajuste de inventario» |

    **Por qué creo que falló** (hipótesis mías):

    - **Las 3 alertas omitidas** son ajustes sin cliente. Mi etiqueta usó el valor estimado (cantidad × precio normal) y alerté sobre £100. El prompt del agente no le pide estimar ese valor, así que los trató a todos como ajustes internos.
    - **Las 2 falsas alarmas:** el agente afirma que el cliente no suele repetir, y en el caso 139 mi hoja indica 3 compras previas de ese producto. Su justificación parece contradecir el dato. El cliente 13141 ya había comprado este producto 4 veces, siempre 2 uds al mismo precio de £12,75. Es un patrón de compra habitual, no una anomalía.
    - **Regla más débil:** `compra_repetida_mismo_dia` tiene 1 acierto de 4 casos (con 4 casos no se puede hablar de una tasa). Los fallos por agotar los pasos salen más caros: el caso 235 costó US$0.0835 y 76.8 s, unas 3.6 veces el costo medio de una línea de la evaluación.

    ## 6. Costo por tarea resuelta y cuadre con el panel

    «Tarea resuelta» = línea con una decisión registrada. Todo el costo de la bitácora se divide entre ellas, incluido lo gastado en líneas fallidas.

    | Medida | Corrida larga | Evaluación |
    |---|---|---|
    | Costo total | US$32.3342 | US$0.6682 |
    | Tareas resueltas | 1,411 | 29 |
    | **Costo por tarea resuelta** | **US$0.0229** | **US$0.0230** |
    | Costo por decisión que coincide con mi etiqueta | no se mide | US$0.0304 (22 aciertos) |

    Los fallos cuestan: la decisión correcta sale un 32% más cara (0.0304 frente a 0.0230).

    Cuadre con el panel del reto (cuento solo lecturas finales, esperando un par de minutos tras cada corrida, porque el panel baja de más mientras hay llamadas en curso):

    | Tramo | Bitácora (US$) | Panel: baja (US$) |
    |---|---|---|
    | Humo (5 líneas) | 0.1132 | 0.12 (77.23 → 77.11) |
    | Chat, factura C531179 | 0.0179 | 0.02 (77.11 → 77.09) |
    | Piloto + corrida larga + evaluación (lectura combinada) | 34.0972 | 34.34 (77.09 → 42.75) |
    | **Total** | **34.2283** | **34.48 (77.23 → 42.75)** |

    El panel bajó US$34.48 y las bitácoras suman US$34.2283: una diferencia de US$0.25 (0.7%), en el sentido de que el panel cobró más. No pude confirmar su causa. Hipótesis: llamadas ya enviadas que se interrumpieron o expiraron (cobradas pero sin registrar), por ejemplo al detener el piloto con Ctrl+C, o el timeout de la corrida larga. 

    El panel muestra US$37.25 gastados en total de US$80.00. Los US$2.77 de diferencia con los US$34.48 anteriores son de trabajo previo a estas corridas (desarrollo).

    ## 7. Límites

    - 30 casos, estratificados (misma cantidad por regla): no reflejan la frecuencia natural de cada caso, y el porcentaje tiene mucha incertidumbre.
    - Etiquetas de una sola persona, con un criterio propio y sin respuesta oficial en UCI. Los dudosos no puntúan.
    - Antes de etiquetar vi el resumen agregado de la corrida larga (cuántas alertas por regla), no las decisiones individuales de estos casos. 
    - UCI son ventas de artículos de regalo, no renta de equipo: el dominio es cercano, no idéntico. No trae costos, inventario ni facturas, y los montos están en libras.
    - El 11% de los tokens no se desglosa en entrada y salida. Mi hipótesis es que son tokens de razonamiento del modelo, sin confirmar.
    - La corrida cubre 1,430 de las 1,967 líneas de la cola.
    - «Con decisión» no significa «decisión correcta»: eso lo mide solo la evaluación.

    ## 8. Mejoras propuestas (no aplicadas, porque los resultados se midieron así)

    - Subir el máximo de 6 pasos, o indicarle al agente que registre la decisión cuando ya tenga evidencia suficiente.
    - Pedirle que estime el valor (cantidad × precio normal) en los ajustes sin cliente.
    - Contrastar su justificación con los datos de la hoja, para detectar afirmaciones como «no suele repetir pedidos».

    ## 9. Evidencia

    - `evidencia/corrida/`: `bitacora.csv` (una fila por intento), `ordenes.jsonl`, `llamadas.jsonl`, `errores.jsonl`, `eventos.jsonl`, `resumen.md` y `verificacion.txt`.
    - `evidencia/evaluacion/`: bitácora de los 30 casos, `evaluacion.md`, `evaluacion_casos.csv` y `casos_etiquetado.xlsx` (mis etiquetas y motivos).
    - `evidencia/piloto2/`: bitácora del piloto.
    - `data/evaluacion/criterios.md`: mis criterios de etiquetado.
    - Pruebas: `python -m pytest`.