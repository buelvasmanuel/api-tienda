import json
import threading
from pathlib import Path

import joblib
from fastapi import APIRouter, Depends, HTTPException
from sklearn.pipeline import Pipeline

from app import ml, schemas

router = APIRouter(prefix="/prediccion", tags=["Predicción"])

SIN_MODELO = "Modelo no entrenado. Ejecuta: python -m scripts.entrenar_modelo"


class CargadorModelo:
    """Carga el modelo una sola vez y lo mantiene en memoria.

    En cada petición solo mira la fecha de modificación del archivo (stat(),
    instantáneo): si se reentrena el modelo, se recarga solo, sin reiniciar
    la API. Si no ha cambiado, no se lee nada del disco.

    Seguridad: joblib.load usa pickle, que puede ejecutar código al cargar.
    Carga solo modelos generados por ti (scripts/entrenar_modelo.py).
    """

    def __init__(self, ruta_modelo: Path, ruta_metricas: Path):
        self.ruta_modelo = ruta_modelo
        self.ruta_metricas = ruta_metricas
        self._fecha: float | None = None
        self._pipeline: Pipeline | None = None
        self._lock = threading.Lock()  # FastAPI atiende peticiones en varios hilos

    def pipeline(self) -> Pipeline:
        try:
            fecha = self.ruta_modelo.stat().st_mtime
        except FileNotFoundError:
            raise HTTPException(status_code=503, detail=SIN_MODELO)
        with self._lock:
            if fecha != self._fecha:
                self._pipeline = joblib.load(self.ruta_modelo)
                self._fecha = fecha
            return self._pipeline

    def metricas(self) -> dict:
        try:
            return json.loads(self.ruta_metricas.read_text())
        except FileNotFoundError:
            raise HTTPException(status_code=503, detail=SIN_MODELO)


_cargador = CargadorModelo(ml.RUTA_MODELO, ml.RUTA_METRICAS)


def obtener_cargador() -> CargadorModelo:
    """Dependencia: los tests la sustituyen por un cargador con un modelo de prueba."""
    return _cargador


@router.post("/riesgo", response_model=schemas.PrediccionSalida)
def predecir_riesgo(datos: schemas.PrediccionEntrada,
                    cargador: CargadorModelo = Depends(obtener_cargador)):
    pipeline = cargador.pipeline()

    validas = ml.carreras_validas(pipeline)
    if datos.carrera not in validas:
        raise HTTPException(
            status_code=422,
            detail={"mensaje": f"Carrera desconocida para el modelo: {datos.carrera!r}",
                    "carreras_validas": validas},
        )

    probabilidad = ml.probabilidad_riesgo(pipeline, datos.model_dump())
    return schemas.PrediccionSalida(
        # Se decide con la probabilidad SIN redondear: 0.498 no es riesgo
        # aunque se muestre como 0.50.
        en_riesgo=probabilidad >= 0.5,
        probabilidad=round(probabilidad, 2),
        modelo=ml.nombre_modelo(pipeline),
    )


@router.get("/metricas")
def metricas(cargador: CargadorModelo = Depends(obtener_cargador)) -> dict:
    """Métricas del modelo (modelos/metricas.json) y las carreras válidas.

    carreras_validas sale del propio modelo cargado, así siempre coincide con
    lo que acepta POST /prediccion/riesgo (útil para el desplegable en Flutter).
    """
    return cargador.metricas() | {"carreras_validas": ml.carreras_validas(cargador.pipeline())}
