from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Categoria(Base):
    __tablename__ = "categorias"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(50), unique=True)

    # passive_deletes: al borrar la categoría, MariaDB pone categoria_id a NULL
    # (ON DELETE SET NULL) sin que SQLAlchemy tenga que cargar los productos.
    productos: Mapped[list["Producto"]] = relationship(
        back_populates="categoria", passive_deletes=True
    )


class Producto(Base):
    __tablename__ = "productos"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(100))
    precio: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    stock: Mapped[int] = mapped_column(default=0)
    categoria_id: Mapped[int | None] = mapped_column(
        ForeignKey("categorias.id", ondelete="SET NULL")
    )

    categoria: Mapped[Categoria | None] = relationship(back_populates="productos")
