# AuditBot Agent

**Agente de IA con razonamiento de Grok que identifica pérdidas económicas ocultas en las PyMEs B2B.**

AuditBot Agent automatiza la revisión de órdenes de empresas dedicadas a la renta y venta de equipos, identificando inconsistencias entre costos, precios, inventario y facturación. Utiliza reglas deterministas para detectar posibles problemas y Grok para investigar los casos que requieren un análisis adicional.

## El problema

En las empresas de renta y venta de equipos, lo cotizado, lo disponible en inventario y lo facturado no siempre coincide. Esto puede ocasionar ventas por debajo del costo, promesas de stock inexistente, facturación inferior a lo acordado y cobros duplicados.

La revisión manual de todas las órdenes consume tiempo y dificulta detectar estos problemas oportunamente. AuditBot busca automatizar la detección de inconsistencias y aportar evidencia para facilitar su investigación.

## Cómo funciona

1. **Detección determinista:** las reglas de negocio revisan las órdenes y generan banderas por margen negativo, inventario insuficiente, ausencia de factura, subfacturación y posibles facturas duplicadas. Esta etapa no utiliza IA ni consume tokens de Grok.

2. **Investigación con Grok:** el agente analiza las órdenes señaladas por las reglas y puede invocar tres herramientas para consultar información relevante:
   - Historial de costos.
   - Órdenes similares.
   - Descuentos autorizados.

3. **Decisión fundamentada:** con la información obtenida, el agente decide si corresponde generar una alerta o ignorar el caso por tratarse de una falsa alarma. La decisión incluye la causa, las evidencias y la pérdida estimada en pesos mexicanos (MXN), cuando corresponde.

4. **Control de costos y errores:** se implementan mecanismos de reintento, un límite de gasto, un máximo de cinco órdenes por consulta y registros de las llamadas a Grok para facilitar el seguimiento del consumo.

5. **Registro de resultados:** las decisiones y los costos de las llamadas se guardan en archivos de registro para facilitar su revisión y evaluación.

## Ejemplos de auditoría

- **Orden 11 — Alerta:** margen negativo sin descuento autorizado. El precio de venta es 30 y el costo vigente es 31. La pérdida estimada es de 52 MXN.
- **Orden 1 — Sin acción:** el margen negativo está justificado por un descuento autorizado del 15 %, correspondiente a un contrato marco por volumen.

Estos ejemplos corresponden a los datos simulados del proyecto.

## Interfaz y control por voz

AuditBot cuenta con una interfaz web de chat y una ventana flotante desarrollada con Electron, que permite presentar el asistente en el escritorio.

La aplicación permite introducir órdenes mediante texto y está preparada para integrarse con el control por voz. El reconocimiento utiliza Python, Vosk y SoundDevice, con soporte para seleccionar un dispositivo de entrada, incluido WO Mic.

**Estado de la integración por voz:**
- El modelo de reconocimiento de voz carga correctamente.
- El micrófono WO Mic ha sido identificado por Python.
- El listener ha reconocido variantes de la palabra de activación y emitido el evento `wake`.
- La ventana flotante y el saludo funcionan mediante la opción de prueba manual de Electron.
- Está pendiente terminar de verificar la activación automática por voz, el saludo y el flujo completo de órdenes mediante el micrófono.

La interfaz utiliza el servicio de síntesis de voz Edge TTS a través del servidor local.

## Consultas disponibles

La interfaz permite interpretar consultas por periodos y órdenes específicas, por ejemplo:

- `agosto`
- `del 10 al 20 de agosto`
- `hoy`
- `3 órdenes de agosto`
- `órdenes 3, 5 y 8`
- `orden 11`

En los datos simulados actuales, `hoy` corresponde al último día disponible en la base de datos: 14 de octubre de 2026.

## Tecnologías

