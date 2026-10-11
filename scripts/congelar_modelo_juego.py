"""Congela el modelo del juego en app/modelo_juego.json (hay que hacer commit).

Entrena la misma regresión logística que scripts/entrenar_modelo.py (mismo
pipeline, mismo reparto entrenamiento/prueba con la semilla 42) con los
estudiantes de la base del .env, y guarda sus parámetros. Así el juego da hoy
las mismas probabilidades que la pestaña Predicción, pero no cambia en cada
deploy. Ver app/modelo_juego.py y docs/CONTRATO_LABORATORIO.md.

Uso (desde la raíz del proyecto):
    python -m scripts.congelar_modelo_juego
    python -m scripts.congelar_modelo_juego --version 2026-11-02
"""

import argparse
import hashlib
import json
import sys
from datetime import date

import joblib
import sklearn
from sklearn.model_selection import train_test_split

from app import ml, modelo_juego
from app.database import engine
from scripts.entrenar_modelo import leer_estudiantes

MUESTRAS_DE_CONTROL = 8


def huella(estudiantes) -> str:
    """MD5 de los datos usados: permite saber con qué datos se congeló."""
    columnas = ["id", *ml.VARIABLES, "promedio"]
    texto = estudiantes.sort_values("id")[columnas].to_csv(index=False)
    return hashlib.md5(texto.encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", default=date.today().isoformat())
    args = parser.parse_args()

    estudiantes = leer_estudiantes()
    if len(estudiantes) < 50:
        print(f"Solo hay {len(estudiantes)} estudiantes en '{engine.url.database}'.", file=sys.stderr)
        return 1
    X, y = ml.preparar_datos(estudiantes)
    X_train, X_test, y_train, _ = train_test_split(X, y, test_size=0.2, stratify=y, random_state=ml.SEMILLA)
    pipeline = ml.construir_pipeline("logistica").fit(X_train, y_train)

    # Entradas de prueba con la probabilidad que da scikit-learn: los tests
    # comprueban que la inferencia propia las reproduce.
    muestras = X_test.head(MUESTRAS_DE_CONTROL)
    control = [
        {"entrada": {k: (v.item() if hasattr(v, "item") else v) for k, v in fila.items()},
         "probabilidad": ml.probabilidad_riesgo(pipeline, fila.to_dict())}
        for _, fila in muestras.iterrows()
    ]
    datos = modelo_juego.exportar(pipeline, X_train, version=args.version, control=control, descripcion={
        "entrenamiento": {"estudiantes": len(X_train), "en_riesgo": int(y_train.sum())},
        "total_estudiantes": len(X),
        "huella_datos": huella(estudiantes),
        "semilla": ml.SEMILLA,
        "umbral_riesgo_promedio": ml.UMBRAL_RIESGO,
        "version_sklearn": sklearn.__version__,
    })

    # Comprobaciones antes de guardar.
    juego = modelo_juego.ModeloJuego(datos)
    error = max(abs(juego.probabilidad(c["entrada"]) - c["probabilidad"]) for c in control)
    print(f"Inferencia propia vs scikit-learn (muestras de control): error máximo {error:.2e}")
    if error > 1e-9:
        print("La inferencia propia no reproduce a scikit-learn: no se guarda.", file=sys.stderr)
        return 1
    if ml.RUTA_MODELO.exists():
        servido = joblib.load(ml.RUTA_MODELO)
        if ml.nombre_modelo(servido) == ml.NOMBRES["logistica"]:
            diferencia = max(abs(juego.probabilidad(fila) - ml.probabilidad_riesgo(servido, fila))
                             for fila in X.to_dict("records"))
            print(f"Diferencia con el modelo del build (modelos/riesgo.joblib) en los {len(X)} "
                  f"estudiantes: {diferencia:.2e}")
        else:
            print("Aviso: el modelo del build no es la logística; el juego y la pestaña Predicción "
                  "darán probabilidades distintas.")

    modelo_juego.RUTA.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Guardado {modelo_juego.RUTA.relative_to(ml.RAIZ)} (versión {args.version}). Haz commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
