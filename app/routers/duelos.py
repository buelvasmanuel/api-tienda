"""Duelo de cartas (Modo 4). Contrato: docs/CONTRATO_DUELO.md.

Requiere sesión. El servidor reparte, valida y decide; las respuestas van
siempre desde el punto de vista de quien pregunta ("yo" / "rival").
"""

from typing import Literal

from fastapi import APIRouter, Depends, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import duelos as reglas
from app import progreso, schemas
from app.database import get_db
from app.dependencias import usuario_actual
from app.errores import error
from app.models import Duelo, Usuario
from app.routers.juego import bloquear_usuario, insignias_out, resumen

router = APIRouter(prefix="/duelos", tags=["Duelo"], dependencies=[Depends(usuario_actual)])


def _perspectiva(lado_ronda: str, soy: str) -> str:
    return "empate" if lado_ronda == "empate" else "yo" if lado_ronda == soy else "rival"


def _rival(db: Session, duelo: Duelo, soy: str) -> schemas.RivalOut:
    rival_id = duelo.jugador_b_id if soy == "a" else duelo.jugador_a_id
    if rival_id is None:
        return schemas.RivalOut(tipo="maquina", alias=reglas.ALIAS_MAQUINA)
    return schemas.RivalOut(tipo="jugador", alias=db.get(Usuario, rival_id).alias)


def _mazos(duelo: Duelo, soy: str) -> tuple[list, list]:
    return (duelo.mazo_a, duelo.mazo_b) if soy == "a" else (duelo.mazo_b, duelo.mazo_a)


def duelo_out(db: Session, duelo: Duelo, usuario: Usuario, insignias=(), progreso_=None) -> schemas.DueloOut:
    soy = reglas.lado_de(duelo, usuario.id)
    mio, suyo = _mazos(duelo, soy)
    puntos_yo, puntos_rival = (duelo.puntos_a, duelo.puntos_b) if soy == "a" else (duelo.puntos_b, duelo.puntos_a)
    rondas = []
    for r in duelo.rondas:
        mia, rival = mio[r["numero"] - 1], suyo[r["numero"] - 1]
        rondas.append(schemas.RondaOut(
            numero=r["numero"], elegido_por=_perspectiva(r["elegido_por"], soy), atributo=r["atributo"],
            mi_carta=mia, carta_rival=rival, ganador=_perspectiva(r["ganador"], soy),
            explicacion=reglas.explicacion(r, mia, rival, soy),
        ))
    turno = reglas.turno(duelo)
    return schemas.DueloOut(
        id=duelo.id, modo=duelo.modo, estado=duelo.estado, rival=_rival(db, duelo, soy),
        rondas_total=reglas.RONDAS, ronda=duelo.ronda,
        turno=None if turno is None else ("yo" if turno == soy else "rival"),
        marcador=schemas.MarcadorOut(yo=puntos_yo, rival=puntos_rival),
        # Solo la carta propia de la ronda en curso: nunca la del rival ni las siguientes.
        mi_carta=mio[duelo.ronda - 1] if duelo.ronda else None,
        rondas=rondas,
        resultado=reglas.resultado_de(duelo, soy),
        xp_ganada=duelo.xp_a if soy == "a" else duelo.xp_b,
        duelos_con_xp_hoy=schemas.DuelosConXpOut(
            jugados=min(reglas.terminados_hoy(db, usuario.id), reglas.DUELOS_CON_XP_AL_DIA),
            maximo=reglas.DUELOS_CON_XP_AL_DIA),
        atributos=[schemas.AtributoOut(**vars(a)) for a in reglas.ATRIBUTOS.values()],
        expira_turno_en=duelo.expira_turno_en,
        creado_en=duelo.creado_en, actualizado_en=duelo.actualizado_en,
        insignias_nuevas=insignias_out(insignias), progreso=progreso_,
    )


