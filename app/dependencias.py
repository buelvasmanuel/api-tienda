"""Dependencias de autenticación y autorización para los routers.

En cada petición protegida se descifra el access token y se carga el usuario
de la base: así el rol, la desactivación y "cerrar todas las sesiones" se
aplican al instante, sin esperar a que caduque el token.
"""

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app import seguridad
from app.database import get_db
from app.errores import error, no_autorizado
from app.models import Usuario

# auto_error=False: sin cabecera (o con otro esquema) devuelve None y el error
# lo damos nosotros, con el formato del contrato.
_bearer = HTTPBearer(auto_error=False, description="Access token de /auth/login")


@dataclass(frozen=True)
class SesionActual:
    usuario: Usuario
    familia_id: str


def sesion_actual(
    credenciales: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> SesionActual:
    if credenciales is None:
        raise no_autorizado("no_autenticado", "Falta el token de acceso")
    try:
        access = seguridad.leer_access(credenciales.credentials)
    except seguridad.TokenInvalido as e:
        raise no_autorizado(e.codigo, e.mensaje)

    usuario = db.get(Usuario, access.usuario_id)
    if usuario is None:
        raise no_autorizado("sesion_cerrada", "La sesión ya no es válida")
    if not usuario.activo:
        raise error(403, "cuenta_desactivada", "La cuenta está desactivada")
    if usuario.sesiones_validas_desde and access.emitido_en < usuario.sesiones_validas_desde:
        raise no_autorizado("sesion_cerrada", "La sesión se cerró; vuelve a iniciar sesión")
    return SesionActual(usuario, access.familia_id)


def usuario_actual(sesion: SesionActual = Depends(sesion_actual)) -> Usuario:
    return sesion.usuario


def requiere_rol(*roles: str) -> Callable[..., Usuario]:
    """Dependencia que deja pasar solo a usuarios con alguno de esos roles."""

    def comprobar(usuario: Usuario = Depends(usuario_actual)) -> Usuario:
        if usuario.rol not in roles:
            raise error(403, "permiso_denegado", "No tienes permiso para esta acción")
        return usuario

    return comprobar
