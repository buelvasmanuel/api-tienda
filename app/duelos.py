"""Reglas del Duelo de cartas (docs/CONTRATO_DUELO.md).

El servidor reparte, guarda los mazos, decide el turno, valida cada jugada y
calcula el resultado y la XP: el cliente solo dice qué atributo elige.

Lados: "a" es quien creó el duelo; "b", el rival (la máquina en la v1). Las
respuestas se dan siempre desde el punto de vista de quien pregunta ("yo" y
"rival"), así el mismo código servirá para jugador contra jugador.
"""

import secrets
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session

from app import reloj
from app.casos import cargar_estudiantes, correlacion, numero
from app.models import Duelo, Estudiante
from app.progreso import DESFASE_COLOMBIA, dia_colombia

RONDAS = 5
MINIMO_ESTUDIANTES = 2 * RONDAS
XP_POR_RESULTADO = {  # por modo
    "maquina": {"victoria": 30, "empate": 15, "derrota": 5},
    "jugador": {"victoria": 40, "empate": 20, "derrota": 10},
}
DUELOS_CON_XP_AL_DIA = 3  # por modo
ALIAS_MAQUINA = "Búho Máquina"

# v2 (jugador contra jugador)
ESPERA = timedelta(seconds=60)  # cuánto se busca rival
TURNO = timedelta(seconds=45)  # cuánto tiene quien elige
TURNOS_VENCIDOS_PARA_ABANDONO = 2
RONDAS_MINIMAS_PARA_XP_POR_ABANDONO = 3
CANDADO_EMPAREJAMIENTO = "api_tienda_duelos_emparejamiento"


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
    if ronda.get("motivo") == "tiempo":
        if ronda["ganador"] == soy:
            return "Al rival se le acabó el tiempo de su turno: la ronda es para ti."
        return "Se te acabó el tiempo del turno: la ronda es para el rival."
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


def con_xp_hoy(db: Session, usuario_id: int, modo: str) -> int:
    """Duelos de ese modo que hoy (hora de Colombia) ocupan un hueco de XP del
    jugador: los terminados y los ganados por abandono del rival con XP."""
    gano_por_abandono = or_(and_(Duelo.jugador_a_id == usuario_id, Duelo.xp_a > 0),
                            and_(Duelo.jugador_b_id == usuario_id, Duelo.xp_b > 0))
    return db.scalar(select(func.count()).select_from(Duelo).where(
        or_(Duelo.jugador_a_id == usuario_id, Duelo.jugador_b_id == usuario_id),
        Duelo.modo == modo, Duelo.terminado_en >= inicio_del_dia(),
        or_(Duelo.estado == "terminado", and_(Duelo.estado == "abandonado", gano_por_abandono))))


# ---------- Acciones ----------

class JugadaInvalida(Exception):
    def __init__(self, estado: int, codigo: str, mensaje: str, **extra):
        super().__init__(mensaje)
        self.estado, self.codigo, self.mensaje, self.extra = estado, codigo, mensaje, extra


def cargar_estudiantes_con_nombre(db: Session) -> list[dict]:
    columnas = ("id", "nombre", "apellido", "carrera", "promedio", "asistencia", "horas_estudio", "semestre")
    return [{c: getattr(e, c) for c in columnas} for e in db.scalars(select(Estudiante))]


def _poblacion(db: Session) -> list[dict]:
    poblacion = cargar_estudiantes_con_nombre(db)
    if len(poblacion) < MINIMO_ESTUDIANTES:
        raise JugadaInvalida(409, "sin_datos", f"Hacen falta al menos {MINIMO_ESTUDIANTES} estudiantes para repartir")
    return poblacion


def crear_contra_maquina(db: Session, usuario_id: int) -> Duelo:
    """Abandona (sin XP) el duelo contra la máquina en juego, si lo hay, y reparte uno nuevo."""
    poblacion = _poblacion(db)
    for anterior in db.scalars(select(Duelo).where(Duelo.jugador_a_id == usuario_id, Duelo.modo == "maquina",
                                                   Duelo.estado == "en_juego").with_for_update()):
        abandonar(db, anterior, "a")
    mazo_a, mazo_b = repartir(poblacion)
    duelo = Duelo(id=uuid.uuid4().hex, modo="maquina", estado="en_juego", jugador_a_id=usuario_id,
                  jugador_b_id=None, mazo_a=mazo_a, mazo_b=mazo_b, rondas=[], ronda=1)
    db.add(duelo)
    db.flush()
    return duelo


