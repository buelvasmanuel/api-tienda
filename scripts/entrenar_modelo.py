"""Entrena el modelo predictivo de riesgo académico (promedio < 3.0).

Lee los estudiantes de la base del .env, compara modelos con validación
cruzada, evalúa en un conjunto de prueba y guarda el mejor (por F2) en
modelos/riesgo.joblib, con sus métricas en modelos/metricas.json.

Uso (desde la raíz del proyecto):
    python -m scripts.entrenar_modelo
    python -m scripts.entrenar_modelo --sin-graficas   # sin matplotlib (servidor)
"""

import argparse
import json
import sys
from datetime import datetime

import joblib
import pandas as pd
import sklearn
from sklearn.metrics import (
    ConfusionMatrixDisplay,
    accuracy_score,
    confusion_matrix,
    f1_score,
    fbeta_score,
    make_scorer,
    precision_score,
    recall_score,
)
from sklearn.model_selection import StratifiedKFold, cross_validate, train_test_split
from sqlalchemy import select

from app.database import engine
from app.ml import (
    NOMBRES,
    RAIZ,
    RUTA_METRICAS,
    RUTA_MODELO,
    SEMILLA,
    UMBRAL_RIESGO,
    VARIABLES,
    carreras_validas,
    construir_pipeline,
    preparar_datos,
)
from app.models import Estudiante

COMPITEN = ["logistica", "arbol"]  # candidatos a "mejor modelo"
SOLO_COMPARACION = ["logistica_sin_balancear"]  # se muestra, pero no compite
DIR_GRAFICAS = RAIZ / "graficas"

METRICAS_CV = {
    "accuracy": "accuracy",
    "precision": "precision",
    "recall": "recall",
    "f1": "f1",
    "f2": make_scorer(fbeta_score, beta=2),  # como F1, pero el recall pesa el doble
}


def leer_estudiantes() -> pd.DataFrame:
    with engine.connect() as conexion:
        return pd.read_sql(select(Estudiante), conexion)


def metricas_prueba(y_real, y_pred) -> dict:
    return {
        "accuracy": round(accuracy_score(y_real, y_pred), 3),
        "precision": round(precision_score(y_real, y_pred, zero_division=0), 3),
        "recall": round(recall_score(y_real, y_pred), 3),
        "f1": round(f1_score(y_real, y_pred), 3),
        "f2": round(fbeta_score(y_real, y_pred, beta=2), 3),
        # [[VN, FP], [FN, VP]]: filas = realidad, columnas = predicción
        "matriz_confusion": confusion_matrix(y_real, y_pred, labels=[False, True]).tolist(),
    }


def importancias(pipeline) -> pd.Series:
    """Coeficientes (logística) o feature_importances_ (árbol), por variable."""
    nombres = pipeline.named_steps["preprocesado"].get_feature_names_out()
    modelo = pipeline.named_steps["modelo"]
    valores = modelo.coef_[0] if hasattr(modelo, "coef_") else modelo.feature_importances_
    serie = pd.Series(valores, index=[n.replace("carrera_", "carrera: ") for n in nombres])
    return serie.reindex(serie.abs().sort_values(ascending=False).index).round(3)


def titulo(texto: str) -> None:
    print(f"\n{'=' * 70}\n{texto}\n{'=' * 70}")


