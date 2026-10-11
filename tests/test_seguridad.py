"""Ataques contra el access token (PASETO v4.local) y propiedades de los refresh.

Cada test es un intento de falsificar o reutilizar un token; todos deben
acabar en 401 con el código del contrato (docs/CONTRATO_AUTH.md §2).
"""

import base64
import json

import pyseto
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pyseto import Key

from app import seguridad
from tests.ayudas import cabeceras


def claims_validos(**cambios) -> dict:
    return {
        "iss": seguridad.EMISOR, "aud": seguridad.AUDIENCIA, "sub": "1", "sid": "f" * 32,
        "iat": "2026-10-11T12:00:00.000000Z", "exp": "2999-01-01T00:00:00.000000Z",
    } | cambios


def cifrar(claims: dict, clave=None) -> str:
    """Token con nuestra clave (u otra) y los claims que queramos."""
    clave = clave or seguridad._CLAVE_PASETO
    return pyseto.encode(clave, json.dumps(claims).encode()).decode()


def codigo_de(token: str) -> str:
    with pytest.raises(seguridad.TokenInvalido) as e:
        seguridad.leer_access(token)
    return e.value.codigo


# ---------- El token legítimo funciona ----------

def test_access_ida_y_vuelta(reloj):
    token = seguridad.crear_access(7, "a" * 32)

    access = seguridad.leer_access(token)

    assert (access.usuario_id, access.familia_id, access.emitido_en) == (7, "a" * 32, reloj.momento)
    assert token.startswith("v4.local.")


def test_el_contenido_va_cifrado():
    # v4.local cifra: el cliente (o quien robe el token) no puede leer los claims.
    cuerpo = seguridad.crear_access(7, "a" * 32).removeprefix("v4.local.")
    descodificado = base64.urlsafe_b64decode(cuerpo + "=" * (-len(cuerpo) % 4))

    assert b"api-tienda" not in descodificado and b'"sub"' not in descodificado


# ---------- Manipulación ----------

def test_un_byte_cambiado_invalida_el_token():
    token = seguridad.crear_access(1, "f" * 32)
    posicion = len("v4.local.") + 40  # en mitad del texto cifrado
    cambiado = token[:posicion] + ("A" if token[posicion] != "A" else "B") + token[posicion + 1:]

    assert codigo_de(cambiado) == "token_invalido"


@pytest.mark.parametrize("token", ["", "basura", "v4.local.", "v4.local.AAAA"])
def test_tokens_mal_formados(token):
    assert codigo_de(token) == "token_invalido"


def test_token_truncado():
    token = seguridad.crear_access(1, "f" * 32)

    assert codigo_de(token[:-5]) == "token_invalido"


def test_token_cifrado_con_otra_clave():
    otra = Key.new(version=4, purpose="local", key=b"x" * 32)

    assert codigo_de(cifrar(claims_validos(), otra)) == "token_invalido"


def test_token_v4_public_firmado_por_un_atacante():
    # Confusión de algoritmo al estilo JWT: un token firmado (no cifrado) con
    # una clave del atacante. PASETO no deja elegir: solo vale v4.local.
    privada = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    firmado = cifrar(claims_validos(), Key.new(version=4, purpose="public", key=privada))

    assert firmado.startswith("v4.public.")
    assert codigo_de(firmado) == "token_invalido"


def test_token_de_otra_version_de_paseto():
    v3 = cifrar(claims_validos(), Key.new(version=3, purpose="local", key=b"x" * 32))

    assert codigo_de(v3) == "token_invalido"


def test_jwt_con_alg_none():
    def b64(datos: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(datos).encode()).decode().rstrip("=")

    jwt = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(claims_validos())}."

    assert codigo_de(jwt) == "token_invalido"


def test_un_refresh_no_sirve_como_access():
    assert codigo_de(seguridad.nuevo_refresh()) == "token_invalido"


# ---------- Claims ----------

@pytest.mark.parametrize(
    "cambios",
    [{"iss": "otro-emisor"}, {"aud": "otra-app"}, {"sub": "uno"}, {"iat": "ayer"},
     {"iat": "2026-10-11T12:00:00"},  # sin zona horaria
     {"exp": 1234}],
    ids=["iss", "aud", "sub", "iat", "iat-sin-zona", "exp-numero"],
)
def test_claims_incorrectos(cambios):
    assert codigo_de(cifrar(claims_validos(**cambios))) == "token_invalido"


@pytest.mark.parametrize("falta", ["iss", "aud", "sub", "sid", "iat", "exp"])
def test_claims_que_faltan(falta):
    claims = claims_validos()
    del claims[falta]

    assert codigo_de(cifrar(claims)) == "token_invalido"


def test_payload_que_no_es_un_objeto():
    token = pyseto.encode(seguridad._CLAVE_PASETO, b'["no", "es", "un", "objeto"]').decode()

    assert codigo_de(token) == "token_invalido"


# ---------- Caducidad ----------

def test_el_access_dura_15_minutos(reloj):
    token = seguridad.crear_access(1, "f" * 32)

    reloj.avanzar(minutes=14, seconds=59)
    seguridad.leer_access(token)  # todavía vale

    reloj.avanzar(seconds=1)
    assert codigo_de(token) == "token_expirado"


# ---------- Refresh ----------

def test_refresh_nuevo_aleatorio_de_43_caracteres():
    tokens = {seguridad.nuevo_refresh() for _ in range(100)}

    assert len(tokens) == 100
    assert {len(t) for t in tokens} == {43}


def test_sucesor_determinista_y_distinto():
    refresh = seguridad.nuevo_refresh()

    sucesor = seguridad.sucesor_refresh(refresh)

    assert sucesor == seguridad.sucesor_refresh(refresh)  # base del refresh idempotente
    assert sucesor != refresh and len(sucesor) == 43
    assert seguridad.sucesor_refresh(sucesor) not in (refresh, sucesor)


def test_hash_de_refresh_sha256():
    assert len(seguridad.hash_refresh("abc")) == 64
    assert seguridad.hash_refresh("abc") != seguridad.hash_refresh("abd")


def test_las_subclaves_son_independientes():
    from app.config import settings

    claves = {settings.clave_tokens, seguridad._CLAVE_REFRESH, seguridad._derivar(b"api-tienda/paseto-v4-local")}

    assert len(claves) == 3


# ---------- A través de la API ----------

def test_api_rechaza_token_alterado(client, crear_usuario):
    usuario = crear_usuario()
    token = cabeceras(usuario)["Authorization"]
    alterado = token[:-3] + ("AAA" if not token.endswith("AAA") else "BBB")

    respuesta = client.get("/auth/yo", headers={"Authorization": alterado})

    assert respuesta.status_code == 401
    assert respuesta.json()["detail"]["codigo"] == "token_invalido"
    assert respuesta.headers["WWW-Authenticate"] == "Bearer"


def test_api_rechaza_token_caducado(client, crear_usuario, reloj):
    usuario = crear_usuario()
    viejo = cabeceras(usuario)
    reloj.avanzar(minutes=15)

    respuesta = client.get("/auth/yo", headers=viejo)

    assert respuesta.status_code == 401
    assert respuesta.json()["detail"]["codigo"] == "token_expirado"
