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


def password_igual_al_alias(password: str, alias: str) -> bool:
    return password.casefold() == alias.casefold()


class Entrada(BaseModel):
    """Base de los cuerpos de /auth y /usuarios: campos desconocidos → 422
    (nadie puede colar, p. ej., "rol": "admin" en el registro)."""

    model_config = ConfigDict(extra="forbid")


class RegistroEntrada(Entrada):
    alias: Alias
    password: Password

    @model_validator(mode="after")
    def _password_distinta_del_alias(self) -> Self:
        if password_igual_al_alias(self.password, self.alias):
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


class RefreshEntrada(Entrada):
    # Holgado a propósito: cualquier texto que no sea un refresh nuestro (p. ej.
    # un access token) debe dar 401 refresh_invalido, no 422.
    refresh_token: str = Field(max_length=512)


class PasswordCambio(Entrada):
    password_actual: str = Field(max_length=128)
    password_nueva: Password


class SesionOut(BaseModel):
    id: str = Field(description="Identificador de la sesión (familia de refresh tokens)")
    iniciada_en: FechaUTC
    ultimo_uso: FechaUTC
    dispositivo: str | None = Field(description="User-Agent del último refresh o del login")
    actual: bool = Field(description="True si es la sesión del access token de esta petición")


class UsuarioAdminOut(UsuarioOut):
    activo: bool
    creado_en: FechaUTC


class UsuarioCambios(Entrada):
    rol: Rol | None = None
    activo: bool | None = None


class PasswordAdmin(Entrada):
    password_nueva: Password


class LoteEntrada(Entrada):
    aliases: list[Alias] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def _sin_repetidos(self) -> Self:
        vistos: set[str] = set()
        repetidos = []
        for alias in self.aliases:
            if alias.casefold() in vistos:
                repetidos.append(alias)
            vistos.add(alias.casefold())
        if repetidos:
            raise ValueError(f"Alias repetidos en la lista: {', '.join(repetidos)}")
        return self


class CuentaCreada(BaseModel):
    id: int
    alias: str
    password: str = Field(description="Contraseña inicial. Solo se muestra en esta respuesta")


class LoteOut(BaseModel):
    cuentas: list[CuentaCreada]


# ---------- Modelo del juego y Laboratorio (docs/CONTRATO_LABORATORIO.md) ----------

Probabilidad = Annotated[float, Field(ge=0, le=1, description="Riesgo de que el promedio sea < 3.0")]


class ModeloOut(BaseModel):
    nombre: str
    version: str


class BaseOut(BaseModel):
    probabilidad: Probabilidad
    logit: float


class AporteOut(BaseModel):
    variable: Literal["horas_estudio", "asistencia", "semestre", "edad", "carrera"]
    valor: int | str
    aporte: float = Field(description="En log-odds; positivo sube el riesgo")
    peso: float = Field(ge=0, le=1, description="|aporte| / Σ|aporte|")
    efecto: Literal["sube", "baja", "neutro"]


class ExplicacionOut(BaseModel):
    probabilidad: Probabilidad
    en_riesgo: bool
    base: BaseOut = Field(description="Riesgo del estudiante medio de los datos de entrenamiento")
    logit: float
    aportes: list[AporteOut]
    modelo: ModeloOut
    aviso: str


class LoteRiesgoEntrada(Entrada):
    entradas: list[PrediccionEntrada] = Field(min_length=1, max_length=50)


class RiesgoOut(BaseModel):
    probabilidad: Probabilidad
    en_riesgo: bool


class LoteRiesgoOut(BaseModel):
    resultados: list[RiesgoOut]
    modelo: ModeloOut
    aviso: str


class CasoResumenOut(BaseModel):
    estudiante_id: int
    nombre: str
    apellido: str
    carrera: str
    semestre: int
    probabilidad: Probabilidad


class CasosOut(BaseModel):
    casos: list[CasoResumenOut]
    presupuesto: int
    objetivo: float


class EstudianteCasoOut(BaseModel):
    """Sin promedio: el modelo no lo usa y no cambia con las intervenciones."""

    id: int
    nombre: str
    apellido: str
    edad: int
    carrera: str
    semestre: int
    horas_estudio: int
    asistencia: int
    model_config = ConfigDict(from_attributes=True)


