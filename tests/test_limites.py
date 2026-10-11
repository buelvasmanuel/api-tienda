"""Límite por IP, elección de la IP del cliente, cabeceras de seguridad, CORS
y validación de la configuración."""

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from app import limites
from app.config import Settings, settings
from tests.ayudas import PASSWORD, codigo


def login(client, password=PASSWORD, **cabeceras):
    return client.post("/auth/login", json={"alias": "jugador_1", "password": password}, headers=cabeceras)


# ---------- Límite por IP (pensado para un aula: muchos alumnos, una IP) ----------

def test_una_clase_entera_inicia_sesion_desde_la_misma_ip(client, crear_usuario, limite_por_ip):
    for i in range(40):
        crear_usuario(f"alumno_{i:02d}")

    respuestas = [client.post("/auth/login", json={"alias": f"alumno_{i:02d}", "password": PASSWORD})
                  for i in range(40)]

    assert {r.status_code for r in respuestas} == {200}


def test_logins_fallidos_por_ip(client, crear_usuario, limite_por_ip, monkeypatch):
    crear_usuario("jugador_1")
    for i in range(30):  # alias distintos: el bloqueo por cuenta no entra en juego
        assert client.post("/auth/login", json={"alias": f"no_existe_{i}", "password": "x"}).status_code == 401
    hashes = []
    monkeypatch.setattr("app.seguridad.verificar_password", lambda *a: hashes.append(a))

    # El 31.º intento se corta antes de calcular ningún hash, aunque sea correcto.
    respuesta = login(client)

    assert respuesta.status_code == 429
    assert codigo(respuesta) == "demasiados_intentos"
    assert 1 <= int(respuesta.headers["Retry-After"]) <= 60
    assert hashes == []


def test_los_logins_correctos_no_cuentan_como_fallos(client, crear_usuario, limite_por_ip):
    crear_usuario("jugador_1")
    for i in range(29):
        client.post("/auth/login", json={"alias": f"no_existe_{i}", "password": "x"})
    for _ in range(10):
        assert login(client).status_code == 200

    assert client.post("/auth/login", json={"alias": "no_existe", "password": "x"}).status_code == 401  # fallo 30
    assert codigo(login(client)) == "demasiados_intentos"


def test_techo_total_de_logins_por_ip(client, crear_usuario, limite_por_ip):
    crear_usuario("jugador_1")
    for _ in range(120):
        assert login(client).status_code == 200

    assert login(client).status_code == 429


def test_registro_60_por_hora(client, limite_por_ip):
    for i in range(60):
        assert client.post("/auth/registro", json={"alias": f"nuevo_{i}", "password": PASSWORD}).status_code == 201

    respuesta = client.post("/auth/registro", json={"alias": "nuevo_60", "password": PASSWORD})

    assert respuesta.status_code == 429
    assert int(respuesta.headers["Retry-After"]) > 60


def test_refresh_300_por_minuto(client, limite_por_ip):
    for _ in range(300):
        assert client.post("/auth/refresh", json={"refresh_token": "x"}).status_code == 401

    assert client.post("/auth/refresh", json={"refresh_token": "x"}).status_code == 429


def fallar_30_veces(client, xff: str) -> None:
    for i in range(30):
        client.post("/auth/login", json={"alias": f"no_existe_{i}", "password": "x"},
                    headers={"X-Forwarded-For": xff})


def test_cada_ip_tiene_su_contador(client, crear_usuario, limite_por_ip, monkeypatch):
    monkeypatch.setattr(settings, "IP_CLIENTE", "x-forwarded-for:1")
    crear_usuario("jugador_1")
    fallar_30_veces(client, "203.0.113.1")

    assert login(client, **{"X-Forwarded-For": "203.0.113.1"}).status_code == 429
    assert login(client, **{"X-Forwarded-For": "203.0.113.2"}).status_code == 200


def test_falsificar_la_ip_de_la_izquierda_no_esquiva_el_limite(client, crear_usuario, limite_por_ip, monkeypatch):
    # El proxy añade la IP real a la derecha; lo de la izquierda lo escribe el cliente.
    monkeypatch.setattr(settings, "IP_CLIENTE", "x-forwarded-for:1")
    crear_usuario("jugador_1")
    fallar_30_veces(client, "10.0.0.1, 203.0.113.1")

    assert login(client, **{"X-Forwarded-For": "10.0.0.99, 203.0.113.1"}).status_code == 429