def _mio(db: Session, duelo_id: str, usuario: Usuario, bloquear: bool = False) -> Duelo:
    """El duelo, si existe y el usuario juega en él (404 en los dos casos)."""
    consulta = select(Duelo).where(Duelo.id == duelo_id)
    duelo = db.scalar(consulta.with_for_update() if bloquear else consulta)
    if duelo is None or reglas.lado_de(duelo, usuario.id) is None:
        raise error(status.HTTP_404_NOT_FOUND, "duelo_no_encontrado", "No existe ese duelo")
    return duelo


def _http(e: reglas.JugadaInvalida):
    return error(e.estado, e.codigo, e.mensaje, **e.extra)


@router.post("", response_model=schemas.DueloOut, status_code=status.HTTP_201_CREATED)
def crear(_: schemas.NuevoDueloEntrada, usuario: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    bloquear_usuario(db, usuario)  # dos "nuevo duelo" a la vez no dejan dos en juego
    try:
        duelo = reglas.crear_contra_maquina(db, usuario.id)
    except reglas.JugadaInvalida as e:
        raise _http(e)
    db.commit()
    return duelo_out(db, duelo, usuario)


@router.get("", response_model=schemas.DuelosOut)
def listar(estado: Literal["en_juego", "terminado"] | None = None, usuario: Usuario = Depends(usuario_actual),
           db: Session = Depends(get_db)):
    consulta = (select(Duelo).where(or_(Duelo.jugador_a_id == usuario.id, Duelo.jugador_b_id == usuario.id))
                .order_by(Duelo.creado_en.desc(), Duelo.id).limit(20))
    if estado:
        consulta = consulta.where(Duelo.estado == estado)
    resumenes = []
    for duelo in db.scalars(consulta):
        d = duelo_out(db, duelo, usuario)
        resumenes.append(schemas.DueloResumenOut(id=d.id, modo=d.modo, estado=d.estado, rival=d.rival,
                                                 marcador=d.marcador, resultado=d.resultado,
                                                 xp_ganada=d.xp_ganada, creado_en=d.creado_en))
    return schemas.DuelosOut(duelos=resumenes)


@router.get("/{duelo_id}", response_model=schemas.DueloOut)
def obtener(duelo_id: str, usuario: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    return duelo_out(db, _mio(db, duelo_id, usuario), usuario)


@router.post("/{duelo_id}/jugadas", response_model=schemas.DueloOut)
def jugar(duelo_id: str, datos: schemas.JugadaEntrada, usuario: Usuario = Depends(usuario_actual),
          db: Session = Depends(get_db)):
    bloquear_usuario(db, usuario)  # la XP del final no puede darse dos veces
    duelo = _mio(db, duelo_id, usuario, bloquear=True)
    lado = reglas.lado_de(duelo, usuario.id)
    xp_antes = progreso.xp_total(db, usuario.id)
    try:
        jugada_nueva = reglas.jugar(db, duelo, lado, datos.ronda, datos.atributo)
    except reglas.JugadaInvalida as e:
        raise _http(e)
    nuevas, resumen_progreso = [], None
    if jugada_nueva and duelo.estado == "terminado":
        db.flush()
        nuevas = progreso.otorgar(db, usuario.id)
        resumen_progreso = resumen(xp_antes, progreso.xp_total(db, usuario.id))
    db.commit()
    return duelo_out(db, duelo, usuario, nuevas, resumen_progreso)


@router.post("/{duelo_id}/abandonar", response_model=schemas.DueloOut)
def abandonar(duelo_id: str, usuario: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    duelo = _mio(db, duelo_id, usuario, bloquear=True)
    lado = reglas.lado_de(duelo, usuario.id)
    if duelo.estado == "abandonado" and reglas.resultado_de(duelo, lado) == "derrota":
        return duelo_out(db, duelo, usuario)  # repetirlo devuelve el mismo estado
    if duelo.estado != "en_juego":
        raise error(status.HTTP_409_CONFLICT, "duelo_terminado", "El duelo ya terminó")
    reglas.abandonar(duelo, lado)
    db.commit()
    return duelo_out(db, duelo, usuario)
