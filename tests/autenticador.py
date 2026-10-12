"""Autenticador WebAuthn simulado para los tests (un "dispositivo" de software).

Crea una clave P-256 de verdad y produce las mismas estructuras que un
navegador con una passkey: clientDataJSON, authenticatorData, atestación
"none" y firmas ECDSA. La API las verifica con la librería `webauthn`, igual
que en producción, así que los tests prueban la criptografía real.

Los parámetros permiten fabricar respuestas tramposas: otro origen, otro
dominio, sin verificación del usuario, contador que retrocede…
"""

import base64
import hashlib
import json
import secrets
import struct

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

FLAG_UP, FLAG_UV, FLAG_BE, FLAG_BS, FLAG_AT = 0x01, 0x04, 0x08, 0x10, 0x40


def b64url(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).decode().rstrip("=")


def de_b64url(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


class Autenticador:
    def __init__(self, rp_id: str = "buelvasmanuel.github.io", origen: str = "https://buelvasmanuel.github.io"):
        self.rp_id, self.origen = rp_id, origen
        self.clave = ec.generate_private_key(ec.SECP256R1())
        self.credencial_id = secrets.token_bytes(32)
        self.contador = 0
        self.user_handle: bytes | None = None

    def _clave_cose(self) -> bytes:
        numeros = self.clave.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numeros.x.to_bytes(32, "big"), -3: numeros.y.to_bytes(32, "big")})

    def _datos_del_autenticador(self, flags: int, rp_id: str, con_credencial: bool) -> bytes:
        datos = hashlib.sha256(rp_id.encode()).digest() + bytes([flags]) + struct.pack(">I", self.contador)
        if con_credencial:
            aaguid = bytes(16)
            datos += aaguid + struct.pack(">H", len(self.credencial_id)) + self.credencial_id + self._clave_cose()
        return datos

    def _datos_del_cliente(self, tipo: str, reto: str, origen: str) -> bytes:
        return json.dumps({"type": tipo, "challenge": reto, "origin": origen, "crossOrigin": False}).encode()

    def crear(self, opciones: dict, *, origen: str | None = None, rp_id: str | None = None,
              verificado: bool = True, sincronizada: bool = True) -> dict:
        """Como navigator.credentials.create(): devuelve credential.toJSON()."""
        self.user_handle = de_b64url(opciones["user"]["id"])
        flags = FLAG_UP | FLAG_AT | (FLAG_UV if verificado else 0) | ((FLAG_BE | FLAG_BS) if sincronizada else 0)
        auth = self._datos_del_autenticador(flags, rp_id or opciones["rp"]["id"], con_credencial=True)
        cliente = self._datos_del_cliente("webauthn.create", opciones["challenge"], origen or self.origen)
        atestacion = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth})
        return {
            "id": b64url(self.credencial_id), "rawId": b64url(self.credencial_id), "type": "public-key",
            "response": {"clientDataJSON": b64url(cliente), "attestationObject": b64url(atestacion),
                         "transports": ["internal", "hybrid"]},
            "clientExtensionResults": {}, "authenticatorAttachment": "platform",
        }

    def firmar(self, opciones: dict, *, origen: str | None = None, rp_id: str | None = None,
               verificado: bool = True, contador: int | None = None, reto: str | None = None) -> dict:
        """Como navigator.credentials.get(): devuelve credential.toJSON()."""
        self.contador = self.contador + 1 if contador is None else contador
        flags = FLAG_UP | (FLAG_UV if verificado else 0) | FLAG_BE | FLAG_BS
        auth = self._datos_del_autenticador(flags, rp_id or opciones["rpId"], con_credencial=False)
        cliente = self._datos_del_cliente("webauthn.get", reto or opciones["challenge"], origen or self.origen)
        firma = self.clave.sign(auth + hashlib.sha256(cliente).digest(), ec.ECDSA(hashes.SHA256()))
        return {
            "id": b64url(self.credencial_id), "rawId": b64url(self.credencial_id), "type": "public-key",
            "response": {"clientDataJSON": b64url(cliente), "authenticatorData": b64url(auth),
                         "signature": b64url(firma), "userHandle": b64url(self.user_handle or b"")},
            "clientExtensionResults": {}, "authenticatorAttachment": "platform",
        }