def test_los_demas_endpoints_no_tienen_limite_por_ip(client, limite_por_ip):
    for _ in range(40):
        assert client.get("/estudiantes/").status_code == 200


# ---------- Qué IP se usa ----------

def peticion(cabeceras: dict[str, str], ip="10.9.8.7") -> Request:
    return Request({
        "type": "http", "method": "GET", "path": "/", "client": (ip, 1234),
        "headers": [(k.lower().encode(), v.encode()) for k, v in cabeceras.items()],
    })


@pytest.mark.parametrize(
    ("modo", "cabeceras", "esperada"),
    [
        ("conexion", {"X-Forwarded-For": "1.1.1.1"}, "10.9.8.7"),
        ("x-forwarded-for:1", {"X-Forwarded-For": "6.6.6.6, 1.1.1.1, 2.2.2.2"}, "2.2.2.2"),
        ("x-forwarded-for:2", {"X-Forwarded-For": "6.6.6.6, 1.1.1.1, 2.2.2.2"}, "1.1.1.1"),
        ("x-forwarded-for:2", {"X-Forwarded-For": "2.2.2.2"}, "10.9.8.7"),  # faltan saltos
        ("x-forwarded-for:1", {}, "10.9.8.7"),
        ("cf-connecting-ip", {"CF-Connecting-IP": "3.3.3.3"}, "3.3.3.3"),
        ("cf-connecting-ip", {}, "10.9.8.7"),
    ],
)
def test_ip_cliente(monkeypatch, modo, cabeceras, esperada):
    monkeypatch.setattr(settings, "IP_CLIENTE", modo)

    assert limites.ip_cliente(peticion(cabeceras)) == esperada


# ---------- Cabeceras de seguridad y CORS ----------

@pytest.mark.parametrize("ruta", ["/", "/estudiantes/", "/no-existe"])
def test_cabeceras_de_seguridad(client, ruta):
    cabeceras = client.get(ruta).headers

    assert cabeceras["X-Content-Type-Options"] == "nosniff"
    assert cabeceras["Strict-Transport-Security"] == "max-age=31536000"
    assert cabeceras["X-Frame-Options"] == "DENY"
    assert cabeceras["Referrer-Policy"] == "no-referrer"


def test_cors_permite_el_origen_configurado(client):
    respuesta = client.options("/auth/login", headers={
        "Origin": "https://app.ejemplo.org", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization, content-type",
    })

    assert respuesta.status_code == 200
    assert respuesta.headers["Access-Control-Allow-Origin"] == "https://app.ejemplo.org"


def test_cors_rechaza_otros_origenes(client):
    respuesta = client.get("/", headers={"Origin": "https://malicioso.example"})

    assert "Access-Control-Allow-Origin" not in respuesta.headers


def test_cors_expone_retry_after(client):
    respuesta = client.get("/", headers={"Origin": "https://app.ejemplo.org"})

    assert respuesta.headers["Access-Control-Expose-Headers"] == "Retry-After"


# ---------- Configuración ----------

BASE = {"DB_USER": "u", "DB_PASSWORD": "p", "DB_NAME": "d", "CORS_ORIGINS": "*",
        "CLAVE_TOKENS": "dGVzdHMtdGVzdHMtdGVzdHMtdGVzdHMtdGVzdHMtMzI"}


@pytest.mark.parametrize("falta", ["CLAVE_TOKENS", "CORS_ORIGINS"])
def test_variables_obligatorias(monkeypatch, falta):
    monkeypatch.delenv(falta, raising=False)

    with pytest.raises(ValidationError, match=falta):
        Settings(_env_file=None, **{k: v for k, v in BASE.items() if k != falta})


@pytest.mark.parametrize("clave", ["corta", "no es base64 !!", "QUFB" * 12])
def test_clave_de_tokens_invalida(clave):
    with pytest.raises(ValidationError, match="CLAVE_TOKENS"):
        Settings(_env_file=None, **BASE | {"CLAVE_TOKENS": clave})


@pytest.mark.parametrize("modo", ["x-forwarded-for:0", "x-forwarded-for", "con espacios", ""])
def test_ip_cliente_invalida(modo):
    with pytest.raises(ValidationError, match="IP_CLIENTE"):
        Settings(_env_file=None, **BASE | {"IP_CLIENTE": modo})
