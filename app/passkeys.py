"""Passkeys (WebAuthn) como forma adicional de entrar. Contrato:
docs/CONTRATO_PASSKEYS.md.

Se guarda solo la clave pública de cada passkey. Cada registro o entrada usa
un desafío aleatorio de un solo uso, guardado en la base con 5 minutos de
vida. La verificación criptográfica la hace la librería `webauthn` (Duo
Labs): comprueba el origen, el dominio, el desafío, la firma y que el
usuario se verificó (huella, cara o PIN).
"""

import json
import secrets
import uuid
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app import reloj, seguridad
from app.config import settings
from app.models import Passkey, RetoWebAuthn, Usuario

RETO_DURACION = timedelta(minutes=5)
NOMBRE_POR_DEFECTO = "Passkey"
# Cualquier fallo al interpretar o verificar lo que manda el navegador.
_ERRORES_DE_VERIFICACION = (WebAuthnException, ValueError, KeyError, TypeError)


class PasskeyRechazada(Exception):
    def __init__(self, estado: int, codigo: str, mensaje: str):
        super().__init__(mensaje)
        self.estado, self.codigo, self.mensaje = estado, codigo, mensaje


def _reto_invalido() -> PasskeyRechazada:
    return PasskeyRechazada(401, "reto_invalido", "El desafío no es válido o ya caducó: vuelve a intentarlo")


# ---------- Desafíos ----------

def _nuevo_reto(db: Session, tipo: str, usuario_id: int | None) -> tuple[RetoWebAuthn, bytes]:
    ahora = reloj.ahora()
    db.execute(delete(RetoWebAuthn).where(RetoWebAuthn.expira_en < ahora))  # limpieza (no hay cron en Render free)
    desafio = secrets.token_bytes(32)
    reto = RetoWebAuthn(id=uuid.uuid4().hex, tipo=tipo, usuario_id=usuario_id,
                        reto=bytes_to_base64url(desafio), expira_en=ahora + RETO_DURACION)
    db.add(reto)
    db.flush()
    return reto, desafio


def _usar_reto(db: Session, reto_id: str, tipo: str, usuario_id: int | None) -> bytes:
    """Marca el desafío como usado y lo devuelve. FOR UPDATE: dos peticiones
    con el mismo reto no pueden usarlo las dos."""
    reto = db.scalar(select(RetoWebAuthn).where(RetoWebAuthn.id == reto_id).with_for_update())
    ahora = reloj.ahora()
    if (reto is None or reto.tipo != tipo or reto.usuario_id != usuario_id
            or reto.usado_en is not None or ahora >= reto.expira_en):
        raise _reto_invalido()
    reto.usado_en = ahora
    return base64url_to_bytes(reto.reto)


# ---------- Registro ----------

def opciones_registro(db: Session, usuario: Usuario) -> tuple[str, dict]:
    existentes = db.scalars(select(Passkey.credencial_id).where(Passkey.usuario_id == usuario.id)).all()
    reto, desafio = _nuevo_reto(db, "registro", usuario.id)
    opciones = generate_registration_options(
        rp_id=settings.PASSKEYS_RP_ID,
        rp_name=settings.PASSKEYS_RP_NOMBRE,
        user_id=seguridad.identificador_webauthn(usuario.id),
        user_name=usuario.alias,
        user_display_name=usuario.alias,
        challenge=desafio,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,  # descubrible: entrar sin escribir el alias
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(c)) for c in existentes],
    )
    return reto.id, json.loads(options_to_json(opciones))


def registrar(db: Session, usuario: Usuario, reto_id: str, credencial: dict, nombre: str | None) -> Passkey:
    desafio = _usar_reto(db, reto_id, "registro", usuario.id)
    try:
        verificada = verify_registration_response(
            credential=credencial, expected_challenge=desafio, expected_rp_id=settings.PASSKEYS_RP_ID,
            expected_origin=settings.passkeys_origenes, require_user_verification=True,
        )
    except _ERRORES_DE_VERIFICACION:
        raise PasskeyRechazada(422, "passkey_invalida", "La passkey no se pudo verificar")
    credencial_id = bytes_to_base64url(verificada.credential_id)
    if db.scalar(select(Passkey.id).where(Passkey.credencial_id == credencial_id)) is not None:
        raise PasskeyRechazada(409, "passkey_repetida", "Esa passkey ya está registrada")
    passkey = Passkey(
        usuario_id=usuario.id, credencial_id=credencial_id,
        clave_publica=bytes_to_base64url(verificada.credential_public_key),
        contador=verificada.sign_count, sincronizada=verificada.credential_backed_up,
        nombre=nombre or NOMBRE_POR_DEFECTO,
    )
    db.add(passkey)
    db.flush()
    return passkey


# ---------- Entrar ----------

def opciones_login(db: Session) -> tuple[str, dict]:
    reto, desafio = _nuevo_reto(db, "login", None)
    # Sin allowCredentials: el navegador ofrece las passkeys de este dominio.
    opciones = generate_authentication_options(rp_id=settings.PASSKEYS_RP_ID, challenge=desafio,
                                               user_verification=UserVerificationRequirement.REQUIRED)
    return reto.id, json.loads(options_to_json(opciones))


def entrar(db: Session, reto_id: str, credencial: dict) -> Usuario:
    desafio = _usar_reto(db, reto_id, "login", None)
    invalida = PasskeyRechazada(401, "passkey_invalida", "La passkey no es válida")
    credencial_id = credencial.get("rawId") or credencial.get("id")
    if not isinstance(credencial_id, str):
        raise invalida
    passkey = db.scalar(select(Passkey).where(Passkey.credencial_id == credencial_id).with_for_update())
    if passkey is None:
        raise invalida
    try:
        verificada = verify_authentication_response(
            credential=credencial, expected_challenge=desafio, expected_rp_id=settings.PASSKEYS_RP_ID,
            expected_origin=settings.passkeys_origenes,
            credential_public_key=base64url_to_bytes(passkey.clave_publica),
            credential_current_sign_count=passkey.contador, require_user_verification=True,
        )
    except _ERRORES_DE_VERIFICACION:
        raise invalida
    usuario = db.get(Usuario, passkey.usuario_id)
    if not usuario.activo:
        raise PasskeyRechazada(403, "cuenta_desactivada", "La cuenta está desactivada")
    passkey.contador = verificada.new_sign_count
    passkey.ultimo_uso = reloj.ahora()
    return usuario


def borrar_de(db: Session, usuario_id: int) -> int:
    """Borra todas las passkeys de un usuario (al restablecer su cuenta)."""
    return db.execute(delete(Passkey).where(Passkey.usuario_id == usuario_id)).rowcount