- **Python:** reglas de negocio, agente, herramientas y reconocimiento de voz.
- **Grok:** razonamiento y análisis de órdenes señaladas.
- **API compatible con OpenAI:** comunicación con el modelo mediante el endpoint del reto.
- **SQLite:** almacenamiento y consulta de las órdenes.
- **Flask y tecnologías web:** servidor local e interfaz de chat.
- **Electron:** ventana flotante de escritorio.
- **Vosk y SoundDevice:** reconocimiento de voz local.
- **Edge TTS:** síntesis de voz.

## Cómo ejecutar el proyecto

### 1. Instalar dependencias

Desde la raíz del repositorio:

```bash
pip install -r requirements.txt
```

Si vas a utilizar el control por voz, instala también las dependencias de audio que requiere el proyecto, si no están incluidas en `requirements.txt`.

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

### 5. Ejecutar auditorías desde la consola

```bash
python main.py 3
```

También es posible ejecutar consultas sobre órdenes específicas mediante los argumentos admitidos por `main.py`.

### 6. Iniciar la interfaz de escritorio

En Windows, se puede utilizar el archivo `iniciar.bat` de la raíz del proyecto, una vez configuradas las dependencias y el entorno.

El arranque debe coordinar el servidor, la ventana flotante de Electron y el listener de voz, evitando iniciar procesos duplicados.

Para probar únicamente el reconocimiento de voz:

```bash
python listener.py --debug --device 1
```

El índice del dispositivo puede variar según la configuración de audio del equipo.

## Estructura del proyecto

```text
Auditbot/
├── agent/
│   └── ...
├── db/
│   ├── seed.py
│   └── ...
├── engine/
│   └── margins.py
├── services/
│   └── llm.py
├── electron/
│   └── ...
├── logs/
│   ├── decisiones.jsonl
│   └── llamadas.jsonl
├── listener.py
├── servidor.py
├── main.py
├── iniciar.bat
├── requirements.txt
├── .env.example
└── README.md
```

La estructura exacta puede incluir otros archivos auxiliares y recursos de la interfaz.

## Bitácora y costos

- `logs/decisiones.jsonl`: registra las decisiones del agente, sus causas y evidencias.
- `logs/llamadas.jsonl`: registra información de las llamadas a Grok, incluidos tokens y costos cuando están disponibles.

Grok se utiliza para investigar las órdenes señaladas por las reglas deterministas, con el objetivo de limitar las llamadas innecesarias y controlar el consumo.

El costo aproximado de US$0.01 por orden investigada es una estimación del proyecto y puede variar según los tokens utilizados, el modelo y las tarifas aplicables.

## Datos de prueba

El proyecto utiliza una base de datos simulada y determinista con 1.008 órdenes correspondientes al periodo del 15 de agosto al 14 de octubre de 2026, para un negocio ficticio de equipos de construcción.

Los datos contienen 75 casos señalados por las reglas:
- 60 errores plantados.
- 15 casos legítimos con descuentos autorizados.

Las tablas `errores_sembrados` y `casos_legitimos` almacenan las respuestas esperadas para evaluar los resultados. Estas tablas no forman parte de las consultas del agente durante la investigación.

**Limitación actual:** los datos son simulados. La incorporación y evaluación con datos reales o muestras públicas es un objetivo para las siguientes etapas.

## Estado del proyecto

### S1 — Agente base

La versión base implementa el flujo de auditoría de extremo a extremo: reglas deterministas, investigación con Grok mediante herramientas y registro de decisiones. Se han probado los cinco tipos de error contemplados.

### S2 — Herramientas y robustez

El proyecto cuenta con las tres herramientas de investigación, mecanismos de control de consumo y registros de decisiones y llamadas. La interfaz de escritorio y el reconocimiento de voz se encuentran en proceso de integración y pruebas.

Para completar y documentar S2, queda verificar y evidenciar:
- Ejecuciones completas del agente con las herramientas reales.
- Manejo de errores durante consultas y llamadas al modelo.
- Al menos diez ejecuciones reales del agente, según los requisitos del reto.
- Una demostración en video de aproximadamente dos minutos, mostrando un caso de principio a fin, las herramientas y un fallo controlado.

## Licencia

Este proyecto se distribuye bajo la licencia MIT.