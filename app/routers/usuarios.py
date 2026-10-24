"""Gestión de usuarios: el docente ve a los jugadores y crea cuentas en lote;
el admin cambia roles, desactiva cuentas y restablece contraseñas (no hay
email para recuperarlas). Contrato: docs/CONTRATO_AUTH.md §4.9."""

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import logs, passkeys, schemas, seguridad, sesiones
from app.database import get_db
from app.dependencias import requiere_rol
from app.contrasenas import es_comun
from app.errores import error, password_comun, validacion_password_igual_al_alias
from app.models import Usuario
from app.routers.auth import registrar_acierto

router = APIRouter(prefix="/usuarios", tags=["Usuarios"])
log = logs.obtener("app.auth")


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
    antes = (usuario.rol, usuario.activo)
    if cambios.rol is not None:
        usuario.rol = cambios.rol  # efecto inmediato: el rol se lee de la BD en cada petición
    if cambios.activo is not None:
        usuario.activo = cambios.activo
        if not cambios.activo:
            sesiones.cerrar_todas(db, usuario, "desactivada")
    db.commit()
    db.refresh(usuario)
    # Registro de auditoría: quién cambió qué (sin datos sensibles).
    log.info("admin_modifica_usuario", admin_id=admin.id, usuario_id=usuario.id,
             rol_antes=antes[0], rol=usuario.rol, activo_antes=antes[1], activo=usuario.activo)
    return usuario


@router.put("/{usuario_id}/password", status_code=status.HTTP_204_NO_CONTENT)
def restablecer_password(usuario_id: int, datos: schemas.PasswordAdmin,
                         admin: Usuario = Depends(requiere_rol("admin")), db: Session = Depends(get_db)):
    """Pone una contraseña nueva, cierra todas las sesiones de ese usuario y
    borra sus passkeys (restablecer = solo la contraseña nueva da acceso)."""
    usuario = _objetivo(usuario_id, admin, db)
    if schemas.password_igual_al_alias(datos.password_nueva, usuario.alias):
        raise validacion_password_igual_al_alias("password_nueva")
    if es_comun(datos.password_nueva):
        raise password_comun("password_nueva")
    usuario.hash_password = seguridad.hashear_password(datos.password_nueva)
    registrar_acierto(usuario)  # quita un posible bloqueo por fuerza bruta
    sesiones.cerrar_todas(db, usuario, "password")
    passkeys_borradas = passkeys.borrar_de(db, usuario.id)
    db.commit()
    log.info("admin_restablece_password", admin_id=admin.id, usuario_id=usuario.id, passkeys_borradas=passkeys_borradas)


@router.post("/lote", response_model=schemas.LoteOut, status_code=status.HTTP_201_CREATED)
def crear_lote(datos: schemas.LoteEntrada, respuesta: Response,
               docente: Usuario = Depends(requiere_rol("docente", "admin")), db: Session = Depends(get_db)):
    """Crea cuentas de jugador con contraseñas generadas (todas o ninguna).

    Pensado para el aula: el docente reparte las contraseñas y los alumnos no
    tienen que registrarse (ni chocar con el límite de registros por IP).
    Cada alumno puede cambiarla después con PUT /auth/password.
    """
    def alias_en_uso(aliases: list[str]):
        return error(status.HTTP_409_CONFLICT, "alias_en_uso",
                     "Algunos alias ya están en uso; no se creó ninguna cuenta", aliases=sorted(aliases))

    # La collation compara sin distinguir mayúsculas, como el UNIQUE.
    existentes = db.scalars(select(Usuario.alias).where(Usuario.alias.in_(datos.aliases))).all()
    if existentes:
        raise alias_en_uso(list(existentes))

    creadas = []
    for alias in datos.aliases:
        password = seguridad.generar_password()
        usuario = Usuario(alias=alias, hash_password=seguridad.hashear_password(password))
        db.add(usuario)
        creadas.append((usuario, password))
    try:
        db.flush()
    except IntegrityError:  # otra petición creó alguno de esos alias a la vez
        db.rollback()
        raise alias_en_uso([])
    db.commit()

    respuesta.headers["Cache-Control"] = "no-store"  # lleva contraseñas
    log.info("alta_en_lote", cuentas=len(creadas), docente_id=docente.id)
    return schemas.LoteOut(cuentas=[
        schemas.CuentaCreada(id=u.id, alias=u.alias, password=p) for u, p in creadas
    ])
