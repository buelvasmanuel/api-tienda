from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class CategoriaCreate(BaseModel):
    nombre: str = Field(min_length=1, max_length=50)

    # Quita espacios al inicio y al final antes de validar: "  Ropa  " -> "Ropa".
    model_config = ConfigDict(str_strip_whitespace=True)


class CategoriaOut(CategoriaCreate):
    id: int
    model_config = ConfigDict(from_attributes=True)


class ProductoCreate(BaseModel):
    nombre: str = Field(min_length=1, max_length=100)
    precio: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    stock: int = Field(ge=0, default=0)
    categoria_id: int | None = None

    model_config = ConfigDict(str_strip_whitespace=True)


class ProductoOut(ProductoCreate):
    id: int
    model_config = ConfigDict(from_attributes=True)


# Rangos de un estudiante, definidos una sola vez y compartidos por
# EstudianteCreate y PrediccionEntrada.
Edad = Annotated[int, Field(ge=16, le=35)]
Carrera = Annotated[str, Field(min_length=1, max_length=100)]
Semestre = Annotated[int, Field(ge=1, le=10)]
HorasEstudio = Annotated[int, Field(ge=0, le=40, description="Horas de estudio por semana")]
Asistencia = Annotated[int, Field(ge=0, le=100, description="Porcentaje de asistencia")]


class EstudianteCreate(BaseModel):
    nombre: str = Field(min_length=1, max_length=50)
    apellido: str = Field(min_length=1, max_length=50)
    edad: Edad
    carrera: Carrera
    semestre: Semestre
    promedio: Decimal = Field(ge=0, le=5, max_digits=3, decimal_places=2)
    ciudad: str = Field(min_length=1, max_length=100)
    horas_estudio: HorasEstudio
    asistencia: Asistencia

    model_config = ConfigDict(str_strip_whitespace=True)


class EstudianteOut(EstudianteCreate):
    id: int
    model_config = ConfigDict(from_attributes=True)


class EstadisticaOut(BaseModel):
    """Formato común de todas las estadísticas (fácil de consumir en Flutter)."""

    etiqueta: str
    valor: int | float


class PrediccionEntrada(BaseModel):
    """Datos de un estudiante para predecir su riesgo (sin promedio: es lo que se predice)."""

    horas_estudio: HorasEstudio
    asistencia: Asistencia
    semestre: Semestre
    edad: Edad
    carrera: Carrera

    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={"examples": [{
            "horas_estudio": 6, "asistencia": 70, "semestre": 2, "edad": 18, "carrera": "Medicina",
        }]},
    )


class PrediccionSalida(BaseModel):
    en_riesgo: bool
    probabilidad: float = Field(ge=0, le=1, description="Probabilidad de estar en riesgo (0-1)")
    modelo: str
