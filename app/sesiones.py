"""Sesiones: familias de refresh tokens guardadas en la tabla sesiones_refresh.

Un login abre una familia; cada refresh la hace avanzar (rotación). Ver
docs/CONTRATO_AUTH.md §5 para la regla de reintentos y la detección de reuso.
"""

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app import reloj, seguridad
from app.models import SesionRefresh, Usuario

log = logging.getLogger("app.auth")


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


class RefreshRechazado(Exception):
    """El refresh no sirve. `estado` y `codigo` son los del contrato (§4.3)."""

    def __init__(self, estado: int, codigo: str, mensaje: str):
        super().__init__(mensaje)
        self.estado, self.codigo, self.mensaje = estado, codigo, mensaje


def _buscar(db: Session, refresh: str) -> SesionRefresh | None:
    # FOR UPDATE: dos refresh simultáneos del mismo token se ponen en fila; el
    # segundo ve ya la rotación del primero y recibe el mismo sucesor.
    consulta = select(SesionRefresh).where(SesionRefresh.hash_token == seguridad.hash_refresh(refresh))
    return db.scalar(consulta.with_for_update())


def rotar(db: Session, refresh: str, user_agent: str | None) -> tuple[Usuario, Emitido]:
    """Cambia un refresh por su sucesor (docs/CONTRATO_AUTH.md §5).

    - Sin usar: se marca como usado y se emite el sucesor.
    - Ya usado hace <= 30 s y con el sucesor sin usar: reintento o petición
      concurrente; se devuelve el mismo sucesor (el sucesor es determinista).
    - Ya usado en otro caso: reuso, señal de robo. Se revoca la familia entera.

    Lanza RefreshRechazado; quien llama debe hacer commit también en ese caso
    para que la revocación por reuso quede guardada.
    """
    ahora = reloj.ahora()
    actual = _buscar(db, refresh)
    if actual is None:
        raise RefreshRechazado(401, "refresh_invalido", "La sesión no es válida; inicia sesión")
    if actual.revocado_en is not None:
        raise RefreshRechazado(401, "sesion_revocada", "La sesión se cerró; inicia sesión de nuevo")
    if ahora >= actual.expira_en:
        raise RefreshRechazado(401, "refresh_invalido", "La sesión caducó; inicia sesión")
    usuario = actual.usuario
    if not usuario.activo:
        raise RefreshRechazado(403, "cuenta_desactivada", "La cuenta está desactivada")

    sucesor = seguridad.sucesor_refresh(refresh)

    if actual.usado_en is None:
        actual.usado_en = ahora
        nueva = SesionRefresh(
            usuario_id=usuario.id,
            familia_id=actual.familia_id,
            hash_token=seguridad.hash_refresh(sucesor),
            creado_en=ahora,
            expira_en=min(ahora + seguridad.REFRESH_DURACION, actual.familia_expira_en),
            familia_expira_en=actual.familia_expira_en,
            user_agent=_recortar_user_agent(user_agent),
        )
        db.add(nueva)
        db.flush()
        return usuario, Emitido(sucesor, nueva)

    siguiente = _buscar(db, sucesor)
    es_reintento = (
        ahora - actual.usado_en <= seguridad.REFRESH_GRACIA
        and siguiente is not None
        and siguiente.usado_en is None
        and siguiente.revocado_en is None
    )
    if es_reintento:
        return usuario, Emitido(sucesor, siguiente)

    revocar_familia(db, actual.familia_id, "reuso")
    log.warning("reuso de refresh detectado: familia %s del usuario %s revocada",
                actual.familia_id, usuario.id)
    raise RefreshRechazado(401, "sesion_revocada", "La sesión se cerró por seguridad; inicia sesión de nuevo")


def revocar_familia(db: Session, familia_id: str, motivo: str) -> None:
    db.execute(
        update(SesionRefresh)
        .where(SesionRefresh.familia_id == familia_id, SesionRefresh.revocado_en.is_(None))
        .values(revocado_en=reloj.ahora(), motivo_revocacion=motivo)
    )


def cerrar(db: Session, refresh: str) -> None:
    """Logout: revoca la familia de ese refresh. Si no existe, no hace nada."""
    sesion = db.scalar(
        select(SesionRefresh).where(SesionRefresh.hash_token == seguridad.hash_refresh(refresh))
    )
    if sesion is not None:
        revocar_familia(db, sesion.familia_id, "logout")


def cerrar_todas(db: Session, usuario: Usuario, motivo: str) -> None:
    """Revoca todas las familias del usuario e invalida al instante sus access
    (los emitidos antes de ahora dejan de valer: ver dependencias.sesion_actual)."""
    ahora = reloj.ahora()
    db.execute(
        update(SesionRefresh)
        .where(SesionRefresh.usuario_id == usuario.id, SesionRefresh.revocado_en.is_(None))
        .values(revocado_en=ahora, motivo_revocacion=motivo)
    )
    usuario.sesiones_validas_desde = ahora


def abiertas(db: Session, usuario: Usuario) -> list[SesionRefresh]:
    """El último refresh (sin usar, sin revocar, vigente) de cada familia abierta,
    del más reciente al más antiguo."""
    return list(db.scalars(
        select(SesionRefresh)
        .where(
            SesionRefresh.usuario_id == usuario.id,
            SesionRefresh.usado_en.is_(None),
            SesionRefresh.revocado_en.is_(None),
            SesionRefresh.expira_en > reloj.ahora(),
        )
        .order_by(SesionRefresh.creado_en.desc(), SesionRefresh.id.desc())
    ))
