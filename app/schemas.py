from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, StringConstraints, model_validator


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


# ---------- Autenticación (docs/CONTRATO_AUTH.md §3) ----------

Alias = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_]{3,30}$")]
# Sin reglas de composición (NIST 800-63B): lo que protege es la longitud.
# No se recortan espacios: forman parte de la contraseña.
Password = Annotated[str, Field(min_length=10, max_length=128)]
Rol = Literal["jugador", "docente", "admin"]
# Las fechas se guardan en UTC sin zona; al cliente se le envían con "Z".
FechaUTC = Annotated[datetime, PlainSerializer(lambda d: d.isoformat(timespec="microseconds") + "Z")]


class Entrada(BaseModel):
    """Base de los cuerpos de /auth y /usuarios: campos desconocidos → 422
    (nadie puede colar, p. ej., "rol": "admin" en el registro)."""

    model_config = ConfigDict(extra="forbid")


class RegistroEntrada(Entrada):
    alias: Alias
    password: Password

    @model_validator(mode="after")
    def _password_distinta_del_alias(self) -> Self:
        if self.password.casefold() == self.alias.casefold():
            raise ValueError("La contraseña no puede ser igual al alias")
        return self


class LoginEntrada(Entrada):
    # Sin el patrón del alias: un alias imposible simplemente no existe (401).
    alias: str = Field(max_length=100)
    password: str = Field(max_length=128)


class UsuarioOut(BaseModel):
    id: int
    alias: str
    rol: Rol
    model_config = ConfigDict(from_attributes=True)


class TokensOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Segundos de vida del access token")
    refresh_token: str
    refresh_expires_in: int = Field(description="Segundos hasta que caduca este refresh token")
    usuario: UsuarioOut
