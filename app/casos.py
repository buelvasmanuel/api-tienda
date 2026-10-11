"""Casos del Modo 2 (docs/CONTRATO_JUEGO.md §3).

Cada caso calcula, con los estudiantes actuales y el modelo del juego, sus
opciones, cuáles son correctas, el dato de cada opción y la explicación. Nada
de esto se guarda: si cambian los datos, cambian las respuestas.

Las comparaciones usan fracciones exactas (no floats) para que un empate sea
un empate de verdad: si dos opciones empatan, todas son correctas.
"""

import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import ml
from app.modelo_juego import ModeloJuego
from app.models import Estudiante

XP_POR_NIVEL = {"novato": 20, "investigador": 40, "experto": 60}
RUTA_GRAFICAS = "/estudiantes/estadisticas/"


@dataclass(frozen=True)
class Opcion:
    valor: str
    texto: str


@dataclass(frozen=True)
class Evaluacion:
    """Todo lo que un caso dice con los datos de este momento."""

    enunciado: str
    opciones: list[Opcion]
    correctas: set[str]
    explicacion: str
    # Dato de cada opción (lo que se muestra al elegirla). Vacío en los casos
    # en los que cualquier dato revelaría la respuesta.
    datos: dict[str, str]
    dato_al_acertar: str


@dataclass(frozen=True)
class Caso:
    codigo: str
    nivel: str
    titulo: str
    grafica: str | None
    pista: str
    evaluar: Callable[[list[dict], ModeloJuego], Evaluacion]

    @property
    def xp(self) -> int:
        return XP_POR_NIVEL[self.nivel]


# ---------- Utilidades ----------

def numero(valor, decimales: int = 2) -> str:
    """Número con coma decimal, como se escribe en Colombia: 33,33."""
    return f"{float(valor):.{decimales}f}".replace(".", ",")


def lista(nombres: list[str]) -> str:
    return nombres[0] if len(nombres) == 1 else ", ".join(nombres[:-1]) + " y " + nombres[-1]


def maximos(valores: dict[str, Fraction | int]) -> set[str]:
    """Claves con el valor máximo (varias si empatan)."""
    mayor = max(valores.values())
    return {clave for clave, valor in valores.items() if valor == mayor}


def media(valores: list) -> Fraction:
    return sum((Fraction(v) for v in valores), Fraction(0)) / len(valores)


def por(estudiantes: list[dict], campo: str) -> dict[str, list[dict]]:
    grupos: dict[str, list[dict]] = defaultdict(list)
    for e in estudiantes:
        grupos[str(e[campo])].append(e)
    return grupos


UMBRAL = Decimal(str(ml.UMBRAL_RIESGO))  # el promedio llega como Decimal


def en_riesgo(e: dict) -> bool:
    return e["promedio"] < UMBRAL


def orden_natural(texto: str) -> list:
    """Clave de orden "humano": sin distinguir tildes ni mayúsculas, y con los
    números por su valor ("Semestre 2" antes que "Semestre 10")."""
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().casefold()
    return [int(trozo) if trozo.isdigit() else trozo for trozo in re.split(r"(\d+)", sin_tildes)]


def opciones_alfabeticas(valores, texto=str) -> list[Opcion]:
    return sorted((Opcion(str(v), texto(v)) for v in valores), key=lambda o: orden_natural(o.texto))


def cargar_estudiantes(db: Session) -> list[dict]:
    columnas = ("id", "carrera", "ciudad", "semestre", "edad", "promedio", "horas_estudio", "asistencia")
    return [{c: getattr(e, c) for c in columnas} for e in db.scalars(select(Estudiante))]


# ---------- Novato: leer una gráfica ----------

def _el_mas_numeroso(campo: str, texto: Callable[[str], str], opciones: Callable[[Counter], list[str]],
                     enunciado: str, unidad: str = "estudiantes"):
    def evaluar(estudiantes: list[dict], _: ModeloJuego) -> Evaluacion:
        conteo = Counter(str(e[campo]) for e in estudiantes)
        correctas = maximos(conteo)
        n = conteo[next(iter(correctas))]
        nombres = sorted(texto(c) for c in correctas)
        if len(correctas) == 1:
            explicacion = f"{nombres[0]} tiene {n} {unidad}, más que ninguna otra opción."
        else:
            explicacion = f"{lista(nombres)} empatan con {n} {unidad}: cualquiera es correcta."
        valores = opciones(conteo)
        return Evaluacion(
            enunciado=enunciado,
            opciones=opciones_alfabeticas(valores, texto),
            correctas=correctas,
            explicacion=explicacion,
            datos={v: f"{texto(v)}: {conteo[v]} {unidad}." for v in valores},
            dato_al_acertar=f"{lista(nombres)}: {n} {unidad}.",
        )
    return evaluar


