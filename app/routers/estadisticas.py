"""Estadísticas de estudiantes para gráficas.

Todas responden con el mismo formato: [{"etiqueta": str, "valor": number}, ...].
El agrupamiento, los conteos, las medias, el orden y el límite los calcula
MariaDB (GROUP BY); Python solo da forma a las filas resultantes.
"""

from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app import schemas
from app.database import get_db
from app.models import Estudiante

router = APIRouter(prefix="/estudiantes/estadisticas", tags=["Estadísticas"])

RIESGO = 3  # promedio por debajo del cual un estudiante está en riesgo académico


def _a_estadisticas(filas) -> list[schemas.EstadisticaOut]:
    """Convierte filas (etiqueta, valor) de SQL al formato común."""
    return [
        schemas.EstadisticaOut(
            etiqueta=str(etiqueta),
            # AVG/ROUND devuelven Decimal; COUNT devuelve int.
            valor=float(valor) if isinstance(valor, Decimal) else valor,
        )
        for etiqueta, valor in filas
    ]


def _conteo_por(columna, *, orden_por_cantidad: bool, limite: int | None = None):
    """SELECT columna, COUNT(*) FROM estudiantes GROUP BY columna ORDER BY ..."""
    cantidad = func.count().label("valor")
    consulta = select(columna, cantidad).group_by(columna)
    if orden_por_cantidad:
        consulta = consulta.order_by(cantidad.desc(), columna)  # desempate por nombre
    else:
        consulta = consulta.order_by(columna)
    return consulta.limit(limite) if limite else consulta


@router.get("/por-carrera", response_model=list[schemas.EstadisticaOut])
def por_carrera(db: Session = Depends(get_db)):
    """Cantidad de estudiantes por carrera (de más a menos)."""
    consulta = _conteo_por(Estudiante.carrera, orden_por_cantidad=True)
    return _a_estadisticas(db.execute(consulta))


@router.get("/por-edad", response_model=list[schemas.EstadisticaOut])
def por_edad(db: Session = Depends(get_db)):
    """Cantidad de estudiantes por edad (edad ascendente)."""
    consulta = _conteo_por(Estudiante.edad, orden_por_cantidad=False)
    return _a_estadisticas(db.execute(consulta))


@router.get("/por-semestre", response_model=list[schemas.EstadisticaOut])
def por_semestre(db: Session = Depends(get_db)):
    """Cantidad de estudiantes por semestre (semestre ascendente)."""
    consulta = _conteo_por(Estudiante.semestre, orden_por_cantidad=False)
    return _a_estadisticas(db.execute(consulta))


@router.get("/por-ciudad", response_model=list[schemas.EstadisticaOut])
def por_ciudad(db: Session = Depends(get_db)):
    """Las 10 ciudades con más estudiantes."""
    consulta = _conteo_por(Estudiante.ciudad, orden_por_cantidad=True, limite=10)
    return _a_estadisticas(db.execute(consulta))


@router.get("/promedio-por-carrera", response_model=list[schemas.EstadisticaOut])
def promedio_por_carrera(db: Session = Depends(get_db)):
    """Promedio académico medio por carrera, redondeado a 2 decimales."""
    media = func.round(func.avg(Estudiante.promedio), 2).label("valor")
    consulta = (
        select(Estudiante.carrera, media)
        .group_by(Estudiante.carrera)
        .order_by(media.desc(), Estudiante.carrera)
    )
    return _a_estadisticas(db.execute(consulta))


@router.get("/riesgo-por-carrera", response_model=list[schemas.EstadisticaOut])
def riesgo_por_carrera(db: Session = Depends(get_db)):
    """Porcentaje de estudiantes con promedio < 3.0 en cada carrera.

    CASE convierte cada estudiante en 1 (en riesgo) o 0; la media de esos
    unos y ceros es la proporción en riesgo, y por 100 el porcentaje.
    """
    en_riesgo = case((Estudiante.promedio < RIESGO, 1), else_=0)
    porcentaje = func.round(100 * func.avg(en_riesgo), 2).label("valor")
    consulta = (
        select(Estudiante.carrera, porcentaje)
        .group_by(Estudiante.carrera)
        .order_by(porcentaje.desc(), Estudiante.carrera)
    )
    return _a_estadisticas(db.execute(consulta))
