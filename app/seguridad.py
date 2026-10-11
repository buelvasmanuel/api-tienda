"""Criptografía de la autenticación: contraseñas y tokens.

Solo funciones puras (no tocan la base de datos). El contrato con el cliente
está en docs/CONTRATO_AUTH.md.

- Contraseñas: Argon2id. Como cada hash reserva ARGON2_MEMORIA_KIB y Render
  free tiene 512 MB y 0,1 CPU, un semáforo limita cuántos se calculan a la vez.
- Access token: PASETO v4.local (cifrado y autenticado, con un único algoritmo
  fijado por la versión: no hay campo "alg" que un atacante pueda cambiar).
- Refresh token: texto aleatorio opaco. En la base solo se guarda su SHA-256.
  El sucesor de un refresh es HMAC(clave, refresh): repetir el mismo refresh
  (reintento o petición concurrente) da siempre el mismo sucesor.
"""

import base64
import hashlib
import hmac
import json
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pyseto
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pyseto import Key

from app import reloj
from app.config import settings

ACCESS_DURACION = timedelta(minutes=15)
REFRESH_DURACION = timedelta(days=14)
FAMILIA_DURACION = timedelta(days=30)  # máximo absoluto de una sesión
# Ventana del refresh idempotente: más que el timeout del cliente (60 s, por el
# arranque en frío de Render) más un reintento. Ver docs/CONTRATO_AUTH.md §5.
REFRESH_GRACIA = timedelta(seconds=120)

EMISOR = "api-tienda"
AUDIENCIA = "app-estudiantes"


# ---------- Contraseñas ----------

_hasher = PasswordHasher(
    time_cost=settings.ARGON2_TIEMPO,
    memory_cost=settings.ARGON2_MEMORIA_KIB,
    parallelism=settings.ARGON2_PARALELISMO,
    type=Type.ID,
)
_semaforo = threading.BoundedSemaphore(settings.ARGON2_CONCURRENCIA)

# Hash de una contraseña que nadie tiene. Si el alias no existe, se verifica
# contra este hash para que la respuesta tarde lo mismo que con un alias real
# (si no, el tiempo delataría qué alias existen).
_HASH_FALSO = _hasher.hash(secrets.token_urlsafe(16))


def hashear_password(password: str) -> str:
    with _semaforo:
        return _hasher.hash(password)


def verificar_password(hash_guardado: str, password: str) -> bool:
    with _semaforo:
        try:
            return _hasher.verify(hash_guardado, password)
        except (VerificationError, InvalidHashError):
            return False


# Sin letras ni números que se confundan al dictarlos o copiarlos (i, l, o, 0, 1).
_ALFABETO_PASSWORD = "abcdefghjkmnpqrstuvwxyz23456789"


def generar_password() -> str:
    """Contraseña inicial para cuentas creadas por un docente: 12 caracteres
    aleatorios en 3 grupos ("k7mq-x3hp-9tfa"), unos 59 bits de entropía."""
    caracteres = [secrets.choice(_ALFABETO_PASSWORD) for _ in range(12)]
    return "-".join("".join(caracteres[i:i + 4]) for i in range(0, 12, 4))


def gastar_tiempo_de_verificacion(password: str) -> None:
    """Verifica contra un hash falso: mismo coste que un login real."""
    verificar_password(_HASH_FALSO, password)


def necesita_rehash(hash_guardado: str) -> bool:
    """True si el hash se hizo con otros parámetros (p. ej. tras subirlos)."""
    return _hasher.check_needs_rehash(hash_guardado)


# ---------- Claves ----------

def _derivar(proposito: bytes) -> bytes:
    """Subclave independiente para cada uso, derivada de CLAVE_TOKENS (HKDF)."""
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=proposito).derive(
        settings.clave_tokens
    )


_CLAVE_PASETO = Key.new(version=4, purpose="local", key=_derivar(b"api-tienda/paseto-v4-local"))
_CLAVE_REFRESH = _derivar(b"api-tienda/refresh-sucesor")


# ---------- Access token (PASETO v4.local) ----------

class TokenInvalido(Exception):
    """El access token no sirve. `codigo` es el del contrato (token_invalido, token_expirado)."""

    def __init__(self, codigo: str, mensaje: str):
        super().__init__(mensaje)
        self.codigo = codigo
        self.mensaje = mensaje


@dataclass(frozen=True)
class Access:
    usuario_id: int
    familia_id: str
    emitido_en: datetime  # UTC sin zona, como reloj.ahora()


def _a_iso(momento: datetime) -> str:
    return momento.isoformat(timespec="microseconds") + "Z"


def _de_iso(texto: str) -> datetime:
    momento = datetime.fromisoformat(texto)
    if momento.tzinfo is None:
        raise ValueError("fecha sin zona horaria")
    return momento.astimezone(UTC).replace(tzinfo=None)


def crear_access(usuario_id: int, familia_id: str) -> str:
    ahora = reloj.ahora()
    claims = {
        "iss": EMISOR,
        "aud": AUDIENCIA,
        "sub": str(usuario_id),
        "sid": familia_id,
        "iat": _a_iso(ahora),
        "exp": _a_iso(ahora + ACCESS_DURACION),
    }
    # Payload en bytes y sin `exp=`: así pyseto no añade ni valida claims con la
    # hora real, y la caducidad se comprueba aquí con reloj.ahora().
    return pyseto.encode(_CLAVE_PASETO, json.dumps(claims).encode()).decode()


def leer_access(token: str) -> Access:
    """Descifra y valida un access token. Lanza TokenInvalido si no sirve."""
    try:
        # Solo acepta v4.local con nuestra clave: cualquier otra versión,
        # propósito o clave, o un solo byte cambiado, falla aquí.
        claims = json.loads(pyseto.decode(_CLAVE_PASETO, token).payload)
        if claims.get("iss") != EMISOR or claims.get("aud") != AUDIENCIA:
            raise ValueError("emisor o audiencia incorrectos")
        access = Access(
            usuario_id=int(claims["sub"]),
            familia_id=str(claims["sid"]),
            emitido_en=_de_iso(claims["iat"]),
        )
        expira_en = _de_iso(claims["exp"])
    except (pyseto.PysetoError, ValueError, KeyError, TypeError, AttributeError):
        raise TokenInvalido("token_invalido", "Token no válido")

    if reloj.ahora() >= expira_en:
        raise TokenInvalido("token_expirado", "El token ha caducado")
    return access


# ---------- Refresh token (opaco) ----------

def nuevo_refresh() -> str:
    """Primer refresh de una sesión: 256 bits aleatorios (43 caracteres)."""
    return secrets.token_urlsafe(32)


def sucesor_refresh(refresh: str) -> str:
    """Refresh que sustituye a `refresh` al rotarlo. Determinista: el mismo
    refresh produce siempre el mismo sucesor (base del refresh idempotente)."""
    firma = hmac.new(_CLAVE_REFRESH, refresh.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(firma).decode().rstrip("=")


def hash_refresh(refresh: str) -> str:
    """Lo que se guarda en la base. Un SHA-256 basta (no Argon2): el token tiene
    256 bits de entropía, así que no se puede adivinar por fuerza bruta, y hay
    que poder buscarlo por su hash."""
    return hashlib.sha256(refresh.encode()).hexdigest()