@contextmanager
def candado(db: Session, nombre: str, segundos: int = 10):
    """Candado con nombre de MySQL/MariaDB (GET_LOCK): pone en fila a quien lo
    pida, aunque no haya ninguna fila que bloquear (la cola puede estar vacía).

    GET_LOCK pertenece a la CONEXIÓN que lo pide. Por eso va en una conexión
    propia, abierta aquí y soltada aquí: la de la sesión se devuelve al pool
    en cada commit, y un RELEASE_LOCK hecho después iría a otra conexión y
    dejaría el candado cogido para siempre (pasó en producción).
    """
    vinculo = db.get_bind()
    motor = getattr(vinculo, "engine", vinculo)  # en los tests la sesión va atada a una Connection
    with motor.connect() as conexion:
        if conexion.execute(text("SELECT GET_LOCK(:n, :s)"), {"n": nombre, "s": segundos}).scalar() != 1:
            raise JugadaInvalida(503, "emparejamiento_ocupado", "Hay mucha gente buscando rival: inténtalo de nuevo")
        try:
            yield
        finally:
            conexion.execute(text("SELECT RELEASE_LOCK(:n)"), {"n": nombre})


def buscar_rival(db: Session, usuario_id: int) -> tuple[Duelo, bool]:
    """Emparejamiento (CONTRATO_DUELO §7.1). Devuelve (duelo, es_nuevo).
    Hay que llamarla dentro de `candado(db, CANDADO_EMPAREJAMIENTO)`."""
    ahora = reloj.ahora()
    propios = db.scalars(select(Duelo).where(
        or_(Duelo.jugador_a_id == usuario_id, Duelo.jugador_b_id == usuario_id),
        Duelo.modo == "jugador", Duelo.estado.in_(("esperando", "en_juego"))).with_for_update()).all()
    for duelo in propios:
        aplicar_plazos(db, duelo)
    for duelo in propios:
        if duelo.estado == "en_juego":
            raise JugadaInvalida(409, "duelo_en_curso", "Ya tienes un duelo contra un jugador en juego",
                                 duelo_id=duelo.id)
        if duelo.estado == "esperando":
            return duelo, False

    esperando = db.scalar(select(Duelo).where(
        Duelo.modo == "jugador", Duelo.estado == "esperando", Duelo.jugador_a_id != usuario_id,
        Duelo.expira_turno_en > ahora).order_by(Duelo.creado_en, Duelo.id).limit(1).with_for_update())
    if esperando is not None:
        mazo_a, mazo_b = repartir(_poblacion(db))
        esperando.jugador_b_id = usuario_id
        esperando.estado = "en_juego"
        esperando.mazo_a, esperando.mazo_b = mazo_a, mazo_b
        esperando.ronda = 1
        esperando.expira_turno_en = ahora + TURNO
        db.flush()
        return esperando, True

    _poblacion(db)  # comprobar que se podrá repartir antes de dejarle esperando
    duelo = Duelo(id=uuid.uuid4().hex, modo="jugador", estado="esperando", jugador_a_id=usuario_id,
                  jugador_b_id=None, mazo_a=[], mazo_b=[], rondas=[], ronda=None,
                  expira_turno_en=ahora + ESPERA)
    db.add(duelo)
    db.flush()
    return duelo, True


def _anotar(db: Session, duelo: Duelo, ronda: dict) -> None:
    duelo.rondas = [*duelo.rondas, ronda]  # asignar una lista nueva: JSONTexto no detecta cambios internos
    if ronda["ganador"] == "a":
        duelo.puntos_a += 1
    elif ronda["ganador"] == "b":
        duelo.puntos_b += 1


def _resolver(db: Session, duelo: Duelo, ronda: int, elige: str, atributo: str) -> None:
    carta_a, carta_b = duelo.mazo_a[ronda - 1], duelo.mazo_b[ronda - 1]
    if atributo == "promedio":
        r = 1.0
    else:
        estudiantes = cargar_estudiantes(db)
        r = correlacion([e[atributo] for e in estudiantes], [e["promedio"] for e in estudiantes])
    _anotar(db, duelo, {"numero": ronda, "elegido_por": elige, "atributo": atributo, "motivo": "jugada",
                        "ganador": ganador(carta_a, carta_b, atributo), "r": round(r, 4)})
    _avanzar(db, duelo, ronda)


def _avanzar(db: Session, duelo: Duelo, ronda: int, desde: datetime | None = None) -> None:
    if ronda == RONDAS:
        terminar(db, duelo)
        return
    duelo.ronda = ronda + 1
    if duelo.modo == "jugador":
        duelo.expira_turno_en = (desde or reloj.ahora()) + TURNO