def _top(n: int):
    """Las n claves con más estudiantes (desempate alfabético)."""
    return lambda conteo: [c for c, _ in sorted(conteo.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]


def _todas(conteo: Counter) -> list[str]:
    return list(conteo)


# ---------- Investigador: comparar ----------

def _carrera_mas_riesgo(estudiantes: list[dict], _: ModeloJuego) -> Evaluacion:
    porcentaje = {c: 100 * Fraction(sum(en_riesgo(e) for e in grupo), len(grupo))
                  for c, grupo in por(estudiantes, "carrera").items()}
    correctas = maximos(porcentaje)
    p = numero(porcentaje[next(iter(correctas))])
    return Evaluacion(
        enunciado="¿Qué carrera tiene el mayor porcentaje de estudiantes en riesgo (promedio menor que 3.0)?",
        opciones=opciones_alfabeticas(porcentaje),
        correctas=correctas,
        explicacion=(f"{lista(sorted(correctas))} tiene el porcentaje más alto ({p} %). Ojo: es un "
                     "porcentaje; en número de estudiantes en riesgo podría ganar una carrera más grande."),
        datos={c: f"{c}: {numero(v)} % de sus estudiantes está en riesgo." for c, v in porcentaje.items()},
        dato_al_acertar=f"{lista(sorted(correctas))}: {p} % en riesgo.",
    )


def _carrera_mejor_promedio(estudiantes: list[dict], _: ModeloJuego) -> Evaluacion:
    promedio = {c: media([e["promedio"] for e in grupo]) for c, grupo in por(estudiantes, "carrera").items()}
    correctas = maximos(promedio)
    p = numero(promedio[next(iter(correctas))])
    return Evaluacion(
        enunciado="¿Qué carrera tiene el promedio académico más alto?",
        opciones=opciones_alfabeticas(promedio),
        correctas=correctas,
        explicacion=f"{lista(sorted(correctas))} tiene el promedio medio más alto: {p} sobre 5.",
        datos={c: f"{c}: promedio {numero(v)}." for c, v in promedio.items()},
        dato_al_acertar=f"{lista(sorted(correctas))}: promedio {p}.",
    )


DIFERENCIA_IGUAL = 2  # puntos de asistencia por debajo de los cuales es "igual"


def _asistencia_en_riesgo(estudiantes: list[dict], _: ModeloJuego) -> Evaluacion:
    riesgo = [e["asistencia"] for e in estudiantes if en_riesgo(e)]
    resto = [e["asistencia"] for e in estudiantes if not en_riesgo(e)]
    a_riesgo, a_resto = (media(riesgo) if riesgo else Fraction(0)), (media(resto) if resto else Fraction(0))
    diferencia = a_riesgo - a_resto
    correcta = "igual" if abs(diferencia) < DIFERENCIA_IGUAL else "mas" if diferencia > 0 else "menos"
    cifras = f"Asistencia media: {numero(a_riesgo, 1)} % en riesgo frente a {numero(a_resto, 1)} % del resto."
    return Evaluacion(
        enunciado="Los estudiantes en riesgo, ¿van más o menos a clase que los demás?",
        opciones=[Opcion("mas", "Van más a clase"), Opcion("menos", "Van menos a clase"),
                  Opcion("igual", "Más o menos lo mismo")],
        correctas={correcta},
        explicacion=(f"{cifras} Es una asociación entre asistencia y riesgo: por sí sola no prueba "
                     "que faltar a clase cause el riesgo."),
        datos={},
        dato_al_acertar=cifras,
    )


# ---------- Experto: correlación frente a causalidad ----------

UMBRAL_CORRELACION = 0.1


def correlacion(x: list, y: list) -> float:
    try:
        return statistics.correlation([float(v) for v in x], [float(v) for v in y])
    except statistics.StatisticsError:  # menos de 2 datos o una variable constante
        return 0.0


def _horas_y_promedio(estudiantes: list[dict], _: ModeloJuego) -> Evaluacion:
    r = correlacion([e["horas_estudio"] for e in estudiantes], [e["promedio"] for e in estudiantes])
    correcta = "positiva" if r > UMBRAL_CORRELACION else "negativa" if r < -UMBRAL_CORRELACION else "ninguna"
    return Evaluacion(
        enunciado="¿Cómo se relacionan las horas de estudio con el promedio?",
        opciones=[Opcion("positiva", "Más horas, mejor promedio"), Opcion("negativa", "Más horas, peor promedio"),
                  Opcion("ninguna", "No hay una relación clara")],
        correctas={correcta},
        explicacion=(f"La correlación entre horas de estudio y promedio es r = {numero(r)} (de −1 a 1; "
                     f"por encima de {numero(UMBRAL_CORRELACION, 1)} en valor absoluto la contamos como relación). "
                     "Una correlación mide si dos cosas suben o bajan juntas, no cuál causa a cuál."),
        datos={},
        dato_al_acertar=f"r = {numero(r)}.",
    )


NOMBRES_VARIABLES = {"horas_estudio": "Horas de estudio", "asistencia": "Asistencia",
                     "semestre": "Semestre", "edad": "Edad"}


def _variable_mas_influyente(_: list[dict], modelo: ModeloJuego) -> Evaluacion:
    peso = {v: abs(Fraction(modelo.numericas[v]["coef"])) for v in ml.NUMERICAS}
    correctas = maximos(peso)
    nombre = lista(sorted(NOMBRES_VARIABLES[v] for v in correctas))
    return Evaluacion(
        enunciado="En el modelo del juego, ¿qué variable pesa más a la hora de estimar el riesgo?",
        opciones=opciones_alfabeticas(ml.NUMERICAS, NOMBRES_VARIABLES.get),
        correctas=correctas,
        explicacion=(f"{nombre} tiene el coeficiente más grande en valor absoluto ({numero(peso[next(iter(correctas))])}): "
                     "un cambio típico en ella mueve más el riesgo que uno en cualquier otra variable. "
                     "Es un peso del modelo, no una prueba de causa."),
        datos={v: f"{NOMBRES_VARIABLES[v]}: peso {numero(p)} en el modelo." for v, p in peso.items()},
        dato_al_acertar=f"{nombre}: peso {numero(peso[next(iter(correctas))])}.",
    )


def _edad_y_riesgo(estudiantes: list[dict], modelo: ModeloJuego) -> Evaluacion:
    coef = modelo.numericas["edad"]["coef"]
    r = correlacion([e["edad"] for e in estudiantes], [e["promedio"] for e in estudiantes])
    correcta = "espurio" if abs(r) < UMBRAL_CORRELACION else "asociacion"
    cifras = (f"Correlación real entre edad y promedio: r = {numero(r)}; "
              f"coeficiente de la edad en el modelo: {numero(coef)}.")
    if correcta == "espurio":
        explicacion = (f"{cifras} En los datos la edad casi no se relaciona con el promedio, pero el modelo "
                       "le dio peso: aprendió un patrón casual de esta muestra (un patrón espurio). "
                       "Con más datos, ese peso tendería a desaparecer.")
    else:
        explicacion = (f"{cifras} Hay una relación en los datos, pero con datos observados no se puede "
                       "afirmar que la edad la cause: otra variable podría explicar las dos.")
    return Evaluacion(
        enunciado=(f"El modelo da {'menos' if coef < 0 else 'más'} riesgo a los estudiantes mayores. "
                   "¿La edad influye en el promedio?"),
        opciones=[Opcion("causa", "Sí: cumplir años cambia el promedio"),
                  Opcion("asociacion", "Hay relación en los datos, aunque eso no prueba que la cause"),
                  Opcion("espurio", "No hay relación real: el modelo aprendió un patrón casual de la muestra")],
        correctas={correcta},
        explicacion=explicacion,
        datos={},
        dato_al_acertar=cifras,
    )


# ---------- Catálogo (en el orden del contrato) ----------

CASOS: dict[str, Caso] = {c.codigo: c for c in [
    Caso("carrera_mas_estudiantes", "novato", "El edificio más lleno", RUTA_GRAFICAS + "por-carrera",
         "Mira la gráfica de estudiantes por carrera: busca la barra más larga.",
         _el_mas_numeroso("carrera", str, _todas, "¿Qué carrera tiene más estudiantes?")),
    Caso("ciudad_mas_estudiantes", "novato", "¿De dónde vienen?", RUTA_GRAFICAS + "por-ciudad",
         "La gráfica de ciudades muestra de dónde viene cada estudiante.",
         _el_mas_numeroso("ciudad", str, _top(6), "¿De qué ciudad vienen más estudiantes?")),
    Caso("semestre_mas_estudiantes", "novato", "El piso más concurrido", RUTA_GRAFICAS + "por-semestre",
         "En el campus cada piso es un semestre: cuenta las ventanas, o mira la gráfica por semestre.",
         _el_mas_numeroso("semestre", lambda s: f"Semestre {s}", _todas, "¿En qué semestre hay más estudiantes?")),
    Caso("carrera_mas_riesgo", "investigador", "La facultad en apuros", RUTA_GRAFICAS + "riesgo-por-carrera",
         "Mira la gráfica de riesgo por carrera: cuenta el porcentaje, no el número de estudiantes.",
         _carrera_mas_riesgo),
    Caso("carrera_mejor_promedio", "investigador", "El cuadro de honor", RUTA_GRAFICAS + "promedio-por-carrera",
         "La gráfica de promedio por carrera ordena las carreras de mayor a menor.",
         _carrera_mejor_promedio),
    Caso("asistencia_en_riesgo", "investigador", "Pupitres vacíos", None,
         "Compara las ventanas rojas con las verdes: ¿quién falta más a clase?",
         _asistencia_en_riesgo),
    Caso("horas_y_promedio", "experto", "Horas de biblioteca", None,
         "Piensa en los estudiantes que estudian muchas horas: ¿suelen tener mejor o peor promedio?",
         _horas_y_promedio),
    Caso("variable_mas_influyente", "experto", "La pista principal", None,
         "En el Laboratorio, la explicación muestra qué variable empuja más el riesgo.",
         _variable_mas_influyente),
    Caso("edad_y_riesgo", "experto", "El falso sospechoso", None,
         "Que un modelo use una variable no significa que esa variable importe de verdad: mira los datos.",
         _edad_y_riesgo),
]}
