import json
import threading
from pathlib import Path

import joblib
from fastapi import APIRouter, Depends, status
from sklearn.pipeline import Pipeline

from app import ml, schemas
from app.errores import error
from app.modelo_juego import AVISO, UMBRAL, Explicacion, ModeloJuego

router = APIRouter(prefix="/prediccion", tags=["Predicción"])

SIN_MODELO = "Modelo no entrenado. Ejecuta: python -m scripts.entrenar_modelo"


def _sin_modelo():
    return error(status.HTTP_503_SERVICE_UNAVAILABLE, "modelo_no_disponible", SIN_MODELO)


def carrera_desconocida(carrera: str, validas: list[str], indices: list[int] | None = None):
    """422 con las carreras que acepta el modelo (y, en un lote, en qué posiciones)."""
    extra = {"carreras_validas": validas} | ({"indices": indices} if indices is not None else {})
    return error(422, "carrera_desconocida", f"Carrera desconocida para el modelo: {carrera!r}", **extra)


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
            raise _sin_modelo()
        with self._lock:
            if fecha != self._fecha:
                self._pipeline = joblib.load(self.ruta_modelo)
                self._fecha = fecha
            return self._pipeline

    def metricas(self) -> dict:
        try:
            return json.loads(self.ruta_metricas.read_text())
        except FileNotFoundError:
            raise _sin_modelo()


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
        raise carrera_desconocida(datos.carrera, validas)

    probabilidad = ml.probabilidad_riesgo(pipeline, datos.model_dump())
    return schemas.PrediccionSalida(
        # Se decide con la probabilidad SIN redondear: 0.498 no es riesgo
        # aunque se muestre como 0.50.
        en_riesgo=probabilidad >= 0.5,
        probabilidad=round(probabilidad, 2),
        modelo=ml.nombre_modelo(pipeline),
    )


# exclude_unset: el JSON sale igual que metricas.json (sin añadir nulls por
# los campos que no traiga).
@router.get("/metricas", response_model=schemas.MetricasOut, response_model_exclude_unset=True)
def metricas(cargador: CargadorModelo = Depends(obtener_cargador)):
    """Métricas del modelo (modelos/metricas.json) y las carreras válidas.

    carreras_validas sale del propio modelo cargado, así siempre coincide con
    lo que acepta POST /prediccion/riesgo (útil para el desplegable en Flutter).
    """
    return cargador.metricas() | {"carreras_validas": ml.carreras_validas(cargador.pipeline())}


# ---------- Modelo del juego (docs/CONTRATO_LABORATORIO.md §3) ----------

_modelo_juego = ModeloJuego.cargar()


def obtener_modelo_juego() -> ModeloJuego:
    """Dependencia: el modelo congelado del juego (los tests pueden sustituirlo)."""
    return _modelo_juego


def modelo_out(modelo: ModeloJuego) -> schemas.ModeloOut:
    return schemas.ModeloOut(nombre=modelo.nombre, version=modelo.version)


def explicacion_out(modelo: ModeloJuego, explicacion: Explicacion) -> schemas.ExplicacionOut:
    total = sum(abs(a.aporte) for a in explicacion.aportes)
    return schemas.ExplicacionOut(
        probabilidad=round(explicacion.probabilidad, 4),
        en_riesgo=explicacion.probabilidad >= UMBRAL,  # sin redondear, como /riesgo
        base=schemas.BaseOut(probabilidad=round(explicacion.probabilidad_base, 4),
                             logit=round(explicacion.logit_base, 4)),
        logit=round(explicacion.logit, 4),
        aportes=[
            schemas.AporteOut(
                variable=a.variable, valor=a.valor, aporte=round(a.aporte, 4),
                peso=round(abs(a.aporte) / total, 4) if total else 0.0,
                efecto="sube" if a.aporte > 0 else "baja" if a.aporte < 0 else "neutro",
            )
            for a in explicacion.aportes
        ],
        modelo=modelo_out(modelo),
        aviso=AVISO,
    )


@router.post("/explicacion", response_model=schemas.ExplicacionOut)
def explicar(datos: schemas.PrediccionEntrada, modelo: ModeloJuego = Depends(obtener_modelo_juego)):
    """Probabilidad y aporte exacto de cada variable (modelo del juego)."""
    if datos.carrera not in modelo.carreras:
        raise carrera_desconocida(datos.carrera, modelo.carreras_validas)
    return explicacion_out(modelo, modelo.explicar(datos.model_dump()))


@router.post("/riesgo/lote", response_model=schemas.LoteRiesgoOut)
def riesgo_lote(datos: schemas.LoteRiesgoEntrada, modelo: ModeloJuego = Depends(obtener_modelo_juego)):
    """Hasta 50 predicciones de una vez (modelo del juego), en el mismo orden."""
    desconocidas = [i for i, e in enumerate(datos.entradas) if e.carrera not in modelo.carreras]
    if desconocidas:
        raise carrera_desconocida(datos.entradas[desconocidas[0]].carrera, modelo.carreras_validas, desconocidas)
    resultados = []
    for entrada in datos.entradas:
        probabilidad = modelo.probabilidad(entrada.model_dump())
        resultados.append(schemas.RiesgoOut(probabilidad=round(probabilidad, 4), en_riesgo=probabilidad >= UMBRAL))
    return schemas.LoteRiesgoOut(resultados=resultados, modelo=modelo_out(modelo), aviso=AVISO)