def _pyplot():
    """matplotlib se importa solo al graficar: es dependencia de desarrollo y
    en el servidor (Render) se entrena con --sin-graficas sin tenerla."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def graficar_matrices(resultados: dict, ruta) -> None:
    plt = _pyplot()
    fig, ejes = plt.subplots(1, len(resultados), figsize=(5 * len(resultados), 4.5), layout="constrained")
    for eje, (tipo, r) in zip(ejes, resultados.items()):
        ConfusionMatrixDisplay(
            confusion_matrix=pd.DataFrame(r["matriz_confusion"]).to_numpy(),
            display_labels=["No riesgo", "En riesgo"],
        ).plot(ax=eje, cmap="Blues", colorbar=False)
        eje.set_title(f"{NOMBRES[tipo]}\nrecall={r['recall']:.2f}  accuracy={r['accuracy']:.2f}", fontsize=10)
        eje.set_xlabel("Predicción")
        eje.set_ylabel("Realidad")
    fig.suptitle("Matriz de confusión (conjunto de prueba)", fontsize=13, fontweight="bold")
    fig.savefig(ruta, dpi=120)
    plt.close(fig)


def graficar_importancias(series: dict, ruta) -> None:
    plt = _pyplot()
    fig, ejes = plt.subplots(1, len(series), figsize=(7 * len(series), 5.5), layout="constrained")
    for eje, (tipo, serie) in zip(ejes, series.items()):
        serie = serie.iloc[::-1]  # la más importante arriba
        colores = ["#c0392b" if v > 0 else "#2e86c1" for v in serie] if tipo.startswith("logistica") else "#3b6ea5"
        eje.barh(serie.index, serie.values, color=colores)
        eje.axvline(0, color="black", linewidth=0.8)
        eje.set_title(NOMBRES[tipo], fontweight="bold")
        eje.set_xlabel(
            "Coeficiente (rojo: sube el riesgo, azul: lo baja)"
            if tipo.startswith("logistica") else "Importancia (suma 1)"
        )
        eje.spines[["top", "right"]].set_visible(False)
    fig.suptitle("Importancia de las variables", fontsize=13, fontweight="bold")
    fig.savefig(ruta, dpi=120)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description="Entrena el modelo de riesgo académico.")
    parser.add_argument("--sin-graficas", action="store_true",
                        help="no genera las gráficas (no necesita matplotlib)")
    args = parser.parse_args()

    estudiantes = leer_estudiantes()
    if len(estudiantes) < 50:
        print(f"Solo hay {len(estudiantes)} estudiantes en '{engine.url.database}'. "
              "Genera datos con: python -m scripts.seed_estudiantes", file=sys.stderr)
        return 1

    X, y = preparar_datos(estudiantes)
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, stratify=y, random_state=SEMILLA
    )

    titulo("DATOS")
    print(f"Base: {engine.url.database}  |  estudiantes: {len(X)}  |  variables: {', '.join(VARIABLES)}")
    print(f"En riesgo (promedio < {UMBRAL_RIESGO}): {y.sum()} ({y.mean():.1%})")
    print(f"Entrenamiento: {len(X_train)} ({y_train.sum()} en riesgo)  |  "
          f"Prueba: {len(X_test)} ({y_test.sum()} en riesgo)")

    # 1) Validación cruzada sobre el conjunto de entrenamiento: para ELEGIR.
    titulo("VALIDACIÓN CRUZADA (5 particiones estratificadas, sobre entrenamiento)")
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEMILLA)
    resumen_cv = {}
    for tipo in COMPITEN + SOLO_COMPARACION:
        puntuaciones = cross_validate(construir_pipeline(tipo), X_train, y_train, cv=cv, scoring=METRICAS_CV)
        resumen_cv[tipo] = {
            m: {"media": round(puntuaciones[f"test_{m}"].mean(), 3),
                "desviacion": round(puntuaciones[f"test_{m}"].std(), 3)}
            for m in METRICAS_CV
        }
    tabla_cv = pd.DataFrame({
        NOMBRES[t]: {m: f"{v['media']:.3f} ± {v['desviacion']:.3f}" for m, v in r.items()}
        for t, r in resumen_cv.items()
    }).T
    print(tabla_cv.to_string())

    mejor = max(COMPITEN, key=lambda t: resumen_cv[t]["f2"]["media"])
    print(f"\n→ Mejor por F2 medio: {NOMBRES[mejor]} "
          f"(la versión sin balancear solo se muestra como comparación)")

    # 2) Entrenar con todo el conjunto de entrenamiento y evaluar en prueba.
    titulo("CONJUNTO DE PRUEBA (datos que ningún modelo vio)")
    pipelines, resultados = {}, {}
    for tipo in COMPITEN + SOLO_COMPARACION:
        pipelines[tipo] = construir_pipeline(tipo).fit(X_train, y_train)
        resultados[tipo] = metricas_prueba(y_test, pipelines[tipo].predict(X_test))
    tabla = pd.DataFrame({NOMBRES[t]: {k: v for k, v in r.items() if k != "matriz_confusion"}
                          for t, r in resultados.items()}).T
    print(tabla.to_string())
    for tipo, r in resultados.items():
        (vn, fp), (fn, vp) = r["matriz_confusion"]
        print(f"\n{NOMBRES[tipo]}:\n"
              f"                     predice NO   predice SÍ\n"
              f"  realmente NO        {vn:>5}  (VN)   {fp:>5}  (FP)\n"
              f"  realmente SÍ        {fn:>5}  (FN)   {vp:>5}  (VP)\n"
              f"  → detecta {vp} de {vp + fn} estudiantes en riesgo; {fn} se le escapan.")

    # 3) Importancia de las variables.
    titulo("IMPORTANCIA DE LAS VARIABLES")
    series = {t: importancias(pipelines[t]) for t in COMPITEN}
    for tipo, serie in series.items():
        print(f"\n{NOMBRES[tipo]}:")
        print(serie.to_string())

    # 4) Guardar el mejor modelo (el Pipeline completo) y sus métricas.
    RUTA_MODELO.parent.mkdir(exist_ok=True)
    joblib.dump(pipelines[mejor], RUTA_MODELO)
    metricas = {
        "modelo": NOMBRES[mejor],
        "criterio_seleccion": "F2 medio en validación cruzada (5 particiones)",
        "entrenado": datetime.now().isoformat(timespec="seconds"),
        "version_sklearn": sklearn.__version__,
        "umbral_riesgo": UMBRAL_RIESGO,
        "variables": VARIABLES,
        "carreras_validas": carreras_validas(pipelines[mejor]),
        "datos": {
            "total": len(X), "entrenamiento": len(X_train), "prueba": len(X_test),
            "porcentaje_en_riesgo": round(100 * float(y.mean()), 1),
        },
        "metricas_prueba": resultados[mejor],  # las del modelo elegido, para mostrar en Flutter
        "comparacion": {
            NOMBRES[t]: {"validacion_cruzada": resumen_cv[t], "prueba": resultados[t]}
            for t in resultados
        },
        "importancias": series[mejor].to_dict(),
    }
    RUTA_METRICAS.write_text(json.dumps(metricas, ensure_ascii=False, indent=2))

    guardados = [RUTA_MODELO, RUTA_METRICAS]
    if not args.sin_graficas:
        DIR_GRAFICAS.mkdir(exist_ok=True)
        graficar_matrices(resultados, DIR_GRAFICAS / "matriz_confusion.png")
        graficar_importancias(series, DIR_GRAFICAS / "importancia_variables.png")
        guardados += [DIR_GRAFICAS / "matriz_confusion.png", DIR_GRAFICAS / "importancia_variables.png"]

    titulo("GUARDADO")
    for ruta in guardados:
        print(f"  {ruta.relative_to(RAIZ)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
