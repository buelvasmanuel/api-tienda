"""Laboratorio de Rescate (Modo 3 del juego). Contrato: docs/CONTRATO_LABORATORIO.md §4.

Requiere sesión (cualquier rol). Los casos se calculan en cada petición con
los estudiantes actuales y el modelo congelado del juego: 300 estudiantes ×
12 combinaciones son unas sumas, no hace falta caché.
"""

from decimal import Decimal

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import laboratorio as lab
from app import ml, progreso, schemas
from app.database import get_db
from app.dependencias import usuario_actual
from app.errores import error
from app.modelo_juego import AVISO, ModeloJuego
from app.models import Estudiante, Rescate, Usuario
from app.routers.juego import bloquear_usuario, insignias_out, resumen
from app.routers.prediccion import explicacion_out, obtener_modelo_juego

router = APIRouter(prefix="/laboratorio", tags=["Laboratorio"], dependencies=[Depends(usuario_actual)])


def _entrada(estudiante: Estudiante) -> dict:
    return {variable: getattr(estudiante, variable) for variable in ml.VARIABLES}


def _caso(db: Session, estudiante_id: int, modelo: ModeloJuego) -> Estudiante:
    estudiante = db.get(Estudiante, estudiante_id)
    if estudiante is None or not lab.es_caso(modelo, _entrada(estudiante)):
        raise error(status.HTTP_404_NOT_FOUND, "caso_no_disponible",
                    "Ese estudiante no es un caso de rescate (no existe, su riesgo es menor "
                    "del 60 % o no se puede rescatar con 5 fichas)")
    return estudiante


@router.get("/casos", response_model=schemas.CasosOut)
def listar_casos(db: Session = Depends(get_db), modelo: ModeloJuego = Depends(obtener_modelo_juego)):
    casos = []
    for estudiante in db.scalars(select(Estudiante).order_by(Estudiante.id)):
        entrada = _entrada(estudiante)
        if lab.es_caso(modelo, entrada):
            casos.append(schemas.CasoResumenOut(
                estudiante_id=estudiante.id, nombre=estudiante.nombre, apellido=estudiante.apellido,
                carrera=estudiante.carrera, semestre=estudiante.semestre,
                probabilidad=round(modelo.probabilidad(entrada), 4),
            ))
    casos.sort(key=lambda c: (-c.probabilidad, c.estudiante_id))
    return schemas.CasosOut(casos=casos, presupuesto=lab.PRESUPUESTO, objetivo=lab.OBJETIVO)


@router.get("/casos/{estudiante_id}", response_model=schemas.CasoOut)
def obtener_caso(estudiante_id: int, db: Session = Depends(get_db),
                 modelo: ModeloJuego = Depends(obtener_modelo_juego)):
    estudiante = _caso(db, estudiante_id, modelo)
    explicacion = modelo.explicar(_entrada(estudiante))
    return schemas.CasoOut(
        estudiante=schemas.EstudianteCasoOut.model_validate(estudiante),
        probabilidad=round(explicacion.probabilidad, 4),
        presupuesto=lab.PRESUPUESTO,
        objetivo=lab.OBJETIVO,
        intervenciones=[schemas.IntervencionOut(**vars(i)) for i in lab.INTERVENCIONES.values()],
        explicacion=explicacion_out(modelo, explicacion),
        aviso=AVISO,
    )


@router.post("/casos/{estudiante_id}/resolver", response_model=schemas.ResultadoRescateOut)
def resolver(estudiante_id: int, datos: schemas.ResolverEntrada, usuario: Usuario = Depends(usuario_actual),
             db: Session = Depends(get_db), modelo: ModeloJuego = Depends(obtener_modelo_juego)):
    """Evalúa un intento y lo guarda. Las estrellas y la XP salen de las
    intervenciones, nunca del cliente (docs/CONTRATO_JUEGO.md §6)."""
    estudiante = _caso(db, estudiante_id, modelo)
    entrada = _entrada(estudiante)
    intento = lab.intentar(modelo, entrada, datos.intervenciones)
    if intento.fichas > lab.PRESUPUESTO:
        raise error(422, "presupuesto_superado",
                    f"Esas intervenciones cuestan {intento.fichas} fichas y el presupuesto es {lab.PRESUPUESTO}",
                    fichas=intento.fichas, presupuesto=lab.PRESUPUESTO)
    optima = lab.solucion_optima(modelo, entrada)  # existe: _caso ya comprobó que es resoluble
    estrellas = lab.estrellas(intento, optima)
    probabilidad_inicial = round(modelo.probabilidad(entrada), 4)

    bloquear_usuario(db, usuario)
    xp_antes = progreso.xp_total(db, usuario.id)
    record_anterior = progreso.record_de(db, usuario.id, estudiante.id)
    xp = progreso.XP_POR_ESTRELLA * max(0, estrellas - record_anterior)
    db.add(Rescate(
        usuario_id=usuario.id, estudiante_id=estudiante.id,
        horas_estudio_inicial=entrada["horas_estudio"], asistencia_inicial=entrada["asistencia"],
        estudio=intento.conteo["estudio"], tutoria=intento.conteo["tutoria"], fichas=intento.fichas,
        probabilidad_inicial=Decimal(str(probabilidad_inicial)),
        probabilidad_final=Decimal(str(round(intento.probabilidad, 4))),
        logrado=intento.logrado, estrellas=estrellas, xp=xp, version_modelo=modelo.version,
    ))
    db.flush()
    nuevas = progreso.otorgar(db, usuario.id)
    db.commit()

    return schemas.ResultadoRescateOut(
        logrado=intento.logrado,
        estrellas=estrellas,
        fichas_usadas=intento.fichas,
        fichas_optimas=optima.fichas,
        probabilidad_inicial=probabilidad_inicial,
        probabilidad_final=round(intento.probabilidad, 4),
        estado_final=schemas.EstadoFinalOut(horas_estudio=intento.entrada["horas_estudio"],
                                            asistencia=intento.entrada["asistencia"]),
        solucion_optima=schemas.SolucionOut(**optima.conteo, fichas=optima.fichas,
                                            probabilidad=round(optima.probabilidad, 4)),
        explicacion_final=explicacion_out(modelo, modelo.explicar(intento.entrada)),
        aviso=AVISO,
        xp_ganada=xp,
        record_estrellas=max(record_anterior, estrellas),
        insignias_nuevas=insignias_out(nuevas),
        progreso=resumen(xp_antes, xp_antes + xp),
    )
