"""Registro, login y sesión del usuario. Contrato: docs/CONTRATO_AUTH.md."""

import math
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import cookie_refresh, limites, logs, reloj, schemas, seguridad, sesiones
from app.config import settings
from app.database import get_db
from app.cookie_refresh import modo_cookie
from app.dependencias import SesionActual, sesion_actual, usuario_actual
from app.errores import demasiados_intentos, error, no_autorizado, validacion_password_igual_al_alias
from app.models import Usuario

log = logs.obtener("app.auth")


def _sin_cache(respuesta: Response) -> None:
    # Las respuestas llevan tokens: que ningún proxy ni navegador las guarde.
    respuesta.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/auth", tags=["Autenticación"], dependencies=[Depends(_sin_cache)])

# Bloqueo por cuenta: a partir de FALLOS_PARA_BLOQUEAR fallos seguidos, 30 s, y
# después 1 min como máximo. Es corto a propósito (auditoría A1, ver
# docs/ARQUITECTURA.md §12): un bloqueo largo deja a cualquiera bloquear la
# cuenta de otro (los alias se ven en el ranking). Frena la fuerza bruta a ~1
# intento por minuto y cuenta; el límite de fallos por IP hace el resto.
FALLOS_PARA_BLOQUEAR = 5
BLOQUEO_INICIAL = timedelta(seconds=30)
BLOQUEO_MAXIMO = timedelta(minutes=1)


def duracion_bloqueo(fallos: int) -> timedelta | None:
    """Cuánto se bloquea la cuenta tras `fallos` fallos seguidos (None = nada)."""
    if fallos < FALLOS_PARA_BLOQUEAR:
        return None
    # El exponente se acota: con cientos de fallos, 2**n desbordaría timedelta
    # (y con el máximo actual, a partir de 2**1 ya se alcanza).
    exponente = min(fallos - FALLOS_PARA_BLOQUEAR, 10)
    return min(BLOQUEO_INICIAL * 2**exponente, BLOQUEO_MAXIMO)


def tokens(usuario: Usuario, emitido: sesiones.Emitido) -> schemas.TokensOut:
    # round, no int: entre crear la fila y llegar aquí pasan microsegundos, y
    # truncar daría 1209599 en vez de 14 días justos.
    restante = emitido.sesion.expira_en - reloj.ahora()
    return schemas.TokensOut(
        access_token=seguridad.crear_access(usuario.id, emitido.sesion.familia_id),
        expires_in=int(seguridad.ACCESS_DURACION.total_seconds()),
        refresh_token=emitido.refresh,
        refresh_expires_in=max(0, round(restante.total_seconds())),
        usuario=schemas.UsuarioOut.model_validate(usuario),
    )


def _alias_en_uso():
    return error(status.HTTP_409_CONFLICT, "alias_en_uso", "Ese alias ya está en uso")


def comprobar_bloqueo(usuario: Usuario) -> None:
    """429 si la cuenta está bloqueada por demasiados fallos."""
    ahora = reloj.ahora()
    if usuario.bloqueado_hasta and ahora < usuario.bloqueado_hasta:
        raise demasiados_intentos(math.ceil((usuario.bloqueado_hasta - ahora).total_seconds()))


def registrar_fallo(usuario: Usuario) -> None:
    usuario.intentos_fallidos += 1
    bloqueo = duracion_bloqueo(usuario.intentos_fallidos)
    if bloqueo:
        usuario.bloqueado_hasta = reloj.ahora() + bloqueo
        log.warning("cuenta_bloqueada", usuario_id=usuario.id, fallos=usuario.intentos_fallidos,
                    segundos=int(bloqueo.total_seconds()))


def registrar_acierto(usuario: Usuario) -> None:
    usuario.intentos_fallidos = 0
    usuario.bloqueado_hasta = None


@router.post("/registro", response_model=schemas.TokensOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(limites.por_ip(limites.REGISTRO, "registro"))])
def registro(datos: schemas.RegistroEntrada, request: Request, respuesta: Response,
             en_cookie: bool = Depends(modo_cookie), db: Session = Depends(get_db)):
    """Crea una cuenta de jugador y abre su primera sesión."""
    if not settings.REGISTRO_ABIERTO:
        raise error(status.HTTP_403_FORBIDDEN, "registro_cerrado",
                    "El registro está cerrado: pide una cuenta a tu docente")
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
    log.info("registro", usuario_id=usuario.id)
    return cookie_refresh.entregar(respuesta, en_cookie, tokens(usuario, emitido))


@router.post("/login", response_model=schemas.TokensOut,
             dependencies=[Depends(limites.por_ip(limites.LOGIN, "login"))])
def login(datos: schemas.LoginEntrada, request: Request, respuesta: Response,
          en_cookie: bool = Depends(modo_cookie), db: Session = Depends(get_db)):
    """Abre una sesión nueva. Mismo error para alias inexistente y contraseña mala."""
    credenciales_invalidas = no_autorizado("credenciales_invalidas", "Alias o contraseña incorrectos")
    limites.comprobar_fallos_login(request)  # antes de gastar CPU en un hash
    # FOR UPDATE: dos intentos simultáneos contra la misma cuenta se ponen en
    # fila, así el contador de fallos no se puede esquivar en paralelo.
    usuario = db.scalar(
        select(Usuario).where(Usuario.alias == datos.alias).with_for_update()
    )
    if usuario is None:
        seguridad.gastar_tiempo_de_verificacion(datos.password)
        limites.registrar_fallo_login(request)
        raise credenciales_invalidas

    comprobar_bloqueo(usuario)

    if not seguridad.verificar_password(usuario.hash_password, datos.password):
        registrar_fallo(usuario)
        db.commit()
        limites.registrar_fallo_login(request)
        raise credenciales_invalidas

    registrar_acierto(usuario)
    if not usuario.activo:
        db.commit()
        raise error(status.HTTP_403_FORBIDDEN, "cuenta_desactivada", "La cuenta está desactivada")
    if seguridad.necesita_rehash(usuario.hash_password):
        usuario.hash_password = seguridad.hashear_password(datos.password)

    emitido = sesiones.abrir(db, usuario, request.headers.get("user-agent"))
    db.commit()
    return cookie_refresh.entregar(respuesta, en_cookie, tokens(usuario, emitido))


