"""Gestión de usuarios: el docente ve a los jugadores; el admin cambia roles,
desactiva cuentas y restablece contraseñas (no hay email para recuperarlas).
Contrato: docs/CONTRATO_AUTH.md §4.9."""

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import schemas, seguridad, sesiones
from app.database import get_db
from app.dependencias import requiere_rol
from app.errores import error, validacion_password_igual_al_alias
from app.models import Usuario
from app.routers.auth import registrar_acierto

router = APIRouter(prefix="/usuarios", tags=["Usuarios"])


def _objetivo(usuario_id: int, admin: Usuario, db: Session) -> Usuario:
    """El usuario a modificar: existe y no es el propio admin.

    Un admin no se cambia a sí mismo por aquí: así no puede quitarse el rol
    por error (y quedarse sin admins) y, con un token de admin robado, no se
    puede cambiar su contraseña sin conocer la actual (eso es /auth/password).
    """
    if usuario_id == admin.id:
        raise error(status.HTTP_409_CONFLICT, "no_puedes_modificarte",
                    "Usa /auth/password para tu propia cuenta; no puedes cambiar tu rol ni desactivarte")
    usuario = db.get(Usuario, usuario_id)
    if usuario is None:
        raise error(status.HTTP_404_NOT_FOUND, "usuario_no_encontrado", "Usuario no encontrado")
    return usuario


@router.get("", response_model=list[schemas.UsuarioAdminOut])
def listar(_: Usuario = Depends(requiere_rol("docente", "admin")), db: Session = Depends(get_db)):
    return db.scalars(select(Usuario).order_by(Usuario.id)).all()


@router.patch("/{usuario_id}", response_model=schemas.UsuarioAdminOut)
def modificar(usuario_id: int, cambios: schemas.UsuarioCambios,
              admin: Usuario = Depends(requiere_rol("admin")), db: Session = Depends(get_db)):
    usuario = _objetivo(usuario_id, admin, db)
    if cambios.rol is not None:
        usuario.rol = cambios.rol  # efecto inmediato: el rol se lee de la BD en cada petición
    if cambios.activo is not None:
        usuario.activo = cambios.activo
        if not cambios.activo:
            sesiones.cerrar_todas(db, usuario, "desactivada")
    db.commit()
    db.refresh(usuario)
    return usuario


@router.put("/{usuario_id}/password", status_code=status.HTTP_204_NO_CONTENT)
def restablecer_password(usuario_id: int, datos: schemas.PasswordAdmin,
                         admin: Usuario = Depends(requiere_rol("admin")), db: Session = Depends(get_db)):
    """Pone una contraseña nueva y cierra todas las sesiones de ese usuario."""
    usuario = _objetivo(usuario_id, admin, db)
    if schemas.password_igual_al_alias(datos.password_nueva, usuario.alias):
        raise validacion_password_igual_al_alias("password_nueva")
    usuario.hash_password = seguridad.hashear_password(datos.password_nueva)
    registrar_acierto(usuario)  # quita un posible bloqueo por fuerza bruta
    sesiones.cerrar_todas(db, usuario, "password")
    db.commit()
