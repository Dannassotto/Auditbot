# AuditBot Agent

**Agente de IA con razonamiento de Grok que identifica pÃ©rdidas econÃ³micas ocultas en las PyMEs B2B.**

AuditBot Agent automatiza la revisiÃ³n de Ã³rdenes de empresas dedicadas a la renta y venta de equipos, identificando inconsistencias entre costos, precios, inventario y facturaciÃ³n. Utiliza reglas deterministas para detectar posibles problemas y Grok para investigar los casos que requieren un anÃ¡lisis adicional.

## El problema

En las empresas de renta y venta de equipos, lo cotizado, lo disponible en inventario y lo facturado no siempre coincide. Esto puede ocasionar ventas por debajo del costo, promesas de stock inexistente, facturaciÃ³n inferior a lo acordado y cobros duplicados.

La revisiÃ³n manual de todas las Ã³rdenes consume tiempo y dificulta detectar estos problemas oportunamente. AuditBot busca automatizar la detecciÃ³n de inconsistencias y aportar evidencia para facilitar su investigaciÃ³n.

## CÃ³mo funciona

1. **DetecciÃ³n determinista:** las reglas de negocio revisan las Ã³rdenes y generan banderas por margen negativo, inventario insuficiente, ausencia de factura, subfacturaciÃ³n y posibles facturas duplicadas. Esta etapa no utiliza IA ni consume tokens de Grok.

2. **InvestigaciÃ³n con Grok:** el agente analiza las Ã³rdenes seÃ±aladas por las reglas y puede invocar tres herramientas para consultar informaciÃ³n relevante:
   - Historial de costos.
   - Ã“rdenes similares.
   - Descuentos autorizados.

3. **DecisiÃ³n fundamentada:** con la informaciÃ³n obtenida, el agente decide si corresponde generar una alerta o ignorar el caso por tratarse de una falsa alarma. La decisiÃ³n incluye la causa, las evidencias y la pÃ©rdida estimada en pesos mexicanos (MXN), cuando corresponde.

4. **Control de costos y errores:** se implementan mecanismos de reintento, un lÃ­mite de gasto, un mÃ¡ximo de diez Ã³rdenes por consulta y registros de las llamadas a Grok para facilitar el seguimiento del consumo.

5. **Registro de resultados:** las decisiones y los costos de las llamadas se guardan en archivos de registro para facilitar su revisiÃ³n y evaluaciÃ³n.

## Ejemplos de auditorÃ­a

- **Orden 11 â€” Alerta:** margen negativo sin descuento autorizado. El precio de venta es 30 y el costo vigente es 31. La pÃ©rdida estimada es de 52 MXN.
- **Orden 1 â€” Sin acciÃ³n:** el margen negativo estÃ¡ justificado por un descuento autorizado del 15 %, correspondiente a un contrato marco por volumen.

Estos ejemplos corresponden a los datos simulados del proyecto.

## Interfaz y control por voz

AuditBot cuenta con una interfaz web de chat y una ventana flotante desarrollada con Electron, que permite presentar el asistente en el escritorio.

La aplicaciÃ³n permite introducir Ã³rdenes mediante texto y estÃ¡ preparada para integrarse con el control por voz. El reconocimiento utiliza Python, Vosk y SoundDevice, con soporte para seleccionar un dispositivo de entrada, incluido WO Mic.

**Estado de la integraciÃ³n por voz:**
- El modelo de reconocimiento de voz carga correctamente.
- El micrÃ³fono WO Mic ha sido identificado por Python.
- El listener ha reconocido variantes de la palabra de activaciÃ³n y emitido el evento `wake`.
- La ventana flotante y el saludo funcionan mediante la opciÃ³n de prueba manual de Electron.

La interfaz utiliza el servicio de sÃ­ntesis de voz Edge TTS a travÃ©s del servidor local.

## Consultas disponibles

La interfaz permite interpretar consultas por periodos y Ã³rdenes especÃ­ficas, por ejemplo:

- `agosto`
- `del 10 al 20 de agosto`
- `3 Ã³rdenes de agosto`
- `Ã³rdenes 3, 5 y 8`
- `orden 11`

## Tecnologi­as

- **Python:** reglas de negocio, agente, herramientas y reconocimiento de voz.
- **Grok:** razonamiento y anÃ¡lisis de Ã³rdenes seÃ±aladas.
- **API compatible con OpenAI:** comunicaciÃ³n con el modelo mediante el endpoint del reto.
- **SQLite:** almacenamiento y consulta de las Ã³rdenes.
- **Servidor local (http.server de Python) e interfaz web:** chat y panel de la corrida.
- **Electron:** ventana flotante de escritorio.
- **Vosk y SoundDevice:** reconocimiento de voz local.
- **Edge TTS:** sÃ­ntesis de voz.

## Como ejecutar el proyecto

### 1. Instalar dependencias

Desde la raÃ­z del repositorio:

```bash
pip install -r requirements.txt
```

