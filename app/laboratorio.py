"""Reglas del Laboratorio de Rescate (docs/CONTRATO_LABORATORIO.md §4.1).

Todo lo que decide el resultado de un rescate (efecto de cada intervención,
solución óptima y estrellas) se calcula aquí, en el servidor, con el modelo
del juego. El cliente solo envía qué intervenciones eligió.
"""

from collections import Counter
from dataclasses import dataclass
from itertools import product

from app.modelo_juego import UMBRAL, ModeloJuego

PRESUPUESTO = 5  # fichas
OBJETIVO = UMBRAL  # rescatado = probabilidad < 0.5
RIESGO_MINIMO = 0.6  # un caso empieza con al menos este riesgo


@dataclass(frozen=True)
class Intervencion:
    codigo: str
    nombre: str
    fichas: int
    variable: str
    incremento: int
    tope: int
    efecto: str


INTERVENCIONES = {
    i.codigo: i
    for i in (
        Intervencion("estudio", "Plan de estudio", 1, "horas_estudio", 2, 40, "+2 h de estudio por semana"),
        Intervencion("tutoria", "Tutoría", 2, "asistencia", 10, 100, "+10 puntos de asistencia"),
    )
}


@dataclass(frozen=True)
class Intento:
    """Resultado de aplicar unas intervenciones a un estudiante."""

    conteo: dict[str, int]  # {"estudio": n, "tutoria": m}
    fichas: int
    entrada: dict  # variables del modelo tras las intervenciones
    probabilidad: float

    @property
    def logrado(self) -> bool:
        return self.probabilidad < OBJETIVO


def fichas(conteo: dict[str, int]) -> int:
    return sum(INTERVENCIONES[codigo].fichas * n for codigo, n in conteo.items())


def aplicar(entrada: dict, conteo: dict[str, int]) -> dict:
    """Variables del estudiante tras las intervenciones, respetando los topes."""
    resultado = dict(entrada)
    for codigo, n in conteo.items():
        i = INTERVENCIONES[codigo]
        resultado[i.variable] = min(resultado[i.variable] + i.incremento * n, i.tope)
    return resultado


def intentar(modelo: ModeloJuego, entrada: dict, intervenciones: list[str]) -> Intento:
    conteo = {codigo: 0 for codigo in INTERVENCIONES} | dict(Counter(intervenciones))
    final = aplicar(entrada, conteo)
    return Intento(conteo, fichas(conteo), final, modelo.probabilidad(final))


def combinaciones() -> list[dict[str, int]]:
    """Todas las combinaciones que caben en el presupuesto (12 con 5 fichas)."""
    maximos = [range(PRESUPUESTO // i.fichas + 1) for i in INTERVENCIONES.values()]
    todas = (dict(zip(INTERVENCIONES, ns)) for ns in product(*maximos))
    return [c for c in todas if fichas(c) <= PRESUPUESTO]


def solucion_optima(modelo: ModeloJuego, entrada: dict) -> Intento | None:
    """La combinación que rescata con menos fichas (si empatan, la que deja
    menos riesgo). None si ninguna lo logra con el presupuesto."""
    exitosas = []
    for conteo in combinaciones():
        final = aplicar(entrada, conteo)
        intento = Intento(conteo, fichas(conteo), final, modelo.probabilidad(final))
        if intento.logrado:
            exitosas.append(intento)
    return min(exitosas, key=lambda i: (i.fichas, i.probabilidad), default=None)


def es_caso(modelo: ModeloJuego, entrada: dict) -> bool:
    """Riesgo inicial >= 60 % y rescatable con el presupuesto."""
    if entrada["carrera"] not in modelo.carreras:
        return False
    return modelo.probabilidad(entrada) >= RIESGO_MINIMO and solucion_optima(modelo, entrada) is not None


def estrellas(intento: Intento, optima: Intento) -> int:
    """3 = con las fichas óptimas, 2 = una de más, 1 = logrado, 0 = no logrado."""
    if not intento.logrado:
        return 0
    if intento.fichas <= optima.fichas:
        return 3
    return 2 if intento.fichas == optima.fichas + 1 else 1
