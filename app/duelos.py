"""Reglas del Duelo de cartas (docs/CONTRATO_DUELO.md).

El servidor reparte, guarda los mazos, decide el turno, valida cada jugada y
calcula el resultado y la XP: el cliente solo dice qué atributo elige.

Lados: "a" es quien creó el duelo; "b", el rival (la máquina en la v1). Las
respuestas se dan siempre desde el punto de vista de quien pregunta ("yo" y
"rival"), así el mismo código servirá para jugador contra jugador.
"""

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import reloj
from app.casos import cargar_estudiantes, correlacion, numero
from app.models import Duelo, Estudiante
from app.progreso import DESFASE_COLOMBIA, dia_colombia

RONDAS = 5
MINIMO_ESTUDIANTES = 2 * RONDAS
XP_POR_RESULTADO = {"victoria": 30, "empate": 15, "derrota": 5}
DUELOS_CON_XP_AL_DIA = 3
ALIAS_MAQUINA = "Búho Máquina"


@dataclass(frozen=True)
class Atributo:
    codigo: str
    nombre: str
    unidad: str
    descripcion: str


ATRIBUTOS = {a.codigo: a for a in (
    Atributo("promedio", "Promedio", "0–5",
             "Es el resultado académico, lo que el modelo intenta predecir."),
    Atributo("asistencia", "Asistencia", "%", "Porcentaje de clases a las que va."),
    Atributo("horas_estudio", "Horas de estudio", "h/semana", "Horas de estudio por semana."),
    Atributo("semestre", "Semestre", "1–10", "Cuánto lleva en la carrera."),
)}


# ---------- Cartas ----------

def percentil(valor, todos: list) -> int:
    """% de estudiantes con un valor menor, más la mitad de los que empatan."""
    menores = sum(1 for v in todos if v < valor)
    iguales = sum(1 for v in todos if v == valor)
    return round(100 * (menores + iguales / 2) / len(todos))


def _valor(estudiante: dict, atributo: str):
    valor = estudiante[atributo]
    return float(valor) if atributo == "promedio" else int(valor)


def carta(estudiante: dict, poblacion: list[dict]) -> dict:
    """Copia de la carta tal como se guarda y se envía (CartaOut)."""
    return {
        "estudiante_id": estudiante["id"],
        "nombre": estudiante["nombre"],
        "apellido": estudiante["apellido"],
        "carrera": estudiante["carrera"],
        "atributos": {
            a: {"valor": _valor(estudiante, a), "percentil": percentil(_valor(estudiante, a),
                                                                       [_valor(e, a) for e in poblacion])}
            for a in ATRIBUTOS
        },
    }


def repartir(poblacion: list[dict]) -> tuple[list[dict], list[dict]]:
    """Dos mazos de RONDAS cartas, todas de estudiantes distintos, al azar."""
    elegidos = secrets.SystemRandom().sample(poblacion, 2 * RONDAS)
    cartas = [carta(e, poblacion) for e in elegidos]
    return cartas[:RONDAS], cartas[RONDAS:]


def eleccion_de_la_maquina(carta_maquina: dict) -> str:
    """El atributo en el que su carta tiene el percentil más alto (si empatan,
    el primero en el orden de ATRIBUTOS): determinista, para que el búho lo
    pueda explicar."""
    return max(ATRIBUTOS, key=lambda a: (carta_maquina["atributos"][a]["percentil"], -list(ATRIBUTOS).index(a)))


def ganador(carta_a: dict, carta_b: dict, atributo: str) -> str:
    va, vb = carta_a["atributos"][atributo]["valor"], carta_b["atributos"][atributo]["valor"]
    return "a" if va > vb else "b" if vb > va else "empate"


# ---------- Explicación del búho ----------

def _formato(atributo: str, valor) -> str:
    return {"promedio": lambda v: numero(v), "asistencia": lambda v: f"{v} %",
            "horas_estudio": lambda v: f"{v} h", "semestre": lambda v: f"semestre {v}"}[atributo](valor)


