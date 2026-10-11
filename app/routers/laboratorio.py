"""Laboratorio de Rescate (Modo 3 del juego). Contrato: docs/CONTRATO_LABORATORIO.md §4.

Requiere sesión (cualquier rol). Los casos se calculan en cada petición con
los estudiantes actuales y el modelo congelado del juego: 300 estudiantes ×
12 combinaciones son unas sumas, no hace falta caché.
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import laboratorio as lab
from app import ml, schemas
from app.database import get_db
from app.dependencias import usuario_actual
from app.errores import error
from app.modelo_juego import AVISO, ModeloJuego
from app.models import Estudiante
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
def resolver(estudiante_id: int, datos: schemas.ResolverEntrada, db: Session = Depends(get_db),
             modelo: ModeloJuego = Depends(obtener_modelo_juego)):
    """Evalúa un intento. Las estrellas salen de las intervenciones, nunca del cliente."""
    estudiante = _caso(db, estudiante_id, modelo)
    entrada = _entrada(estudiante)
    intento = lab.intentar(modelo, entrada, datos.intervenciones)
    if intento.fichas > lab.PRESUPUESTO:
        raise error(422, "presupuesto_superado",
                    f"Esas intervenciones cuestan {intento.fichas} fichas y el presupuesto es {lab.PRESUPUESTO}",
                    fichas=intento.fichas, presupuesto=lab.PRESUPUESTO)
    optima = lab.solucion_optima(modelo, entrada)  # existe: _caso ya comprobó que es resoluble

    return schemas.ResultadoRescateOut(
        logrado=intento.logrado,
        estrellas=lab.estrellas(intento, optima),
        fichas_usadas=intento.fichas,
        fichas_optimas=optima.fichas,
        probabilidad_inicial=round(modelo.probabilidad(entrada), 4),
        probabilidad_final=round(intento.probabilidad, 4),
        estado_final=schemas.EstadoFinalOut(horas_estudio=intento.entrada["horas_estudio"],
                                            asistencia=intento.entrada["asistencia"]),
        solucion_optima=schemas.SolucionOut(**optima.conteo, fichas=optima.fichas,
                                            probabilidad=round(optima.probabilidad, 4)),
        explicacion_final=explicacion_out(modelo, modelo.explicar(intento.entrada)),
        aviso=AVISO,
    )