Si vas a utilizar el control por voz, instala tambiÃ©n las dependencias de audio que requiere el proyecto, si no estÃ¡n incluidas en `requirements.txt`.

### 2. Configurar las variables de entorno

Copia `.env.example` a `.env` y configura la llave proporcionada para el reto:

```dotenv
API_KEY=pk_tu_llave
BASE_URL=https://api.reto.pltk.mx/v1
```

No publiques el archivo `.env` ni incluyas claves privadas en el repositorio.

### 3. Crear la base de datos de prueba

```bash
python db/seed.py
```

Este comando genera los datos simulados del proyecto y no necesita invocar Grok.

### 4. Iniciar el servidor web

```bash
python servidor.py
```

La interfaz web queda disponible en:

`http://127.0.0.1:5000`

### 5. Ejecutar auditorÃ­as desde la consola

```bash
python main.py 3
```

TambiÃ©n es posible ejecutar consultas sobre Ã³rdenes especÃ­ficas mediante los argumentos admitidos por `main.py`.

### 6. Iniciar la interfaz de escritorio

En Windows, se puede utilizar el archivo `iniciar.bat` de la raÃ­z del proyecto, una vez configuradas las dependencias y el entorno.

El arranque debe coordinar el servidor, la ventana flotante de Electron y el listener de voz, evitando iniciar procesos duplicados.

Para probar Ãºnicamente el reconocimiento de voz:

```bash
python listener.py --debug --device 1
```

El Ã­ndice del dispositivo puede variar segÃºn la configuraciÃ³n de audio del equipo.

## Estructura del proyecto

```text
Auditbot/
│
├── agent/
│   ├── uci.py
│   └── loop.py
│
├── db/
│   ├── cargar_uci.py
│   ├── medir_uci.py
│   ├── preparar_cola.py
│   ├── preparar_evaluacion.py
│   ├── ver_cliente.py
│   ├── ver_columnas.py
│   └── seed.py
│
├── engine/
│   └── margins.py
│
├── services/
│   ├── llm.py
│   └── chat_uci.py
│
├── js/
│   ├── app.js
│   └── robot.js
│
├── electron/
│   ├── main.js
│   ├── package.json
│   └── ...
│
├── test/
│   ├── conftest.py
│   ├── test_reglas.py
│   ├── test_chat_uci.py
│   ├── test_evaluar.py
│   ├── test_exportar_bitacora.py
│   ├── test_servidor.py
│   └── test_verificar_bitacora.py
│
├── evidencia/
│   ├── corrida/
│   │   ├── bitacora.csv
│   │   ├── errores.jsonl
│   │   ├── eventos.jsonl
│   │   ├── llamadas.jsonl
│   │   ├── ordenes.jsonl
│   │   ├── resumen.md
│   │   └── verificacion.txt
│   │
│   ├── evaluacion/
│   │   ├── bitacora.csv
│   │   ├── casos_etiquetado.xlsx
│   │   ├── consola.txt
│   │   ├── evaluacion.md
│   │   ├── evaluacion_casos.csv
│   │   ├── eventos.jsonl
│   │   ├── llamadas.jsonl
│   │   └── ordenes.jsonl
│   │
│   └── piloto2/
│       ├── bitacora.csv
│       ├── errores.jsonl
│       ├── eventos.jsonl
│       ├── llamadas.jsonl
│       ├── ordenes.jsonl
│       └── resumen.md
│
├── data/
│   └── (bases de datos locales, excluidas de Git)
│
├── logs/
│   └── (registros de ejecución, excluidos de Git)
│
├── .env
├── .env.example
├── .gitignore
├── README.md
├── requirements.txt
├── main.py
├── servidor.py
├── listener.py
├── worker.py
├── evaluar.py
├── resumen_corrida.py
├── verificar_bitacora.py
├── exportar_bitacora.py
├── run_corrida.bat
├── aplicar_parches.py
└── reporte.md
```

La estructura exacta puede incluir otros archivos auxiliares y recursos de la interfaz.

## BitÃ¡cora y costos

- `logs/decisiones.jsonl`: registra las decisiones del agente, sus causas y evidencias.
- `logs/llamadas.jsonl`: registra informaciÃ³n de las llamadas a Grok, incluidos tokens y costos cuando estÃ¡n disponibles.

Grok se utiliza para investigar las Ã³rdenes seÃ±aladas por las reglas deterministas, con el objetivo de limitar las llamadas innecesarias y controlar el consumo.

El costo medido sobre datos reales es de unos US$0.0229 por tarea resuelta (ver reporte.md). La estimaciÃ³n inicial de US$0.01 por orden no se cumpliÃ³ en UCI.

## Datos de prueba

El proyecto utiliza una base de datos simulada y determinista con 1.008 Ã³rdenes correspondientes al periodo del 15 de agosto al 14 de octubre de 2026, para un negocio ficticio de equipos de construcciÃ³n.

