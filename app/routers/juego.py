"""Modo 2 (casos) y progresión del jugador. Contrato: docs/CONTRATO_JUEGO.md.

Todo requiere sesión. La XP, las insignias y si una respuesta es correcta los
decide siempre el servidor.
"""

from typing import Literal

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import casos as catalogo
from app import progreso, reloj, schemas
from app.database import get_db
from app.dependencias import requiere_rol, usuario_actual
from app.errores import error
from app.modelo_juego import ModeloJuego
from app.models import IntentoCaso, Usuario
from app.routers.prediccion import obtener_modelo_juego

router = APIRouter(prefix="/juego", tags=["Juego"], dependencies=[Depends(usuario_actual)])


# ---------- Ayudas compartidas con el Laboratorio ----------

def bloquear_usuario(db: Session, usuario: Usuario) -> None:
    """FOR UPDATE sobre el jugador: dos respuestas o rescates simultáneos del
    mismo jugador se ponen en fila y no pueden dar la misma XP dos veces."""
    db.execute(select(Usuario.id).where(Usuario.id == usuario.id).with_for_update())


def insignias_out(nuevas) -> list[schemas.InsigniaOut]:
    return [schemas.InsigniaOut(codigo=n.codigo, nombre=progreso.INSIGNIAS[n.codigo][0].nombre,
                                descripcion=progreso.INSIGNIAS[n.codigo][0].descripcion,
                                obtenida_en=n.obtenida_en)
            for n in nuevas]


def resumen(xp_antes: int, xp_despues: int) -> schemas.ProgresoResumenOut:
    return schemas.ProgresoResumenOut(
        xp=xp_despues, nivel=progreso.nivel_de(xp_despues).nombre,
        subio_de_nivel=progreso.nivel_de(xp_despues).numero > progreso.nivel_de(xp_antes).numero,
    )


# ---------- Casos ----------

def _caso(codigo: str) -> catalogo.Caso:
    caso = catalogo.CASOS.get(codigo)
    if caso is None:
        raise error(status.HTTP_404_NOT_FOUND, "caso_no_encontrado", "No existe ese caso")
    return caso


def _evaluar(db: Session, caso: catalogo.Caso, modelo: ModeloJuego) -> catalogo.Evaluacion:
    estudiantes = catalogo.cargar_estudiantes(db)
    if not estudiantes:
        raise error(status.HTTP_409_CONFLICT, "sin_datos", "No hay estudiantes: los casos no tienen respuesta")
    return caso.evaluar(estudiantes, modelo)


