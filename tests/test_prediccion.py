"""Tests de /prediccion sin depender de modelos/ ni de tienda_db: el modelo se
entrena aquí mismo con datos sintéticos y se guarda en una carpeta temporal."""

import json
import random

import joblib
import pandas as pd
import pytest

from app import ml
from app.main import app
from app.routers.prediccion import CargadorModelo, obtener_cargador

CARRERAS = ["Derecho", "Medicina", "Psicología"]
ENTRADA = {"horas_estudio": 15, "asistencia": 85, "semestre": 3, "edad": 20, "carrera": "Derecho"}


def datos_sinteticos(n: int = 300) -> pd.DataFrame:
    """Regla clara: pocas horas y poca asistencia → promedio bajo (en riesgo)."""
    rng = random.Random(0)
    filas = []
    for _ in range(n):
        horas, asistencia = rng.randint(0, 40), rng.randint(40, 100)
        puntuacion = horas / 40 + (asistencia - 40) / 60 + rng.gauss(0, 0.15)
        filas.append({
            "horas_estudio": horas, "asistencia": asistencia,
            "semestre": rng.randint(1, 10), "edad": rng.randint(16, 35),
            "carrera": rng.choice(CARRERAS),
            "promedio": 2.5 if puntuacion < 0.7 else 3.8,
        })
    return pd.DataFrame(filas)


@pytest.fixture(scope="module")
def dir_modelo(tmp_path_factory):
    """Entrena un modelo pequeño (una vez por módulo) y lo guarda en una carpeta temporal."""
    directorio = tmp_path_factory.mktemp("modelos")
    X, y = ml.preparar_datos(datos_sinteticos())
    joblib.dump(ml.construir_pipeline("logistica").fit(X, y), directorio / "riesgo.joblib")
    (directorio / "metricas.json").write_text(json.dumps({"modelo": "Regresión Logística", "metricas_prueba": {"recall": 0.9}}))
    return directorio


@pytest.fixture
def con_modelo(client, dir_modelo):
    cargador = CargadorModelo(dir_modelo / "riesgo.joblib", dir_modelo / "metricas.json")
    app.dependency_overrides[obtener_cargador] = lambda: cargador
    return client


@pytest.fixture
def sin_modelo(client, tmp_path):
    cargador = CargadorModelo(tmp_path / "no-existe.joblib", tmp_path / "no-existe.json")
    app.dependency_overrides[obtener_cargador] = lambda: cargador
    return client


def predecir(client, **cambios):
    return client.post("/prediccion/riesgo", json=ENTRADA | cambios)


# ---------- 503: sin modelo entrenado ----------

@pytest.mark.parametrize(("metodo", "ruta"), [("POST", "/prediccion/riesgo"), ("GET", "/prediccion/metricas")])
def test_sin_modelo_devuelve_503(sin_modelo, metodo, ruta):
    respuesta = sin_modelo.request(metodo, ruta, json=ENTRADA if metodo == "POST" else None)

    assert respuesta.status_code == 503
    assert "python -m scripts.entrenar_modelo" in respuesta.json()["detail"]


# ---------- 200: predicción ----------

def test_prediccion_devuelve_formato_correcto(con_modelo):
    respuesta = predecir(con_modelo)

    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert set(datos) == {"en_riesgo", "probabilidad", "modelo"}
    assert isinstance(datos["en_riesgo"], bool)
    assert 0 <= datos["probabilidad"] <= 1
    assert round(datos["probabilidad"], 2) == datos["probabilidad"]  # 2 decimales
    assert datos["modelo"] == "Regresión Logística"


def test_estudiante_con_malos_habitos_esta_en_riesgo(con_modelo):
    datos = predecir(con_modelo, horas_estudio=1, asistencia=45).json()

    assert datos["en_riesgo"] is True
    assert datos["probabilidad"] > 0.8


def test_estudiante_con_buenos_habitos_no_esta_en_riesgo(con_modelo):
    datos = predecir(con_modelo, horas_estudio=35, asistencia=98).json()

    assert datos["en_riesgo"] is False
    assert datos["probabilidad"] < 0.2


def test_mas_horas_de_estudio_bajan_la_probabilidad(con_modelo):
    probabilidades = [predecir(con_modelo, horas_estudio=h).json()["probabilidad"] for h in (0, 10, 20, 30, 40)]

    assert probabilidades == sorted(probabilidades, reverse=True)


def test_en_riesgo_coincide_con_la_probabilidad(con_modelo):
    vistos = set()
    for horas in range(0, 41, 2):
        datos = predecir(con_modelo, horas_estudio=horas, asistencia=70).json()
        vistos.add(datos["en_riesgo"])
        # Exactamente 0.50 es ambiguo (0.498 y 0.502 se redondean igual): no se afirma.
        if datos["probabilidad"] > 0.5:
            assert datos["en_riesgo"] is True
        elif datos["probabilidad"] < 0.5:
            assert datos["en_riesgo"] is False
    assert vistos == {True, False}  # el barrido cruza el umbral


def test_carrera_con_espacios_se_acepta(con_modelo):
    assert predecir(con_modelo, carrera="  Medicina ").status_code == 200


# ---------- 422 ----------

def test_carrera_desconocida_devuelve_422_con_las_validas(con_modelo):
    respuesta = predecir(con_modelo, carrera="Veterinaria")

    assert respuesta.status_code == 422
    detalle = respuesta.json()["detail"]
    assert "Veterinaria" in detalle["mensaje"]
    assert detalle["carreras_validas"] == sorted(CARRERAS)


@pytest.mark.parametrize(
    "cambios",
    [{"horas_estudio": -1}, {"horas_estudio": 41}, {"asistencia": 101}, {"asistencia": -1},
     {"semestre": 0}, {"semestre": 11}, {"edad": 15}, {"edad": 36}, {"carrera": ""}],
    ids=lambda c: "-".join(f"{k}={v}" for k, v in c.items()),
)
def test_entrada_fuera_de_rango_devuelve_422(con_modelo, cambios):
    assert predecir(con_modelo, **cambios).status_code == 422


def test_entrada_sin_campos_devuelve_422(con_modelo):
    respuesta = con_modelo.post("/prediccion/riesgo", json={})

    assert respuesta.status_code == 422
    assert {e["loc"][-1] for e in respuesta.json()["detail"]} == set(ml.VARIABLES)


# ---------- métricas ----------

def test_metricas_incluye_carreras_validas(con_modelo):
    respuesta = con_modelo.get("/prediccion/metricas")

    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["modelo"] == "Regresión Logística"
    assert datos["metricas_prueba"] == {"recall": 0.9}
    assert datos["carreras_validas"] == sorted(CARRERAS)


# ---------- caché y fuga de datos ----------

def test_el_modelo_se_carga_una_sola_vez(dir_modelo, monkeypatch):
    cargador = CargadorModelo(dir_modelo / "riesgo.joblib", dir_modelo / "metricas.json")
    cargas = []
    carga_real = joblib.load
    monkeypatch.setattr("app.routers.prediccion.joblib.load", lambda ruta: cargas.append(ruta) or carga_real(ruta))

    for _ in range(5):
        cargador.pipeline()

    assert len(cargas) == 1


def test_promedio_no_es_variable_del_modelo():
    # Fuga de datos: el objetivo sale del promedio; usarlo como entrada sería trampa.
    assert "promedio" not in ml.VARIABLES
    X, _ = ml.preparar_datos(datos_sinteticos(20))
    assert "promedio" not in X.columns
