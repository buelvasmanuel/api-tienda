"""Modelo del juego: regresión logística congelada en app/modelo_juego.json.

Por qué un JSON y no el .joblib del build:
- No cambia con los deploys ni con los datos: el Laboratorio explica siempre
  el mismo modelo, y las estrellas de un rescate no cambian de un día a otro.
- Es una regresión logística, así que basta con sus números (medias,
  desviaciones, coeficientes): la inferencia y la explicación son unas sumas.
  Sin pickle (que puede ejecutar código al cargar) ni dependencia de la
  versión de scikit-learn.

Explicación exacta: en el espacio escalado del modelo,
    logit = intercepto + Σ coef_j · z_j
y restando el "estudiante medio" (la media de z_j en entrenamiento):
    logit = base + Σ coef_j · (z_j − media_j)
Cada término es el aporte de una variable. Es justo lo que calcula SHAP para
un modelo lineal (tests/test_modelo_juego.py lo comprueba con shap).

Se genera con scripts/congelar_modelo_juego.py. Contrato: docs/CONTRATO_LABORATORIO.md.
"""

import json
import math
from dataclasses import dataclass
from pathlib import Path

from app import ml

RUTA = Path(__file__).with_name("modelo_juego.json")

AVISO = (
    "Estimación de un modelo estadístico entrenado con datos simulados. Muestra "
    "asociaciones (correlación), no causas: cambiar una variable en el juego no "
    "garantiza el mismo efecto en un estudiante real."
)
UMBRAL = 0.5  # en riesgo si la probabilidad es >= 0.5 (como /prediccion/riesgo)


def _sigmoide(logit: float) -> float:
    return 1 / (1 + math.exp(-logit))


@dataclass(frozen=True)
class Aporte:
    variable: str
    valor: int | str
    aporte: float  # en log-odds


@dataclass(frozen=True)
class Explicacion:
    logit_base: float
    logit: float
    aportes: list[Aporte]  # ordenados por |aporte| de mayor a menor

    @property
    def probabilidad(self) -> float:
        return _sigmoide(self.logit)

    @property
    def probabilidad_base(self) -> float:
        return _sigmoide(self.logit_base)


class ModeloJuego:
    def __init__(self, datos: dict):
        self.version: str = datos["version"]
        self.nombre: str = datos["nombre"]
        self.intercepto: float = datos["intercepto"]
        # {"horas_estudio": {"media", "desviacion", "coef"}, ...}
        self.numericas: dict[str, dict[str, float]] = datos["numericas"]
        # {"Medicina": {"coef", "frecuencia"}, ...}
        self.carreras: dict[str, dict[str, float]] = datos["carreras"]
        self.control: list[dict] = datos.get("control", [])
        # Logit del "estudiante medio": todas las z en su media. Las numéricas
        # escaladas tienen media 0; cada columna del one-hot, su frecuencia.
        self.logit_base = self.intercepto + sum(c["coef"] * c["frecuencia"] for c in self.carreras.values())

    @classmethod
    def cargar(cls, ruta: Path = RUTA) -> "ModeloJuego":
        return cls(json.loads(ruta.read_text(encoding="utf-8")))

    @property
    def carreras_validas(self) -> list[str]:
        return sorted(self.carreras)

    def explicar(self, entrada: dict) -> Explicacion:
        """`entrada` con las variables de app.ml.VARIABLES. Lanza KeyError si la
        carrera no es válida (la API lo comprueba antes y responde 422)."""
        aportes = []
        for variable in ml.NUMERICAS:
            p = self.numericas[variable]
            z = (entrada[variable] - p["media"]) / p["desviacion"]
            aportes.append(Aporte(variable, entrada[variable], p["coef"] * z))  # media de z = 0

        carrera = entrada["carrera"]
        if carrera not in self.carreras:
            raise KeyError(carrera)
        # One-hot: 1 en su carrera y 0 en las demás, menos la frecuencia de cada una.
        aporte_carrera = sum(
            c["coef"] * ((1.0 if nombre == carrera else 0.0) - c["frecuencia"])
            for nombre, c in self.carreras.items()
        )
        aportes.append(Aporte("carrera", carrera, aporte_carrera))

        aportes.sort(key=lambda a: abs(a.aporte), reverse=True)
        logit = self.logit_base + sum(a.aporte for a in aportes)
        return Explicacion(self.logit_base, logit, aportes)

    def probabilidad(self, entrada: dict) -> float:
        return self.explicar(entrada).probabilidad


def exportar(pipeline, X_entrenamiento, *, version: str, descripcion: dict, control: list[dict]) -> dict:
    """Convierte un Pipeline logístico de app.ml (ya entrenado) en el JSON del
    modelo del juego. Lo usan scripts/congelar_modelo_juego.py y los tests."""
    preprocesado = pipeline.named_steps["preprocesado"]
    modelo = pipeline.named_steps["modelo"]
    if not hasattr(modelo, "coef_"):
        raise ValueError("El modelo del juego tiene que ser una regresión logística")

    nombres = list(preprocesado.get_feature_names_out())
    coeficientes = dict(zip(nombres, (float(c) for c in modelo.coef_[0])))
    escalador = preprocesado.named_transformers_["num"]
    carreras = ml.carreras_validas(pipeline)
    frecuencias = X_entrenamiento["carrera"].value_counts(normalize=True)

    return {
        "version": version,
        "nombre": "Regresión Logística (juego)",
        "descripcion": descripcion,
        "intercepto": float(modelo.intercept_[0]),
        "numericas": {
            variable: {
                "media": float(escalador.mean_[i]),
                "desviacion": float(escalador.scale_[i]),
                "coef": coeficientes[variable],
            }
            for i, variable in enumerate(ml.NUMERICAS)
        },
        "carreras": {
            carrera: {"coef": coeficientes[f"carrera_{carrera}"], "frecuencia": float(frecuencias.get(carrera, 0.0))}
            for carrera in carreras
        },
        "control": control,
    }