def significado(atributo: str, r: float) -> str:
    """Qué significa ganar con ese atributo, con la correlación real con el promedio."""
    if atributo == "promedio":
        return ("El promedio es el resultado académico, lo que el modelo intenta predecir: "
                "ganar con él es ganar con el efecto, no con la causa.")
    # (sujeto, forma del verbo en singular o plural)
    sujeto, plural = {"asistencia": ("La asistencia mide cuánto va a clase", False),
                      "horas_estudio": ("Las horas de estudio miden cuánto estudia", True),
                      "semestre": ("El semestre solo dice cuánto lleva en la carrera", False)}[atributo]
    if abs(r) < 0.1:
        return (f"{sujeto}: «más alto» no es «mejor», porque casi no se "
                f"{'relacionan' if plural else 'relaciona'} con el promedio (r = {numero(r)}). "
                "Es ganar por un dato que no dice nada del rendimiento.")
    sentido = "mejor" if r > 0 else "peor"
    return (f"{sujeto}; en estos datos se {'asocian' if plural else 'asocia'} con {sentido} promedio "
            f"(r = {numero(r)}), pero una asociación no garantiza el resultado en cada estudiante.")


def explicacion(ronda: dict, mia: dict, rival: dict, soy: str) -> str:
    atributo = ronda["atributo"]
    nombre = ATRIBUTOS[atributo].nombre.lower()
    yo = mia["atributos"][atributo]
    otro = rival["atributos"][atributo]
    # Siempre "tu carta… y la del rival…": así se entiende quién tenía qué,
    # gane quien gane.
    cifras = (f"tu carta tiene {_formato(atributo, yo['valor'])} (percentil {yo['percentil']}) y la del "
              f"rival, {_formato(atributo, otro['valor'])} (percentil {otro['percentil']})")
    if ronda["ganador"] == "empate":
        inicio = f"Empate en {nombre}: {cifras}."
    elif ronda["ganador"] == soy:
        inicio = f"Ganaste con {nombre}: {cifras}."
    else:
        inicio = f"El rival ganó con {nombre}: {cifras}."
    quien = "Elegiste" if ronda["elegido_por"] == soy else "El rival eligió"
    return f"{inicio} {quien} {nombre}. {significado(atributo, ronda['r'])}"


# ---------- Estado ----------

def otro_lado(lado: str) -> str:
    return "b" if lado == "a" else "a"


def lado_de(duelo: Duelo, usuario_id: int) -> str | None:
    if duelo.jugador_a_id == usuario_id:
        return "a"
    if duelo.jugador_b_id == usuario_id:
        return "b"
    return None


def turno(duelo: Duelo) -> str | None:
    """Lado al que le toca elegir: "a" en las rondas impares, "b" en las pares."""
    if duelo.ronda is None:
        return None
    return "a" if duelo.ronda % 2 == 1 else "b"


def resultado_de(duelo: Duelo, lado: str) -> str | None:
    if duelo.resultado_a is None:
        return None
    if lado == "a" or duelo.resultado_a == "empate":
        return duelo.resultado_a
    return "derrota" if duelo.resultado_a == "victoria" else "victoria"


def inicio_del_dia() -> datetime:
    hoy = dia_colombia(reloj.ahora())
    return datetime(hoy.year, hoy.month, hoy.day) - DESFASE_COLOMBIA


def terminados_hoy(db: Session, usuario_id: int) -> int:
    """Duelos terminados hoy (hora de Colombia) por el jugador, sin contar abandonos."""
    return db.scalar(select(func.count()).select_from(Duelo).where(
        or_(Duelo.jugador_a_id == usuario_id, Duelo.jugador_b_id == usuario_id),
        Duelo.estado == "terminado", Duelo.terminado_en >= inicio_del_dia()))


# ---------- Acciones ----------

class JugadaInvalida(Exception):
    def __init__(self, estado: int, codigo: str, mensaje: str, **extra):
        super().__init__(mensaje)
        self.estado, self.codigo, self.mensaje, self.extra = estado, codigo, mensaje, extra


def crear_contra_maquina(db: Session, usuario_id: int) -> Duelo:
    """Abandona (sin XP) el duelo contra la máquina en juego, si lo hay, y reparte uno nuevo."""
    poblacion = cargar_estudiantes_con_nombre(db)
    if len(poblacion) < MINIMO_ESTUDIANTES:
        raise JugadaInvalida(409, "sin_datos", f"Hacen falta al menos {MINIMO_ESTUDIANTES} estudiantes para repartir")
    for anterior in db.scalars(select(Duelo).where(Duelo.jugador_a_id == usuario_id, Duelo.modo == "maquina",
                                                   Duelo.estado == "en_juego").with_for_update()):
        abandonar(anterior, "a")
    mazo_a, mazo_b = repartir(poblacion)
    duelo = Duelo(id=uuid.uuid4().hex, modo="maquina", estado="en_juego", jugador_a_id=usuario_id,
                  jugador_b_id=None, mazo_a=mazo_a, mazo_b=mazo_b, rondas=[], ronda=1)
    db.add(duelo)
    db.flush()
    return duelo


