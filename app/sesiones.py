"""Sesiones: familias de refresh tokens guardadas en la tabla sesiones_refresh.

Un login abre una familia; cada refresh la hace avanzar (rotación). Ver
docs/CONTRATO_AUTH.md §5 para la regla de reintentos y la detección de reuso.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app import reloj, seguridad
from app.models import SesionRefresh, Usuario


@dataclass(frozen=True)
class Emitido:
    """Un refresh recién emitido (en claro, solo existe en esta respuesta) y su fila."""

    refresh: str
    sesion: SesionRefresh


def _recortar_user_agent(user_agent: str | None) -> str | None:
    return user_agent[:200] if user_agent else None


def limpiar_caducadas(db: Session) -> None:
    """Borra las familias que ya pasaron su máximo absoluto de 30 días.

    Render free no tiene tareas programadas: se llama al abrir cada sesión, así
    la tabla no crece sin control (Aiven free tiene 1 GB).
    """
    db.execute(delete(SesionRefresh).where(SesionRefresh.familia_expira_en < reloj.ahora()))


def abrir(db: Session, usuario: Usuario, user_agent: str | None) -> Emitido:
    """Nueva familia con su primer refresh (login, registro, cambio de contraseña)."""
    limpiar_caducadas(db)
    ahora = reloj.ahora()
    refresh = seguridad.nuevo_refresh()
    sesion = SesionRefresh(
        usuario_id=usuario.id,
        familia_id=uuid.uuid4().hex,
        hash_token=seguridad.hash_refresh(refresh),
        creado_en=ahora,
        expira_en=ahora + seguridad.REFRESH_DURACION,
        familia_expira_en=ahora + seguridad.FAMILIA_DURACION,
        user_agent=_recortar_user_agent(user_agent),
    )
    db.add(sesion)
    db.flush()
    return Emitido(refresh, sesion)
