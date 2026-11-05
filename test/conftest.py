import sqlite3
from pathlib import Path

import pytest

SCHEMA = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


@pytest.fixture
def con():
    """Base SQLite en memoria con el esquema real del proyecto."""
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA.read_text(encoding="utf-8"))
    yield c
    c.close()


@pytest.fixture
def crear_orden(con):
    """Fábrica de órdenes: todo con valores por defecto 'limpios' (cero banderas)."""
    def _crear(*, id=1, fecha="2026-09-01", cliente="Cliente", pid=1, cant=1, dias=1, precio=100.0,
               costo=50.0, disponible=10, facturas="auto"):
        con.execute("INSERT OR IGNORE INTO productos VALUES (?, 'Equipo', 'renta', 100)", (pid,))
        if disponible is not None:
            con.execute("INSERT OR REPLACE INTO inventario VALUES (?, ?)", (pid, disponible))
        if costo is not None and not con.execute(
                "SELECT 1 FROM costos WHERE producto_id=?", (pid,)).fetchone():
            con.execute("INSERT INTO costos VALUES (?, ?, '2026-01-01')", (pid, costo))
        con.execute("INSERT INTO ordenes VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (id, fecha, cliente, pid, cant, dias, precio))
        if facturas == "auto":
            facturas = [round(precio * cant * dias, 2)]
        for m in facturas:
            con.execute("INSERT INTO facturas (orden_id, monto_facturado) VALUES (?, ?)", (id, m))
        return id
    return _crear