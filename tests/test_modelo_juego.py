"""Modelo congelado del juego: inferencia propia y explicación exacta.

La explicación se valida contra SHAP (`shap` es dependencia solo de desarrollo):
para un modelo lineal, LinearExplainer con un enmascarador independiente
calcula exactamente coef · (z − media de z), que es lo que hace app/modelo_juego.py.
"""

import json
import math

import numpy as np
import pytest
from sklearn.model_selection import train_test_split

from app import ml, modelo_juego
from app.modelo_juego import ModeloJuego
from tests.test_prediccion import CARRERAS, datos_sinteticos

shap = pytest.importorskip("shap")


@pytest.fixture(scope="module")
def entrenado():
    """Pipeline logístico de app.ml con datos sintéticos, y su modelo del juego."""
    X, y = ml.preparar_datos(datos_sinteticos(400))
    X_train, X_test, y_train, _ = train_test_split(X, y, test_size=0.25, stratify=y, random_state=0)
    pipeline = ml.construir_pipeline("logistica").fit(X_train, y_train)
    datos = modelo_juego.exportar(pipeline, X_train, version="test", descripcion={}, control=[])
    return pipeline, ModeloJuego(datos), X_train, X_test


def test_probabilidades_iguales_a_scikit_learn(entrenado):
    pipeline, modelo, _, X_test = entrenado

    for fila in X_test.to_dict("records"):
        assert modelo.probabilidad(fila) == pytest.approx(ml.probabilidad_riesgo(pipeline, fila), abs=1e-12)


def test_aportes_iguales_a_shap(entrenado):
    pipeline, modelo, X_train, X_test = entrenado
    preprocesado, regresion = pipeline.named_steps["preprocesado"], pipeline.named_steps["modelo"]
    Z_train, Z_test = preprocesado.transform(X_train), preprocesado.transform(X_test)
    explicador = shap.LinearExplainer(regresion, shap.maskers.Independent(Z_train, max_samples=len(Z_train)))
    valores = explicador.shap_values(Z_test)
    columnas = list(preprocesado.get_feature_names_out())
    de_carrera = [i for i, c in enumerate(columnas) if c.startswith("carrera_")]

    assert modelo.logit_base == pytest.approx(float(np.ravel(explicador.expected_value)[0]), abs=1e-10)
    for fila, shap_fila in zip(X_test.to_dict("records"), valores):
        aportes = {a.variable: a.aporte for a in modelo.explicar(fila).aportes}
        for variable in ml.NUMERICAS:
            assert aportes[variable] == pytest.approx(shap_fila[columnas.index(variable)], abs=1e-10)
        # La carrera es una sola entrada: la suma de sus columnas del one-hot.
        assert aportes["carrera"] == pytest.approx(shap_fila[de_carrera].sum(), abs=1e-10)


def test_la_explicacion_suma_exactamente_el_logit(entrenado):
    _, modelo, _, X_test = entrenado

    for fila in X_test.to_dict("records"):
        explicacion = modelo.explicar(fila)
        assert explicacion.logit_base + sum(a.aporte for a in explicacion.aportes) == pytest.approx(explicacion.logit)
        assert explicacion.probabilidad == pytest.approx(1 / (1 + math.exp(-explicacion.logit)))


def test_aportes_ordenados_y_una_entrada_por_variable(entrenado):
    _, modelo, _, X_test = entrenado

    aportes = modelo.explicar(X_test.iloc[0].to_dict()).aportes

    assert sorted(a.variable for a in aportes) == sorted(ml.VARIABLES)
    assert [abs(a.aporte) for a in aportes] == sorted((abs(a.aporte) for a in aportes), reverse=True)


def test_menos_asistencia_sube_el_riesgo(entrenado):
    _, modelo, _, _ = entrenado
    base = {"horas_estudio": 15, "asistencia": 85, "semestre": 3, "edad": 20, "carrera": CARRERAS[0]}

    assert modelo.probabilidad(base | {"asistencia": 50}) > modelo.probabilidad(base)


def test_carrera_desconocida(entrenado):
    _, modelo, _, _ = entrenado

    with pytest.raises(KeyError):
        modelo.explicar({"horas_estudio": 1, "asistencia": 1, "semestre": 1, "edad": 20, "carrera": "Astrología"})


def test_solo_se_exporta_una_logistica():
    X, y = ml.preparar_datos(datos_sinteticos(100))
    arbol = ml.construir_pipeline("arbol").fit(X, y)

    with pytest.raises(ValueError, match="regresión logística"):
        modelo_juego.exportar(arbol, X, version="x", descripcion={}, control=[])


# ---------- El archivo congelado del repo ----------

def test_el_modelo_congelado_reproduce_sus_muestras_de_control():
    """Al congelar se guardaron entradas con la probabilidad de scikit-learn."""
    modelo = ModeloJuego.cargar()

    assert len(modelo.control) >= 5
    for muestra in modelo.control:
        assert modelo.probabilidad(muestra["entrada"]) == pytest.approx(muestra["probabilidad"], abs=1e-12)


def test_el_modelo_congelado_es_el_de_la_version_documentada():
    datos = json.loads(modelo_juego.RUTA.read_text(encoding="utf-8"))

    assert datos["version"] == "2026-10-11"
    assert set(datos["numericas"]) == set(ml.NUMERICAS)
    assert len(datos["carreras"]) == 8
    assert sum(c["frecuencia"] for c in datos["carreras"].values()) == pytest.approx(1)