def cargar_estudiantes_con_nombre(db: Session) -> list[dict]:
    columnas = ("id", "nombre", "apellido", "carrera", "promedio", "asistencia", "horas_estudio", "semestre")
    return [{c: getattr(e, c) for c in columnas} for e in db.scalars(select(Estudiante))]


def jugar(db: Session, duelo: Duelo, lado: str, ronda: int, atributo: str | None) -> bool:
    """Resuelve una ronda. Devuelve True si se jugó, False si era un reintento
    idéntico de una ronda ya resuelta (no se juega dos veces)."""
    resueltas = {r["numero"]: r for r in duelo.rondas}
    if ronda in resueltas:  # ¿reintento de una ronda ya resuelta?
        previa = resueltas[ronda]
        esperado = previa["atributo"] if previa["elegido_por"] == lado else None
        if atributo == esperado:
            return False
        raise JugadaInvalida(409, "ronda_incorrecta", "Esa ronda ya se resolvió con otra jugada",
                             ronda_actual=duelo.ronda)
    if duelo.estado != "en_juego":
        raise JugadaInvalida(409, "duelo_terminado", "El duelo ya terminó")
    if ronda != duelo.ronda:
        raise JugadaInvalida(409, "ronda_incorrecta", f"Toca jugar la ronda {duelo.ronda}", ronda_actual=duelo.ronda)

    elige = turno(duelo)
    if elige == lado and atributo is None:
        raise JugadaInvalida(422, "atributo_requerido", "Es tu turno: elige un atributo")
    if elige != lado and atributo is not None:
        raise JugadaInvalida(409, "no_es_tu_turno", "Es el turno del rival")
    if elige != lado:  # turno de la máquina (en la v1 el rival siempre lo es)
        atributo = eleccion_de_la_maquina(duelo.mazo_b[ronda - 1] if elige == "b" else duelo.mazo_a[ronda - 1])

    carta_a, carta_b = duelo.mazo_a[ronda - 1], duelo.mazo_b[ronda - 1]
    estudiantes = cargar_estudiantes(db)
    r = correlacion([e[atributo] for e in estudiantes], [e["promedio"] for e in estudiantes]) if atributo != "promedio" else 1.0
    nueva = {"numero": ronda, "elegido_por": elige, "atributo": atributo,
             "ganador": ganador(carta_a, carta_b, atributo), "r": round(r, 4)}
    duelo.rondas = [*duelo.rondas, nueva]  # asignar una lista nueva: JSONTexto no detecta cambios internos
    if nueva["ganador"] == "a":
        duelo.puntos_a += 1
    elif nueva["ganador"] == "b":
        duelo.puntos_b += 1

    if ronda == RONDAS:
        terminar(db, duelo)
    else:
        duelo.ronda = ronda + 1
    return True


def terminar(db: Session, duelo: Duelo) -> None:
    # XP solo para los DUELOS_CON_XP_AL_DIA primeros del día. Se cuenta antes de
    # marcar este como terminado, para no contarlo a él.
    con_xp = terminados_hoy(db, duelo.jugador_a_id) < DUELOS_CON_XP_AL_DIA
    duelo.estado = "terminado"
    duelo.ronda = None
    duelo.terminado_en = reloj.ahora()
    duelo.resultado_a = ("victoria" if duelo.puntos_a > duelo.puntos_b
                         else "derrota" if duelo.puntos_b > duelo.puntos_a else "empate")
    if con_xp:
        duelo.xp_a = XP_POR_RESULTADO[duelo.resultado_a]


def abandonar(duelo: Duelo, lado: str) -> None:
    duelo.estado = "abandonado"
    duelo.ronda = None
    duelo.terminado_en = reloj.ahora()
    duelo.resultado_a = "derrota" if lado == "a" else "victoria"
    duelo.xp_a = duelo.xp_b = 0
