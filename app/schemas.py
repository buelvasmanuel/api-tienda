from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ProductoCreate(BaseModel):
    nombre: str = Field(min_length=1, max_length=100)
    precio: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    stock: int = Field(ge=0, default=0)


class ProductoOut(ProductoCreate):
    id: int
    model_config = ConfigDict(from_attributes=True)
