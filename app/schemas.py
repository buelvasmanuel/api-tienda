from decimal import Decimal

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


class EstudianteCreate(BaseModel):
    nombre: str = Field(min_length=1, max_length=50)
    apellido: str = Field(min_length=1, max_length=50)
    edad: int = Field(ge=16, le=35)
    carrera: str = Field(min_length=1, max_length=100)
    semestre: int = Field(ge=1, le=10)
    promedio: Decimal = Field(ge=0, le=5, max_digits=3, decimal_places=2)
    ciudad: str = Field(min_length=1, max_length=100)
    horas_estudio: int = Field(ge=0, le=40, description="Horas de estudio por semana")
    asistencia: int = Field(ge=0, le=100, description="Porcentaje de asistencia")

    model_config = ConfigDict(str_strip_whitespace=True)


class EstudianteOut(EstudianteCreate):
    id: int
    model_config = ConfigDict(from_attributes=True)


class EstadisticaOut(BaseModel):
    """Formato común de todas las estadísticas (fácil de consumir en Flutter)."""

    etiqueta: str
    valor: int | float
