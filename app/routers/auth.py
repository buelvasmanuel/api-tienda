"""Registro, login y sesión del usuario. Contrato: docs/CONTRATO_AUTH.md."""

import logging
import math
from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import reloj, schemas, seguridad, sesiones
from app.database import get_db
from app.dependencias import usuario_actual
from app.errores import demasiados_intentos, error, no_autorizado
from app.models import Usuario

log = logging.getLogger("app.auth")


def _sin_cache(respuesta: Response) -> None:
    # Las respuestas llevan tokens: que ningún proxy ni navegador las guarde.
    respuesta.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/auth", tags=["Autenticación"], dependencies=[Depends(_sin_cache)])

# Bloqueo por cuenta: a partir de FALLOS_PARA_BLOQUEAR fallos seguidos, 30 s, y
# cada fallo posterior lo duplica hasta un máximo de 15 min.
FALLOS_PARA_BLOQUEAR = 5
BLOQUEO_INICIAL = timedelta(seconds=30)
BLOQUEO_MAXIMO = timedelta(minutes=15)


def duracion_bloqueo(fallos: int) -> timedelta | None:
    """Cuánto se bloquea la cuenta tras `fallos` fallos seguidos (None = nada)."""
    if fallos < FALLOS_PARA_BLOQUEAR:
        return None
    # El exponente se acota: con cientos de fallos, 2**n desbordaría timedelta,
    # y a partir de 2**5 ya se supera el máximo.
    exponente = min(fallos - FALLOS_PARA_BLOQUEAR, 10)
    return min(BLOQUEO_INICIAL * 2**exponente, BLOQUEO_MAXIMO)


def tokens(usuario: Usuario, emitido: sesiones.Emitido) -> schemas.TokensOut:
    # Desde la emisión, no desde "ahora": así no se pierde un segundo al truncar.
    restante = emitido.sesion.expira_en - emitido.sesion.creado_en
    return schemas.TokensOut(
        access_token=seguridad.crear_access(usuario.id, emitido.sesion.familia_id),
        expires_in=int(seguridad.ACCESS_DURACION.total_seconds()),
        refresh_token=emitido.refresh,
        refresh_expires_in=int(restante.total_seconds()),
        usuario=schemas.UsuarioOut.model_validate(usuario),
    )


def _alias_en_uso():
    return error(status.HTTP_409_CONFLICT, "alias_en_uso", "Ese alias ya está en uso")


@router.post("/registro", response_model=schemas.TokensOut, status_code=status.HTTP_201_CREATED)
def registro(datos: schemas.RegistroEntrada, request: Request, db: Session = Depends(get_db)):
    """Crea una cuenta de jugador y abre su primera sesión."""
    # Se comprueba antes de calcular el hash para no gastar CPU en balde.
    if db.scalar(select(Usuario.id).where(Usuario.alias == datos.alias)) is not None:
        raise _alias_en_uso()

    usuario = Usuario(alias=datos.alias, hash_password=seguridad.hashear_password(datos.password))
    db.add(usuario)
    try:
        db.flush()
    except IntegrityError:  # otra petición registró el mismo alias a la vez
        db.rollback()
        raise _alias_en_uso()

    emitido = sesiones.abrir(db, usuario, request.headers.get("user-agent"))
    db.commit()
    log.info("registro alias=%s id=%s", usuario.alias, usuario.id)
    return tokens(usuario, emitido)


@router.post("/login", response_model=schemas.TokensOut)
def login(datos: schemas.LoginEntrada, request: Request, db: Session = Depends(get_db)):
    """Abre una sesión nueva. Mismo error para alias inexistente y contraseña mala."""
    credenciales_invalidas = no_autorizado("credenciales_invalidas", "Alias o contraseña incorrectos")
    # FOR UPDATE: dos intentos simultáneos contra la misma cuenta se ponen en
    # fila, así el contador de fallos no se puede esquivar en paralelo.
    usuario = db.scalar(
        select(Usuario).where(Usuario.alias == datos.alias).with_for_update()
    )
    if usuario is None:
        seguridad.gastar_tiempo_de_verificacion(datos.password)
        raise credenciales_invalidas

    ahora = reloj.ahora()
    if usuario.bloqueado_hasta and ahora < usuario.bloqueado_hasta:
        espera = math.ceil((usuario.bloqueado_hasta - ahora).total_seconds())
        db.rollback()
        raise demasiados_intentos(espera)

    if not seguridad.verificar_password(usuario.hash_password, datos.password):
        usuario.intentos_fallidos += 1
        bloqueo = duracion_bloqueo(usuario.intentos_fallidos)
        if bloqueo:
            usuario.bloqueado_hasta = ahora + bloqueo
            log.warning("cuenta bloqueada alias=%s fallos=%s durante=%ss",
                        usuario.alias, usuario.intentos_fallidos, int(bloqueo.total_seconds()))
        db.commit()
        raise credenciales_invalidas

    usuario.intentos_fallidos = 0
    usuario.bloqueado_hasta = None
    if not usuario.activo:
        db.commit()
        raise error(status.HTTP_403_FORBIDDEN, "cuenta_desactivada", "La cuenta está desactivada")
    if seguridad.necesita_rehash(usuario.hash_password):
        usuario.hash_password = seguridad.hashear_password(datos.password)

    emitido = sesiones.abrir(db, usuario, request.headers.get("user-agent"))
    db.commit()
    return tokens(usuario, emitido)


@router.get("/yo", response_model=schemas.UsuarioOut)
def yo(usuario: Usuario = Depends(usuario_actual)):
    return usuario