def aplicar_plazos(db: Session, duelo: Duelo) -> bool:
    """Aplica los vencimientos pendientes (CONTRATO_DUELO §7.2). Se llama con la
    fila bloqueada cada vez que se lee o escribe un duelo contra un jugador:
    no hay temporizadores, así que da igual si el servidor estuvo dormido.
    Devuelve True si cambió algo."""
    if duelo.modo != "jugador" or duelo.expira_turno_en is None:
        return False
    ahora = reloj.ahora()
    if duelo.estado == "esperando":
        if ahora < duelo.expira_turno_en:
            return False
        duelo.estado = "abandonado"  # nadie se unió: se cancela sin resultado
        duelo.terminado_en = ahora
        duelo.expira_turno_en = None
        return True

    cambio = False
    while duelo.estado == "en_juego" and ahora >= duelo.expira_turno_en:
        cambio = True
        ronda, elige, vencio = duelo.ronda, turno(duelo), duelo.expira_turno_en
        _anotar(db, duelo, {"numero": ronda, "elegido_por": elige, "atributo": None, "motivo": "tiempo",
                            "ganador": otro_lado(elige), "r": None})
        suyas = [r for r in duelo.rondas if r["elegido_por"] == elige][-TURNOS_VENCIDOS_PARA_ABANDONO:]
        if len(suyas) == TURNOS_VENCIDOS_PARA_ABANDONO and all(r["motivo"] == "tiempo" for r in suyas):
            abandonar(db, duelo, elige, cuando=vencio)
        else:
            _avanzar(db, duelo, ronda, desde=vencio)
    return cambio


def jugar(db: Session, duelo: Duelo, lado: str, ronda: int, atributo: str | None) -> bool:
    """Resuelve una ronda. Devuelve True si se jugó, False si era un reintento
    idéntico de una ronda ya resuelta (no se juega dos veces)."""
    resueltas = {r["numero"]: r for r in duelo.rondas}
    if ronda in resueltas:  # ¿reintento de una ronda ya resuelta?
        previa = resueltas[ronda]
        if previa.get("motivo") == "tiempo" and previa["elegido_por"] == lado:
            raise JugadaInvalida(409, "tiempo_agotado", "Se acabó el tiempo de ese turno", ronda_actual=duelo.ronda)
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
    contra_maquina = duelo.modo == "maquina"
    if elige == lado and atributo is None:
        raise JugadaInvalida(422, "atributo_requerido", "Es tu turno: elige un atributo")
    if elige != lado and (atributo is not None or not contra_maquina):
        raise JugadaInvalida(409, "no_es_tu_turno", "Es el turno del rival")
    if elige != lado:  # turno de la máquina
        atributo = eleccion_de_la_maquina(duelo.mazo_b[ronda - 1])
    _resolver(db, duelo, ronda, elige, atributo)
    return True


def _xp(db: Session, duelo: Duelo, usuario_id: int, resultado: str) -> int:
    """XP de ese resultado si al jugador le quedan duelos con XP hoy en ese modo.
    Se llama antes de marcar el duelo como terminado, para no contarlo a él."""
    if con_xp_hoy(db, usuario_id, duelo.modo) >= DUELOS_CON_XP_AL_DIA:
        return 0
    return XP_POR_RESULTADO[duelo.modo][resultado]


def terminar(db: Session, duelo: Duelo) -> None:
    resultado_a = ("victoria" if duelo.puntos_a > duelo.puntos_b
                   else "derrota" if duelo.puntos_b > duelo.puntos_a else "empate")
    duelo.xp_a = _xp(db, duelo, duelo.jugador_a_id, resultado_a)
    if duelo.jugador_b_id is not None:
        duelo.xp_b = _xp(db, duelo, duelo.jugador_b_id, resultado_de_b(resultado_a))
    duelo.estado = "terminado"
    duelo.resultado_a = resultado_a
    duelo.ronda = None
    duelo.expira_turno_en = None
    duelo.terminado_en = reloj.ahora()


def resultado_de_b(resultado_a: str) -> str:
    return {"victoria": "derrota", "derrota": "victoria", "empate": "empate"}[resultado_a]


def abandonar(db: Session, duelo: Duelo, lado: str, cuando: datetime | None = None) -> None:
    """Abandono de `lado` (también por turnos vencidos). Contra un jugador, el
    otro gana; su XP solo si ya se habían resuelto RONDAS_MINIMAS rondas."""
    if duelo.estado == "esperando":  # cancelar la búsqueda: sin resultado
        duelo.estado = "abandonado"
        duelo.expira_turno_en = None
        duelo.terminado_en = cuando or reloj.ahora()
        return
    ganador_id = duelo.jugador_b_id if lado == "a" else duelo.jugador_a_id
    xp_ganador = 0
    if ganador_id is not None and len(duelo.rondas) >= RONDAS_MINIMAS_PARA_XP_POR_ABANDONO:
        xp_ganador = _xp(db, duelo, ganador_id, "victoria")
    duelo.xp_a, duelo.xp_b = (0, xp_ganador) if lado == "a" else (xp_ganador, 0)
    duelo.estado = "abandonado"
    duelo.resultado_a = "derrota" if lado == "a" else "victoria"
    duelo.ronda = None
    duelo.expira_turno_en = None
    duelo.terminado_en = cuando or reloj.ahora()
