from decimal import Decimal

from sqlalchemy import CheckConstraint, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Estudiante(Base):
    __tablename__ = "estudiantes"
    # CHECK: la base rechaza valores fuera de rango aunque no pasen por la API.
    __table_args__ = (
        CheckConstraint("edad BETWEEN 16 AND 35", name="edad"),
        CheckConstraint("semestre BETWEEN 1 AND 10", name="semestre"),
        CheckConstraint("promedio BETWEEN 0 AND 5", name="promedio"),
        CheckConstraint("horas_estudio BETWEEN 0 AND 40", name="horas_estudio"),
        CheckConstraint("asistencia BETWEEN 0 AND 100", name="asistencia"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(50))
    apellido: Mapped[str] = mapped_column(String(50))
    edad: Mapped[int]
    carrera: Mapped[str] = mapped_column(String(100))
    semestre: Mapped[int]
    promedio: Mapped[Decimal] = mapped_column(Numeric(3, 2))  # escala 0.00 - 5.00
    ciudad: Mapped[str] = mapped_column(String(100))
    horas_estudio: Mapped[int]  # horas por semana
    asistencia: Mapped[int]  # porcentaje
