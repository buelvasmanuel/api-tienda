"""Progreso del jugador: XP, niveles, racha, insignias y ranking.

Nada de esto se guarda como total: todo se calcula a partir de las filas de
intentos_caso y rescates (y usuarios_insignias para las insignias ganadas).
Reglas: docs/CONTRATO_JUEGO.md §1, §4 y §5.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select, union_all
from sqlalchemy.orm import Session

from app import reloj
from app.casos import CASOS
from app.models import InsigniaObtenida, IntentoCaso, Rescate, Usuario

# Colombia no tiene horario de verano: UTC−5 todo el año. Un desfase fijo evita
# depender de la base de datos de zonas horarias del sistema.
DESFASE_COLOMBIA = timedelta(hours=-5)


@dataclass(frozen=True)
class Nivel:
    numero: int
    nombre: str
    xp_desde: int


NIVELES = [Nivel(1, "Novato", 0), Nivel(2, "Investigador", 150), Nivel(3, "Inspector", 400),
           Nivel(4, "Jefe de datos", 800)]

MULTIPLICADOR_POR_INTENTO = {1: 1.0, 2: 0.5}  # del 3.º en adelante, 0.25
XP_POR_ESTRELLA = 10


def nivel_de(xp: int) -> Nivel:
    return [n for n in NIVELES if xp >= n.xp_desde][-1]


def siguiente_nivel(nivel: Nivel) -> Nivel | None:
    return NIVELES[nivel.numero] if nivel.numero < len(NIVELES) else None


def xp_de_caso(xp_base: int, intento: int) -> int:
    """XP al resolver un caso por primera vez en el intento n.º `intento`."""
    return round(xp_base * MULTIPLICADOR_POR_INTENTO.get(intento, 0.25))


def dia_colombia(momento: datetime) -> date:
    return (momento + DESFASE_COLOMBIA).date()


def inicio_semana() -> datetime:
    """Lunes 00:00 de Colombia de esta semana, en UTC sin zona (como la BD)."""
    hoy = dia_colombia(reloj.ahora())
    lunes = hoy - timedelta(days=hoy.weekday())
    return datetime(lunes.year, lunes.month, lunes.day) - DESFASE_COLOMBIA


# ---------- Consultas ----------

def _eventos_xp():
    """(usuario_id, xp, creado_en) de casos y rescates juntos."""
    return union_all(
        select(IntentoCaso.usuario_id, IntentoCaso.xp, IntentoCaso.creado_en),
        select(Rescate.usuario_id, Rescate.xp, Rescate.creado_en),
    ).subquery()


def xp_total(db: Session, usuario_id: int) -> int:
    eventos = _eventos_xp()
    return int(db.scalar(select(func.coalesce(func.sum(eventos.c.xp), 0))
                         .where(eventos.c.usuario_id == usuario_id)))


def casos_resueltos(db: Session, usuario_id: int) -> set[str]:
    return set(db.scalars(select(IntentoCaso.caso).distinct()
                          .where(IntentoCaso.usuario_id == usuario_id, IntentoCaso.correcto)))


def intentos_en(db: Session, usuario_id: int, caso: str) -> int:
    return db.scalar(select(func.count()).select_from(IntentoCaso)
                     .where(IntentoCaso.usuario_id == usuario_id, IntentoCaso.caso == caso))


def casos_al_primer_intento(db: Session, usuario_id: int) -> int:
    """Casos cuyo primer intento fue correcto."""
    primeros = (select(IntentoCaso.caso, func.min(IntentoCaso.id).label("id"))
                .where(IntentoCaso.usuario_id == usuario_id).group_by(IntentoCaso.caso).subquery())
    return db.scalar(select(func.count()).select_from(IntentoCaso)
                     .join(primeros, IntentoCaso.id == primeros.c.id).where(IntentoCaso.correcto))


def records_de_rescate(db: Session, usuario_id: int) -> dict[int, int]:
    """{estudiante_id: mejor número de estrellas} de los rescates logrados."""
    filas = db.execute(select(Rescate.estudiante_id, func.max(Rescate.estrellas))
                       .where(Rescate.usuario_id == usuario_id, Rescate.logrado,
                              Rescate.estudiante_id.is_not(None))
                       .group_by(Rescate.estudiante_id))
    return {estudiante: estrellas for estudiante, estrellas in filas}


def record_de(db: Session, usuario_id: int, estudiante_id: int) -> int:
    return db.scalar(select(func.coalesce(func.max(Rescate.estrellas), 0))
                     .where(Rescate.usuario_id == usuario_id, Rescate.estudiante_id == estudiante_id))


def dias_activos(db: Session, usuario_id: int) -> set[date]:
    eventos = _eventos_xp()
    return {dia_colombia(m) for m in db.scalars(select(eventos.c.creado_en).where(eventos.c.usuario_id == usuario_id))}


def racha(dias: set[date], hoy: date) -> int:
    """Días seguidos con actividad terminando hoy o ayer."""
    dia = hoy if hoy in dias else hoy - timedelta(days=1)
    seguidos = 0
    while dia in dias:
        seguidos += 1
        dia -= timedelta(days=1)
    return seguidos


def ultima_actividad(db: Session, usuario_id: int) -> datetime | None:
    eventos = _eventos_xp()
    return db.scalar(select(func.max(eventos.c.creado_en)).where(eventos.c.usuario_id == usuario_id))


# ---------- Insignias ----------

@dataclass(frozen=True)
class Insignia:
    codigo: str
    nombre: str
    descripcion: str


@dataclass(frozen=True)
class Estado:
    """Lo que hace falta para decidir las insignias."""

    resueltos: set[str]
    al_primer_intento: int
    records: dict[int, int]
    racha: int


def estado(db: Session, usuario_id: int) -> Estado:
    return Estado(
        resueltos=casos_resueltos(db, usuario_id),
        al_primer_intento=casos_al_primer_intento(db, usuario_id),
        records=records_de_rescate(db, usuario_id),
        racha=racha(dias_activos(db, usuario_id), dia_colombia(reloj.ahora())),
    )


EXPERTOS = {c.codigo for c in CASOS.values() if c.nivel == "experto"}

INSIGNIAS: dict[str, tuple[Insignia, Callable[[Estado], bool]]] = {i.codigo: (i, condicion) for i, condicion in [
    (Insignia("primera_pista", "Primera pista", "Resuelve tu primer caso."),
     lambda e: len(e.resueltos) >= 1),
    (Insignia("ojo_de_halcon", "Ojo de halcón", "Resuelve 5 casos distintos al primer intento."),
     lambda e: e.al_primer_intento >= 5),
    (Insignia("mente_experta", "Mente experta", "Resuelve los 3 casos de nivel experto."),
     lambda e: EXPERTOS <= e.resueltos),
    (Insignia("primer_rescate", "Primer rescate", "Rescata a tu primer estudiante."),
     lambda e: len(e.records) >= 1),
    (Insignia("rescate_perfecto", "Rescate perfecto", "Logra un rescate con 3 estrellas."),
     lambda e: 3 in e.records.values()),
    (Insignia("rescatista", "Rescatista", "Rescata a 10 estudiantes distintos."),
     lambda e: len(e.records) >= 10),
    (Insignia("constancia", "Constancia", "Juega 3 días seguidos."),
     lambda e: e.racha >= 3),
]}


def obtenidas(db: Session, usuario_id: int) -> dict[str, datetime]:
    return dict(db.execute(select(InsigniaObtenida.codigo, InsigniaObtenida.obtenida_en)
                           .where(InsigniaObtenida.usuario_id == usuario_id)).all())


def otorgar(db: Session, usuario_id: int) -> list[InsigniaObtenida]:
    """Da las insignias que el jugador ya cumple y aún no tiene. Devuelve las nuevas."""
    actual = estado(db, usuario_id)
    ya = obtenidas(db, usuario_id)
    nuevas = [InsigniaObtenida(usuario_id=usuario_id, codigo=codigo)
              for codigo, (_, condicion) in INSIGNIAS.items() if codigo not in ya and condicion(actual)]
    db.add_all(nuevas)
    db.flush()
    return nuevas


# ---------- Ranking ----------

def ranking(db: Session, desde: datetime | None) -> list[tuple[int, str, int]]:
    """[(usuario_id, alias, xp)] de los jugadores activos con XP > 0 en el periodo,
    de más a menos XP y, si empatan, por alias."""
    eventos = _eventos_xp()
    xp = func.sum(eventos.c.xp).label("xp")
    consulta = (select(Usuario.id, Usuario.alias, xp)
                .join(eventos, eventos.c.usuario_id == Usuario.id)
                .where(Usuario.rol == "jugador", Usuario.activo)
                .group_by(Usuario.id, Usuario.alias)
                .having(xp > 0)
                .order_by(xp.desc(), Usuario.alias))
    if desde is not None:
        consulta = consulta.where(eventos.c.creado_en >= desde)
    return [(i, alias, int(x)) for i, alias, x in db.execute(consulta)]


def posiciones(filas: list[tuple[int, str, int]]) -> list[int]:
    """Posición con empates: 1, 2, 2, 4."""
    resultado = []
    for i, (_, _, xp) in enumerate(filas):
        resultado.append(resultado[-1] if i and xp == filas[i - 1][2] else i + 1)
    return resultado


# ---------- Vista del docente ----------

@dataclass(frozen=True)
class ResumenJugador:
    xp: int
    casos_resueltos: int
    rescates_logrados: int
    insignias: int
    ultima_actividad: datetime | None


def resumen_de_jugadores(db: Session, codigos_casos: set[str]) -> dict[int, ResumenJugador]:
    """Lo mismo que xp_total, casos_resueltos, records_de_rescate… pero para
    todos los usuarios a la vez, con una consulta agrupada por dato (y no unas
    seis por jugador: con 40 alumnos y Aiven a ~20 ms serían varios segundos)."""
    eventos = _eventos_xp()
    xp = dict(db.execute(select(eventos.c.usuario_id, func.sum(eventos.c.xp)).group_by(eventos.c.usuario_id)).all())
    ultima = dict(db.execute(select(eventos.c.usuario_id, func.max(eventos.c.creado_en))
                             .group_by(eventos.c.usuario_id)).all())
    resueltos = dict(db.execute(
        select(IntentoCaso.usuario_id, func.count(IntentoCaso.caso.distinct()))
        .where(IntentoCaso.correcto, IntentoCaso.caso.in_(codigos_casos)).group_by(IntentoCaso.usuario_id)).all())
    rescatados = dict(db.execute(
        select(Rescate.usuario_id, func.count(Rescate.estudiante_id.distinct()))
        .where(Rescate.logrado, Rescate.estudiante_id.is_not(None)).group_by(Rescate.usuario_id)).all())
    insignias = dict(db.execute(select(InsigniaObtenida.usuario_id, func.count())
                                .group_by(InsigniaObtenida.usuario_id)).all())
    ids = set(xp) | set(insignias)
    return {i: ResumenJugador(int(xp.get(i, 0)), resueltos.get(i, 0), rescatados.get(i, 0), insignias.get(i, 0),
                              ultima.get(i)) for i in ids}
