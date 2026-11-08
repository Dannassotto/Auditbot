"""Comprueba qué columnas toma evaluar.py de casos.xlsx."""
import sys
sys.path.insert(0, ".")
from evaluar import leer_tabla, _columna

cab, filas = leer_tabla("data/evaluacion/casos.xlsx")
print("encabezados:", cab)
print("columna de posición ->", _columna(cab, "pos"), "   (debe ser: pos)")
print("columna de etiqueta ->", _columna(cab, "etiqueta"))
print("casos en la hoja:", len(filas))