@router.get("/casos", response_model=schemas.CasosJuegoOut)
def listar_casos(usuario: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    resueltos = progreso.casos_resueltos(db, usuario.id)
    resumen_casos = []
    for caso in catalogo.CASOS.values():
        resumen_casos.append(schemas.CasoResumenJuegoOut(
            codigo=caso.codigo, nivel=caso.nivel, titulo=caso.titulo, xp=caso.xp,
            resuelto=caso.codigo in resueltos,
            intentos=progreso.intentos_en(db, usuario.id, caso.codigo),
            xp_ganada=sum(db.scalars(select(IntentoCaso.xp).where(
                IntentoCaso.usuario_id == usuario.id, IntentoCaso.caso == caso.codigo))),
        ))
    return schemas.CasosJuegoOut(casos=resumen_casos)


@router.get("/casos/{codigo}", response_model=schemas.CasoJuegoOut)
def obtener_caso(codigo: str, usuario: Usuario = Depends(usuario_actual), db: Session = Depends(get_db),
                 modelo: ModeloJuego = Depends(obtener_modelo_juego)):
    caso = _caso(codigo)
    evaluacion = _evaluar(db, caso, modelo)
    return schemas.CasoJuegoOut(
        codigo=caso.codigo, nivel=caso.nivel, titulo=caso.titulo, enunciado=evaluacion.enunciado,
        opciones=[schemas.OpcionOut(valor=o.valor, texto=o.texto) for o in evaluacion.opciones],
        grafica=caso.grafica, xp=caso.xp,
        resuelto=caso.codigo in progreso.casos_resueltos(db, usuario.id),
        intentos=progreso.intentos_en(db, usuario.id, caso.codigo),
    )


@router.post("/casos/{codigo}/responder", response_model=schemas.RespuestaOut)
def responder(codigo: str, datos: schemas.RespuestaEntrada, usuario: Usuario = Depends(usuario_actual),
              db: Session = Depends(get_db), modelo: ModeloJuego = Depends(obtener_modelo_juego)):
    caso = _caso(codigo)
    evaluacion = _evaluar(db, caso, modelo)
    validas = [o.valor for o in evaluacion.opciones]
    if datos.respuesta not in validas:
        raise error(422, "respuesta_invalida", "Esa respuesta no es una de las opciones del caso", opciones=validas)

    bloquear_usuario(db, usuario)
    xp_antes = progreso.xp_total(db, usuario.id)
    ya_resuelto = caso.codigo in progreso.casos_resueltos(db, usuario.id)
    intento = progreso.intentos_en(db, usuario.id, caso.codigo) + 1
    correcto = datos.respuesta in evaluacion.correctas
    xp = progreso.xp_de_caso(caso.xp, intento) if correcto and not ya_resuelto else 0

    db.add(IntentoCaso(usuario_id=usuario.id, caso=caso.codigo, respuesta=datos.respuesta,
                       correcto=correcto, xp=xp))
    db.flush()
    nuevas = progreso.otorgar(db, usuario.id)
    xp_despues = xp_antes + xp
    db.commit()

    return schemas.RespuestaOut(
        correcto=correcto,
        xp_ganada=xp,
        dato=evaluacion.dato_al_acertar if correcto else evaluacion.datos.get(datos.respuesta),
        explicacion=evaluacion.explicacion if correcto else None,
        pista=None if correcto else caso.pista,
        intento=intento,
        insignias_nuevas=insignias_out(nuevas),
        progreso=resumen(xp_antes, xp_despues),
    )


# ---------- Progreso, ranking y vista del docente ----------

@router.get("/progreso", response_model=schemas.ProgresoOut)
def mi_progreso(usuario: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    xp = progreso.xp_total(db, usuario.id)
    nivel = progreso.nivel_de(xp)
    siguiente = progreso.siguiente_nivel(nivel)
    estado = progreso.estado(db, usuario.id)
    ganadas = progreso.obtenidas(db, usuario.id)
    return schemas.ProgresoOut(
        alias=usuario.alias,
        xp=xp,
        nivel=schemas.NivelOut(**vars(nivel)),
        siguiente_nivel=schemas.NivelOut(**vars(siguiente)) if siguiente else None,
        racha_dias=estado.racha,
        casos_resueltos=len(estado.resueltos & set(catalogo.CASOS)),
        casos_total=len(catalogo.CASOS),
        rescates_logrados=len(estado.records),
        estrellas_totales=sum(estado.records.values()),
        insignias=[schemas.InsigniaOut(codigo=i.codigo, nombre=i.nombre, descripcion=i.descripcion,
                                       obtenida_en=ganadas.get(i.codigo))
                   for i, _ in progreso.INSIGNIAS.values()],
    )


@router.get("/ranking", response_model=schemas.RankingOut)
def ver_ranking(periodo: Literal["semana", "siempre"] = "semana", usuario: Usuario = Depends(usuario_actual),
                db: Session = Depends(get_db)):
    desde = progreso.inicio_semana() if periodo == "semana" else None
    filas = progreso.ranking(db, desde)
    posiciones = progreso.posiciones(filas)
    yo = next((schemas.MiPuestoOut(posicion=p, xp=xp) for p, (i, _, xp) in zip(posiciones, filas)
               if i == usuario.id), None)
    return schemas.RankingOut(
        periodo=periodo,
        desde=desde,
        jugadores=[schemas.PuestoOut(posicion=p, alias=alias, xp=xp, nivel=progreso.nivel_de(xp).nombre,
                                     soy_yo=i == usuario.id)
                   for p, (i, alias, xp) in list(zip(posiciones, filas))[:20]],
        yo=yo,
    )


@router.get("/jugadores", response_model=list[schemas.JugadorOut])
def jugadores(_: Usuario = Depends(requiere_rol("docente", "admin")), db: Session = Depends(get_db)):
    lista = []
    for jugador in db.scalars(select(Usuario).where(Usuario.rol == "jugador").order_by(Usuario.alias)):
        xp = progreso.xp_total(db, jugador.id)
        lista.append(schemas.JugadorOut(
            id=jugador.id, alias=jugador.alias, activo=jugador.activo, xp=xp,
            nivel=progreso.nivel_de(xp).nombre,
            casos_resueltos=len(progreso.casos_resueltos(db, jugador.id) & set(catalogo.CASOS)),
            rescates_logrados=len(progreso.records_de_rescate(db, jugador.id)),
            insignias=len(progreso.obtenidas(db, jugador.id)),
            ultima_actividad=progreso.ultima_actividad(db, jugador.id),
        ))
    return lista
