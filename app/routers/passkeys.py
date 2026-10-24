"""Passkeys: registrar, entrar y gestionar. Contrato: docs/CONTRATO_PASSKEYS.md."""

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import cookie_refresh, limites, logs, schemas, seguridad, sesiones
from app import passkeys as reglas
from app.database import get_db
from app.dependencias import usuario_actual
from app.errores import error, no_autorizado
from app.models import Passkey, Usuario
from app.routers.auth import comprobar_bloqueo, registrar_acierto, registrar_fallo, tokens

log = logs.obtener("app.auth")


def _sin_cache(respuesta: Response) -> None:
    respuesta.headers["Cache-Control"] = "no-store"  # opciones con desafíos y respuestas con tokens


router = APIRouter(prefix="/passkeys", tags=["Passkeys"], dependencies=[Depends(_sin_cache)])
por_ip = [Depends(limites.por_ip(limites.PASSKEYS, "passkeys"))]


def _http(e: reglas.PasskeyRechazada):
    if e.estado == 401:
        return no_autorizado(e.codigo, e.mensaje)
    return error(e.estado, e.codigo, e.mensaje)


def passkey_out(p: Passkey) -> schemas.PasskeyOut:
    return schemas.PasskeyOut(id=p.id, nombre=p.nombre, creada_en=p.creado_en, ultimo_uso=p.ultimo_uso,
                              sincronizada=p.sincronizada)


@router.post("/registro/opciones", response_model=schemas.OpcionesPasskeyOut,
             dependencies=[Depends(limites.por_usuario(limites.PASSKEYS_REGISTRO, "passkeys-registro"))])
def opciones_registro(datos: schemas.OpcionesRegistroPasskeyEntrada, usuario: Usuario = Depends(usuario_actual),
                      db: Session = Depends(get_db)):
    # Como en PUT /auth/password: los fallos cuentan para el bloqueo de la
    # cuenta, así que con un access robado tampoco se adivina por fuerza bruta.
    comprobar_bloqueo(usuario)
    if not seguridad.verificar_password(usuario.hash_password, datos.password_actual):
        registrar_fallo(usuario)
        db.commit()
        log.info("passkey_rechazada", paso="opciones", codigo="password_actual_incorrecta", usuario_id=usuario.id)
        raise error(status.HTTP_403_FORBIDDEN, "password_actual_incorrecta", "La contraseña actual no es correcta")
    registrar_acierto(usuario)
    try:
        reto_id, opciones = reglas.opciones_registro(db, usuario)
    except reglas.PasskeyRechazada as e:
        raise _http(e)
    db.commit()
    return schemas.OpcionesPasskeyOut(reto_id=reto_id, opciones=opciones)


@router.post("/registro/verificacion", response_model=schemas.PasskeyOut, status_code=status.HTTP_201_CREATED)
def registrar(datos: schemas.RegistroPasskeyEntrada, usuario: Usuario = Depends(usuario_actual),
              db: Session = Depends(get_db)):
    try:
        passkey = reglas.registrar(db, usuario, datos.reto_id, datos.credencial, datos.nombre)
    except reglas.PasskeyRechazada as e:
        db.commit()  # el reto queda gastado aunque la passkey no valga: un solo uso
        log.info("passkey_rechazada", paso="registro", codigo=e.codigo, usuario_id=usuario.id)
        raise _http(e)
    db.commit()
    log.info("passkey_registrada", usuario_id=usuario.id, passkey_id=passkey.id)
    return passkey_out(passkey)


@router.post("/login/opciones", response_model=schemas.OpcionesPasskeyOut, dependencies=por_ip)
def opciones_login(db: Session = Depends(get_db)):
    reto_id, opciones = reglas.opciones_login(db)
    db.commit()
    return schemas.OpcionesPasskeyOut(reto_id=reto_id, opciones=opciones)


@router.post("/login/verificacion", response_model=schemas.TokensOut, dependencies=por_ip)
def entrar(datos: schemas.LoginPasskeyEntrada, request: Request, respuesta: Response,
           en_cookie: bool = Depends(cookie_refresh.modo_cookie), db: Session = Depends(get_db)):
    try:
        usuario = reglas.entrar(db, datos.reto_id, datos.credencial)
    except reglas.PasskeyRechazada as e:
        db.commit()
        log.info("passkey_rechazada", paso="login", codigo=e.codigo)
        raise _http(e)
    emitido = sesiones.abrir(db, usuario, request.headers.get("user-agent"))
    db.commit()
    log.info("login_con_passkey", usuario_id=usuario.id)
    return cookie_refresh.entregar(respuesta, en_cookie, tokens(usuario, emitido))


@router.get("", response_model=list[schemas.PasskeyOut])
def listar(usuario: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    filas = db.scalars(select(Passkey).where(Passkey.usuario_id == usuario.id)
                       .order_by(Passkey.creado_en.desc(), Passkey.id.desc()))
    return [passkey_out(p) for p in filas]


@router.delete("/{passkey_id}", status_code=status.HTTP_204_NO_CONTENT)
def borrar(passkey_id: int, usuario: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    passkey = db.get(Passkey, passkey_id)
    if passkey is None or passkey.usuario_id != usuario.id:
        raise error(status.HTTP_404_NOT_FOUND, "passkey_no_encontrada", "No existe esa passkey")
    db.delete(passkey)
    db.commit()
    log.info("passkey_borrada", usuario_id=usuario.id, passkey_id=passkey_id)