@router.get("/yo", response_model=schemas.UsuarioOut)
def yo(usuario: Usuario = Depends(usuario_actual)):
    return usuario


@router.post("/refresh", response_model=schemas.TokensOut,
             dependencies=[Depends(limites.por_ip(limites.REFRESH, "refresh"))])
def refresh(datos: schemas.RefreshEntrada, request: Request, respuesta: Response,
            en_cookie: bool = Depends(modo_cookie), db: Session = Depends(get_db)):
    """Cambia el refresh por uno nuevo y entrega un access nuevo.

    Repetir el mismo refresh dentro de 120 s (sin haber usado aún el nuevo)
    devuelve el mismo refresh nuevo: reintentos y peticiones simultáneas son
    seguros. Fuera de esa ventana, reusar un refresh cierra la sesión entera.

    En modo cookie (X-Refresh-En-Cookie: 1) se puede enviar {} y el refresh
    sale de la cookie; si se rechaza, la respuesta borra la cookie.
    """
    refresh_token = cookie_refresh.refresh_pedido(request, datos, en_cookie)
    try:
        usuario, emitido = sesiones.rotar(db, refresh_token, request.headers.get("user-agent"))
    except sesiones.RefreshRechazado as e:
        db.commit()  # guarda la revocación si fue un reuso
        http = (no_autorizado(e.codigo, e.mensaje) if e.estado == status.HTTP_401_UNAUTHORIZED
                else error(e.estado, e.codigo, e.mensaje))
        if en_cookie:
            http.headers = (http.headers or {}) | cookie_refresh.cabeceras_para_borrar()
        raise http
    db.commit()
    return cookie_refresh.entregar(respuesta, en_cookie, tokens(usuario, emitido))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(datos: schemas.RefreshEntrada, request: Request, respuesta: Response,
           en_cookie: bool = Depends(modo_cookie), db: Session = Depends(get_db)):
    """Cierra la sesión de ese refresh. Siempre 204: no revela si existía.
    En modo cookie, usa el de la cookie (si no viene en el cuerpo) y la borra."""
    try:
        refresh_token = cookie_refresh.refresh_pedido(request, datos, en_cookie)
    except HTTPException:  # modo cookie sin cookie: no hay nada que cerrar
        refresh_token = None
    if refresh_token is not None:
        sesiones.cerrar(db, refresh_token)
        db.commit()
    if en_cookie:
        cookie_refresh.borrar(respuesta)


@router.post("/logout-todo", status_code=status.HTTP_204_NO_CONTENT)
def logout_todo(respuesta: Response, en_cookie: bool = Depends(modo_cookie),
                usuario: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    """Cierra todas las sesiones del usuario, incluida esta, al instante."""
    sesiones.cerrar_todas(db, usuario, "logout_todo")
    db.commit()
    if en_cookie:
        cookie_refresh.borrar(respuesta)


@router.get("/sesiones", response_model=list[schemas.SesionOut])
def listar_sesiones(actual: SesionActual = Depends(sesion_actual), db: Session = Depends(get_db)):
    return [
        schemas.SesionOut(
            id=s.familia_id,
            iniciada_en=s.familia_expira_en - seguridad.FAMILIA_DURACION,
            ultimo_uso=s.creado_en,
            dispositivo=s.user_agent,
            actual=s.familia_id == actual.familia_id,
        )
        for s in sesiones.abiertas(db, actual.usuario)
    ]


@router.put("/password", response_model=schemas.TokensOut)
def cambiar_password(datos: schemas.PasswordCambio, request: Request, respuesta: Response,
                     en_cookie: bool = Depends(modo_cookie),
                     usuario: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    """Cambia la contraseña, cierra todas las sesiones y abre una nueva aquí."""
    # Los fallos cuentan para el bloqueo: con un access robado tampoco se
    # puede adivinar la contraseña actual por fuerza bruta.
    comprobar_bloqueo(usuario)
    if not seguridad.verificar_password(usuario.hash_password, datos.password_actual):
        registrar_fallo(usuario)
        db.commit()
        raise error(status.HTTP_403_FORBIDDEN, "password_actual_incorrecta", "La contraseña actual no es correcta")
    if schemas.password_igual_al_alias(datos.password_nueva, usuario.alias):
        raise validacion_password_igual_al_alias("password_nueva")

    registrar_acierto(usuario)
    usuario.hash_password = seguridad.hashear_password(datos.password_nueva)
    sesiones.cerrar_todas(db, usuario, "password")
    emitido = sesiones.abrir(db, usuario, request.headers.get("user-agent"))
    db.commit()
    return cookie_refresh.entregar(respuesta, en_cookie, tokens(usuario, emitido))
