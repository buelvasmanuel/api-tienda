"""Piezas del modelo de riesgo académico compartidas por el script de
entrenamiento (scripts/entrenar_modelo.py) y la API (routers/prediccion.py).

Tenerlas en un solo sitio garantiza que ambos usan las mismas variables, en
el mismo orden y con el mismo preprocesado.
"""

from pathlib import Path

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.tree import DecisionTreeClassifier

RAIZ = Path(__file__).resolve().parent.parent
RUTA_MODELO = RAIZ / "modelos" / "riesgo.joblib"
RUTA_METRICAS = RAIZ / "modelos" / "metricas.json"

UMBRAL_RIESGO = 3.0  # en riesgo = promedio < 3.0
SEMILLA = 42

NUMERICAS = ["horas_estudio", "asistencia", "semestre", "edad"]
CATEGORICAS = ["carrera"]
# "promedio" NO está: es de donde sale el objetivo (sería fuga de datos).
VARIABLES = NUMERICAS + CATEGORICAS

NOMBRES = {
    "logistica": "Regresión Logística",
    "logistica_sin_balancear": "Regresión Logística (sin balancear)",
    "arbol": "Árbol de Decisión",
}


def construir_pipeline(tipo: str) -> Pipeline:
    """Pipeline completo: preprocesado + clasificador.

    El preprocesado va DENTRO del pipeline, así el modelo guardado recibe
    los datos tal cual ("carrera": "Medicina") y hace él mismo el one-hot.
    """
    # La regresión logística necesita variables en la misma escala para que
    # los coeficientes sean comparables; al árbol le da igual la escala.
    numericas = StandardScaler() if tipo.startswith("logistica") else "passthrough"
    preprocesado = ColumnTransformer(
        [
            ("num", numericas, NUMERICAS),
            # handle_unknown="ignore": una carrera nueva no rompe el modelo, pero
            # la API la rechaza antes con 422 (ver carreras_validas).
            ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAS),
        ],
        verbose_feature_names_out=False,
    )

    if tipo == "logistica":
        modelo = LogisticRegression(class_weight="balanced", max_iter=1000)
    elif tipo == "logistica_sin_balancear":
        modelo = LogisticRegression(max_iter=1000)
    elif tipo == "arbol":
        modelo = DecisionTreeClassifier(
            max_depth=4, min_samples_leaf=5, class_weight="balanced", random_state=SEMILLA
        )
    else:
        raise ValueError(f"Tipo de modelo desconocido: {tipo!r}")

    return Pipeline([("preprocesado", preprocesado), ("modelo", modelo)])


def preparar_datos(estudiantes: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Separa variables (X) y objetivo (y). El promedio solo se usa para y."""
    y = (estudiantes["promedio"].astype(float) < UMBRAL_RIESGO).rename("en_riesgo")
    X = estudiantes[VARIABLES]
    return X, y


def nombre_modelo(pipeline: Pipeline) -> str:
    modelo = pipeline.named_steps["modelo"]
    if isinstance(modelo, DecisionTreeClassifier):
        return NOMBRES["arbol"]
    if modelo.class_weight == "balanced":
        return NOMBRES["logistica"]
    return NOMBRES["logistica_sin_balancear"]


def carreras_validas(pipeline: Pipeline) -> list[str]:
    """Carreras que el modelo vio al entrenar (las categorías del one-hot)."""
    codificador = pipeline.named_steps["preprocesado"].named_transformers_["cat"]
    return [str(c) for c in codificador.categories_[0]]


def probabilidad_riesgo(pipeline: Pipeline, datos: dict) -> float:
    """Probabilidad (0-1) de que el estudiante esté en riesgo."""
    fila = pd.DataFrame([datos], columns=VARIABLES)
    clase_riesgo = list(pipeline.classes_).index(True)
    return float(pipeline.predict_proba(fila)[0, clase_riesgo])
