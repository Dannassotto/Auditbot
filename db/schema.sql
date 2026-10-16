DROP TABLE IF EXISTS casos_legitimos;
DROP TABLE IF EXISTS errores_sembrados;
DROP TABLE IF EXISTS descuentos_autorizados;
DROP TABLE IF EXISTS facturas;
DROP TABLE IF EXISTS ordenes;
DROP TABLE IF EXISTS costos;
DROP TABLE IF EXISTS inventario;
DROP TABLE IF EXISTS productos;

CREATE TABLE productos (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,
    tipo TEXT NOT NULL CHECK (tipo IN ('renta', 'venta')),
    precio_lista REAL NOT NULL
);

CREATE TABLE inventario (
    producto_id INTEGER PRIMARY KEY REFERENCES productos(id),
    disponible INTEGER NOT NULL
);

CREATE TABLE costos (
    producto_id INTEGER NOT NULL REFERENCES productos(id),
    costo_unitario REAL NOT NULL,
    vigente_desde TEXT NOT NULL
);

CREATE TABLE ordenes (
    id INTEGER PRIMARY KEY,
    fecha TEXT NOT NULL,
    cliente TEXT NOT NULL,
    producto_id INTEGER NOT NULL REFERENCES productos(id),
    cantidad INTEGER NOT NULL,
    dias INTEGER NOT NULL,
    precio_unitario REAL NOT NULL
);

CREATE TABLE facturas (
    id INTEGER PRIMARY KEY,
    orden_id INTEGER NOT NULL REFERENCES ordenes(id),
    monto_facturado REAL NOT NULL
);

CREATE TABLE descuentos_autorizados (
    orden_id INTEGER PRIMARY KEY REFERENCES ordenes(id),
    porcentaje REAL NOT NULL,
    autorizado_por TEXT NOT NULL,
    motivo TEXT NOT NULL
);

-- RESPUESTAS CORRECTAS: solo para medir al agente. El agente NO debe consultarlas.
CREATE TABLE errores_sembrados (
    orden_id INTEGER NOT NULL,
    tipo TEXT NOT NULL
);

CREATE TABLE casos_legitimos (
    orden_id INTEGER NOT NULL,
    motivo TEXT NOT NULL
);