Los datos contienen 75 casos seÃ±alados por las reglas:
- 60 errores plantados.
- 15 casos legÃ­timos con descuentos autorizados.

Las tablas `errores_sembrados` y `casos_legitimos` almacenan las respuestas esperadas para evaluar los resultados. Estas tablas no forman parte de las consultas del agente durante la investigaciÃ³n.

**Nota:** estos datos simulados se mantienen para pruebas. Las etapas S3 y S4 usan datos reales (UCI Online Retail II), descritos mÃ¡s abajo.

## Estado del proyecto

### S1 â€” Agente base

La versiÃ³n base implementa el flujo de auditorÃ­a de extremo a extremo: reglas deterministas, investigaciÃ³n con Grok mediante herramientas y registro de decisiones. Se probaron los cinco tipos de error contemplados.

### S2 â€” Herramientas y robustez

El proyecto cuenta con las tres herramientas de investigaciÃ³n, control de consumo y registros de decisiones y llamadas. Los puntos que esta secciÃ³n dejaba por documentar quedaron cubiertos en S3 y S4:

- **Ejecuciones reales del agente:** una corrida de 1,430 lÃ­neas, un piloto, un humo de 5 lÃ­neas y una evaluaciÃ³n de 30 casos (`evidencia/`).
- **Manejo de errores:** cada fallo con su cÃ³digo en `errores.jsonl`, reintentos solo para errores temporales y reanudaciÃ³n sin repetir trabajo. Probado con pÃ©rdidas de conexiÃ³n durante el piloto (`evidencia/piloto2/`).
- **Video:** entregado en S2.

La interfaz de escritorio y la voz se describen mÃ¡s arriba; no forman parte de la evaluaciÃ³n de S3 y S4.

### S3 y S4 â€” Datos reales de UCI Online Retail II

Las reglas deterministas filtran y Grok investiga solo las lÃ­neas marcadas, con herramientas y un tope de gasto. Las etapas anteriores usaban datos semilla; estas usan datos reales.

**Hecho y verificable**

- **Datos reales:** 1,067,371 lÃ­neas cargadas en `data/uci.db` (`db/cargar_uci.py`).
- **Cola de trabajo:** 1,967 lÃ­neas con semilla fija (`db/preparar_cola.py`), reproducible.
- **Corrida de 12.2 h:** 1,430 lÃ­neas, 4,336 llamadas, US$32.33. 1,411 con decisiÃ³n (98.7%), 19 sin decisiÃ³n (agotaron el mÃ¡ximo de 6 pasos) y 0 con error. BitÃ¡cora completa en `evidencia/corrida/` (incluye `bitacora.csv`, una fila por intento).
- **EvaluaciÃ³n propia:** 30 casos etiquetados por mÃ­, con motivos (`data/evaluacion/`). Coincide el 78.6% (22 de 28 puntuables). Los 6 fallos estÃ¡n explicados en `reporte.md`.
- **Costo por tarea resuelta:** US$0.0229 en la corrida y US$0.0230 en la evaluaciÃ³n.
- **Pruebas:** `python -m pytest` (106 pruebas, todas pasan).
- **Informe completo:** `reporte.md`.

**Archivos principales de S3 y S4:** `agent/uci.py` (agente sobre UCI), `worker.py` (corrida reanudable), `run_corrida.bat` (vigilante), `resumen_corrida.py`, `verificar_bitacora.py`, `exportar_bitacora.py`, `evaluar.py`, `services/chat_uci.py` y `db/preparar_evaluacion.py`.

**CÃ³mo reproducirlo**

```
python db\cargar_uci.py                  # necesita data\uci\uci.xlsx (no se sube al repo)
python db\preparar_cola.py
python worker.py --dir logs\evaluacion --casos data\evaluacion\casos.xlsx --tope 3
python evaluar.py data\evaluacion\casos.xlsx logs\evaluacion
python worker.py --dir logs\corrida --horas 12.2 --pausa 8 --tope 55
python resumen_corrida.py logs\corrida
python verificar_bitacora.py logs\corrida
python exportar_bitacora.py logs\corrida
python servidor.py                       # interfaz; el botÃ³n Â«Ver corridaÂ» muestra el avance
```

La corrida larga se lanza desde la consola (`worker.py` o `run_corrida.bat`); la interfaz solo la observa leyendo `logs/`. La llave va en `.env` (`API_KEY`, `BASE_URL`), que no se sube.

**Alcance y lÃ­mites declarados**

- El set de evaluaciÃ³n tiene 30 casos y un solo etiquetador: el porcentaje es orientativo.
- La corrida cubre 1,430 de las 1,967 lÃ­neas de la cola; se cortÃ³ por el lÃ­mite de horas elegido.
- UCI no es renta de equipo: el dominio es cercano, no idÃ©ntico. Las reglas de margen, inventario y factura de la base simulada (`data/auditbot.db`) no aplican a UCI.

## Licencia

Este proyecto se distribuye bajo la licencia MIT.
