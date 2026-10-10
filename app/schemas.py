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