class IntervencionOut(BaseModel):
    codigo: Literal["estudio", "tutoria"]
    nombre: str
    fichas: int
    variable: str
    incremento: int
    tope: int
    efecto: str


class CasoOut(BaseModel):
    estudiante: EstudianteCasoOut
    probabilidad: Probabilidad
    presupuesto: int
    objetivo: float
    intervenciones: list[IntervencionOut]
    explicacion: ExplicacionOut
    aviso: str


class ResolverEntrada(Entrada):
    intervenciones: list[Literal["estudio", "tutoria"]] = Field(max_length=5)


class EstadoFinalOut(BaseModel):
    horas_estudio: int
    asistencia: int


class SolucionOut(BaseModel):
    estudio: int
    tutoria: int
    fichas: int
    probabilidad: Probabilidad


class ResultadoRescateOut(BaseModel):
    logrado: bool
    estrellas: int = Field(ge=0, le=3)
    fichas_usadas: int
    fichas_optimas: int
    probabilidad_inicial: Probabilidad
    probabilidad_final: Probabilidad
    estado_final: EstadoFinalOut
    solucion_optima: SolucionOut
    explicacion_final: ExplicacionOut
    aviso: str
    # v1.1 (Fase 5): progreso del jugador.
    xp_ganada: int
    record_estrellas: int = Field(ge=0, le=3)
    insignias_nuevas: list["InsigniaOut"]
    progreso: "ProgresoResumenOut"


# ---------- Juego: casos, progreso, insignias y ranking (docs/CONTRATO_JUEGO.md) ----------

Nivel = Literal["novato", "investigador", "experto"]


class InsigniaOut(BaseModel):
    codigo: str
    nombre: str
    descripcion: str
    obtenida_en: FechaUTC | None


class ProgresoResumenOut(BaseModel):
    xp: int
    nivel: str
    subio_de_nivel: bool


class CasoResumenJuegoOut(BaseModel):
    codigo: str
    nivel: Nivel
    titulo: str
    xp: int
    resuelto: bool
    intentos: int
    xp_ganada: int


class CasosJuegoOut(BaseModel):
    casos: list[CasoResumenJuegoOut]


class OpcionOut(BaseModel):
    valor: str
    texto: str


class CasoJuegoOut(BaseModel):
    codigo: str
    nivel: Nivel
    titulo: str
    enunciado: str
    opciones: list[OpcionOut]
    grafica: str | None
    xp: int
    resuelto: bool
    intentos: int


class RespuestaEntrada(Entrada):
    respuesta: str = Field(max_length=100)


class RespuestaOut(BaseModel):
    correcto: bool
    xp_ganada: int
    dato: str | None
    explicacion: str | None
    pista: str | None
    intento: int
    insignias_nuevas: list[InsigniaOut]
    progreso: ProgresoResumenOut


class NivelOut(BaseModel):
    numero: int
    nombre: str
    xp_desde: int


class ProgresoOut(BaseModel):
    alias: str
    xp: int
    nivel: NivelOut
    siguiente_nivel: NivelOut | None
    racha_dias: int
    casos_resueltos: int
    casos_total: int
    rescates_logrados: int
    estrellas_totales: int
    insignias: list[InsigniaOut]


class PuestoOut(BaseModel):
    posicion: int
    alias: str
    xp: int
    nivel: str
    soy_yo: bool


class MiPuestoOut(BaseModel):
    posicion: int
    xp: int


class RankingOut(BaseModel):
    periodo: Literal["semana", "siempre"]
    desde: FechaUTC | None
    jugadores: list[PuestoOut]
    yo: MiPuestoOut | None


class JugadorOut(BaseModel):
    id: int
    alias: str
    activo: bool
    xp: int
    nivel: str
    casos_resueltos: int
    rescates_logrados: int
    insignias: int
    ultima_actividad: FechaUTC | None


# ResultadoRescateOut (Laboratorio) usa InsigniaOut y ProgresoResumenOut, definidos después.
ResultadoRescateOut.model_rebuild()
