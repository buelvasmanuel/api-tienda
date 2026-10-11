import json
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Numeric, String, Text, TypeDecorator
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app import reloj
from app.database import Base

# DATETIME con microsegundos: un DATETIME normal redondea a segundos, y las
# comparaciones de sesiones (p. ej. un token emitido justo después de cerrar
# todas las sesiones) necesitan más precisión. Siempre en UTC (ver app/reloj.py).
FechaHora = DATETIME(fsp=6)

ROLES = ("jugador", "docente", "admin")
MOTIVOS_REVOCACION = ("logout", "logout_todo", "reuso", "password", "desactivada")


def _en(valores: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in valores)


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


class Usuario(Base):
    __tablename__ = "usuarios"
    __table_args__ = (
        CheckConstraint("alias REGEXP '^[A-Za-z0-9_]{3,30}$'", name="alias"),
        CheckConstraint(f"rol IN ({_en(ROLES)})", name="rol"),
        CheckConstraint("intentos_fallidos >= 0", name="intentos_fallidos"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Único sin distinguir mayúsculas: lo hace la collation utf8mb4_unicode_ci.
    alias: Mapped[str] = mapped_column(String(30), unique=True)
    hash_password: Mapped[str] = mapped_column(String(255))  # formato $argon2id$...
    rol: Mapped[str] = mapped_column(String(10), default="jugador", server_default="jugador")
    activo: Mapped[bool] = mapped_column(default=True, server_default="1")
    # Los access emitidos antes de esta fecha dejan de valer ("cerrar todas las
    # sesiones", cambio de contraseña). NULL = ninguno invalidado.
    sesiones_validas_desde: Mapped[datetime | None] = mapped_column(FechaHora)
    # Bloqueo por fuerza bruta (ver app/seguridad.py).
    intentos_fallidos: Mapped[int] = mapped_column(default=0, server_default="0")
    bloqueado_hasta: Mapped[datetime | None] = mapped_column(FechaHora)
    creado_en: Mapped[datetime] = mapped_column(FechaHora, default=lambda: reloj.ahora())
    actualizado_en: Mapped[datetime] = mapped_column(
        FechaHora, default=lambda: reloj.ahora(), onupdate=lambda: reloj.ahora()
    )

    sesiones: Mapped[list["SesionRefresh"]] = relationship(
        back_populates="usuario", passive_deletes=True
    )


class SesionRefresh(Base):
    """Un refresh token emitido. Los de un mismo login forman una familia.

    No se guarda el token, solo su SHA-256. El sucesor de un token no necesita
    columna: se deriva del propio token (ver app/seguridad.py), y con él su hash.
    """

    __tablename__ = "sesiones_refresh"
    __table_args__ = (
        CheckConstraint("expira_en <= familia_expira_en", name="expira_en"),
        CheckConstraint(
            f"motivo_revocacion IS NULL OR motivo_revocacion IN ({_en(MOTIVOS_REVOCACION)})",
            name="motivo_revocacion",
        ),
        # Revocado y motivo van juntos: o los dos o ninguno.
        CheckConstraint(
            "(revocado_en IS NULL) = (motivo_revocacion IS NULL)", name="revocacion"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), index=True
    )
    familia_id: Mapped[str] = mapped_column(String(32), index=True)  # uuid4 en hex
    hash_token: Mapped[str] = mapped_column(String(64), unique=True)  # SHA-256 en hex
    creado_en: Mapped[datetime] = mapped_column(FechaHora, default=lambda: reloj.ahora())
    expira_en: Mapped[datetime] = mapped_column(FechaHora)
    familia_expira_en: Mapped[datetime] = mapped_column(FechaHora)
    usado_en: Mapped[datetime | None] = mapped_column(FechaHora)  # cuándo se rotó
    revocado_en: Mapped[datetime | None] = mapped_column(FechaHora)
    motivo_revocacion: Mapped[str | None] = mapped_column(String(20))
    user_agent: Mapped[str | None] = mapped_column(String(200))

    usuario: Mapped[Usuario] = relationship(back_populates="sesiones")


# ---------- Juego: intentos de casos, rescates e insignias (docs/CONTRATO_JUEGO.md) ----------
# No hay tabla de "progreso": XP, nivel, racha e insignias salen de estas filas,
# así un total guardado nunca puede desincronizarse de lo que pasó.

class IntentoCaso(Base):
    """Cada respuesta a un caso del Modo 2, con la XP que dio (0 casi siempre)."""

    __tablename__ = "intentos_caso"
    __table_args__ = (
        CheckConstraint("xp >= 0", name="xp"),
        Index("ix_intentos_caso_usuario_id_caso", "usuario_id", "caso"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"))
    caso: Mapped[str] = mapped_column(String(40))  # código del catálogo (app/casos.py)
    respuesta: Mapped[str] = mapped_column(String(100))
    correcto: Mapped[bool]
    xp: Mapped[int] = mapped_column(default=0)
    creado_en: Mapped[datetime] = mapped_column(FechaHora, default=lambda: reloj.ahora())


class Rescate(Base):
    """Cada intento del Laboratorio, con una copia de los datos de partida: si
    el estudiante se edita o se borra, el historial no cambia."""

    __tablename__ = "rescates"
    __table_args__ = (
        CheckConstraint("estrellas BETWEEN 0 AND 3", name="estrellas"),
        CheckConstraint("fichas BETWEEN 0 AND 5", name="fichas"),
        CheckConstraint("estudio >= 0 AND tutoria >= 0", name="intervenciones"),
        CheckConstraint("xp >= 0", name="xp"),
        CheckConstraint("probabilidad_inicial BETWEEN 0 AND 1 AND probabilidad_final BETWEEN 0 AND 1",
                        name="probabilidades"),
        Index("ix_rescates_usuario_id_estudiante_id", "usuario_id", "estudiante_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"))
    estudiante_id: Mapped[int | None] = mapped_column(ForeignKey("estudiantes.id", ondelete="SET NULL"))
    horas_estudio_inicial: Mapped[int]
    asistencia_inicial: Mapped[int]
    estudio: Mapped[int]
    tutoria: Mapped[int]
    fichas: Mapped[int]
    probabilidad_inicial: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    probabilidad_final: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    logrado: Mapped[bool]
    estrellas: Mapped[int]
    xp: Mapped[int] = mapped_column(default=0)
    version_modelo: Mapped[str] = mapped_column(String(20))
    creado_en: Mapped[datetime] = mapped_column(FechaHora, default=lambda: reloj.ahora())


class InsigniaObtenida(Base):
    """Insignias ganadas. El catálogo (nombres, condiciones) está en app/progreso.py."""

    __tablename__ = "usuarios_insignias"

    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"), primary_key=True)
    codigo: Mapped[str] = mapped_column(String(30), primary_key=True)
    obtenida_en: Mapped[datetime] = mapped_column(FechaHora, default=lambda: reloj.ahora())


# ---------- Duelo de cartas (docs/CONTRATO_DUELO.md) ----------

class JSONTexto(TypeDecorator):
    """JSON guardado como TEXT. El tipo JSON nativo se comporta distinto en
    MariaDB (un alias de LONGTEXT) y en MySQL, y Alembic los confunde al
    comparar el esquema; un TEXT con json.dumps/loads es igual en los dos."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, valor, dialect):
        return None if valor is None else json.dumps(valor, ensure_ascii=False)

    def process_result_value(self, valor, dialect):
        return None if valor is None else json.loads(valor)


MODOS_DUELO = ("maquina", "jugador")
ESTADOS_DUELO = ("esperando", "en_juego", "terminado", "abandonado")
RESULTADOS = ("victoria", "derrota", "empate")


class Duelo(Base):
    """Un duelo entero en una fila: los mazos repartidos (copia de las cartas)
    y las rondas resueltas. Las jugadas bloquean la fila (FOR UPDATE).
    El lado "a" es quien creó el duelo; el "b", el rival (NULL = la máquina)."""

    __tablename__ = "duelos"
    __table_args__ = (
        CheckConstraint(f"modo IN ({_en(MODOS_DUELO)})", name="modo"),
        CheckConstraint(f"estado IN ({_en(ESTADOS_DUELO)})", name="estado"),
        CheckConstraint(f"resultado_a IS NULL OR resultado_a IN ({_en(RESULTADOS)})", name="resultado_a"),
        CheckConstraint("puntos_a BETWEEN 0 AND 5 AND puntos_b BETWEEN 0 AND 5", name="puntos"),
        CheckConstraint("xp_a >= 0 AND xp_b >= 0", name="xp"),
        CheckConstraint("ronda IS NULL OR ronda BETWEEN 1 AND 5", name="ronda"),
        Index("ix_duelos_jugador_a_id_estado", "jugador_a_id", "estado"),
        Index("ix_duelos_jugador_b_id_estado", "jugador_b_id", "estado"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # uuid4 en hex: no se puede adivinar
    modo: Mapped[str] = mapped_column(String(10))
    estado: Mapped[str] = mapped_column(String(12))
    jugador_a_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"))
    jugador_b_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"))
    mazo_a: Mapped[list] = mapped_column(JSONTexto)  # lista de cartas (CartaOut)
    mazo_b: Mapped[list] = mapped_column(JSONTexto)
    rondas: Mapped[list] = mapped_column(JSONTexto)  # rondas resueltas: numero, elegido_por, atributo, ganador, r
    ronda: Mapped[int | None]  # la que toca jugar; NULL al terminar
    puntos_a: Mapped[int] = mapped_column(default=0)
    puntos_b: Mapped[int] = mapped_column(default=0)
    resultado_a: Mapped[str | None] = mapped_column(String(10))  # el del lado b es el contrario
    xp_a: Mapped[int] = mapped_column(default=0)
    xp_b: Mapped[int] = mapped_column(default=0)
    expira_turno_en: Mapped[datetime | None] = mapped_column(FechaHora)  # v2 (jugador contra jugador)
    creado_en: Mapped[datetime] = mapped_column(FechaHora, default=lambda: reloj.ahora())
    actualizado_en: Mapped[datetime] = mapped_column(
        FechaHora, default=lambda: reloj.ahora(), onupdate=lambda: reloj.ahora()
    )
    terminado_en: Mapped[datetime | None] = mapped_column(FechaHora)
