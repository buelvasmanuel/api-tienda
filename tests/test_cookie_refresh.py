"""Refresh token en cookie Partitioned para la web (docs/CONTRATO_AUTH.md §6.3).

El TestClient habla por http://testserver y su almacén de cookies no enviaría
una cookie Secure: la cookie se lee del Set-Cookie y se envía a mano.
"""

from datetime import timedelta
from http.cookies import SimpleCookie

import pytest

from app import cookie_refresh
from app.config import settings
from tests.autenticador import Autenticador
from tests.ayudas import PASSWORD, cabeceras, codigo
from tests.test_passkeys import entrar as entrar_con_passkey
from tests.test_passkeys import registrar as registrar_passkey

ORIGEN = "https://app.ejemplo.org"  # el de CORS_ORIGINS en tests/conftest.py
WEB = {"X-Refresh-En-Cookie": "1", "Origin": ORIGEN}


def cookie_de(respuesta) -> SimpleCookie:
    cookies = respuesta.headers.get_list("set-cookie")
    assert len(cookies) == 1, cookies
    galleta = SimpleCookie()
    galleta.load(cookies[0])
    return galleta


def valor_de(respuesta) -> str:
    return cookie_de(respuesta)[cookie_refresh.NOMBRE].value


def con_cookie(valor: str, **otras) -> dict[str, str]:
    return WEB | {"Cookie": f"{cookie_refresh.NOMBRE}={valor}"} | otras


def login(client, **cabeceras_extra):
    return client.post("/auth/login", json={"alias": "jugador_1", "password": PASSWORD}, headers=cabeceras_extra)


@pytest.fixture
def web(client, crear_usuario):
    """Sesión abierta desde la web en modo cookie: devuelve el valor de la cookie."""
    crear_usuario("jugador_1")
    client.cookies.clear()
    respuesta = login(client, **WEB)
    assert respuesta.status_code == 200
    return valor_de(respuesta)


# ---------- Entregar el refresh en la cookie ----------

def test_login_en_modo_cookie(client, crear_usuario):
    crear_usuario("jugador_1")

    respuesta = login(client, **WEB)

    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["refresh_token"] is None  # JavaScript no lo ve
    assert client.get("/auth/yo", headers={"Authorization": f"Bearer {datos['access_token']}"}).status_code == 200
    cruda = respuesta.headers["set-cookie"]
    galleta = cookie_de(respuesta)[cookie_refresh.NOMBRE]
    assert galleta.value
    assert galleta["httponly"] and galleta["secure"]
    assert galleta["samesite"].lower() == "none"
    assert galleta["path"] == "/"
    assert not galleta["domain"]  # __Host- lo prohíbe
    assert int(galleta["max-age"]) == datos["refresh_expires_in"]
    assert "Partitioned" in cruda  # SimpleCookie no conoce el atributo


def test_sin_la_cabecera_todo_sigue_igual(client, crear_usuario):
    """El APK y las apps de escritorio: refresh en el cuerpo, sin cookies."""
    crear_usuario("jugador_1")

    respuesta = login(client)

    assert respuesta.status_code == 200
    assert respuesta.json()["refresh_token"]
    assert "set-cookie" not in respuesta.headers


@pytest.mark.parametrize("valor", ["0", "true", "si"])
def test_la_cabecera_solo_vale_con_1(client, crear_usuario, valor):
    crear_usuario("jugador_1")

    respuesta = login(client, **WEB | {"X-Refresh-En-Cookie": valor})

    assert respuesta.json()["refresh_token"]
    assert "set-cookie" not in respuesta.headers


def test_registro_en_modo_cookie(client):
    respuesta = client.post("/auth/registro", json={"alias": "Nueva_Web", "password": PASSWORD}, headers=WEB)

    assert respuesta.status_code == 201
    assert respuesta.json()["refresh_token"] is None
    assert valor_de(respuesta)


def test_cambiar_password_en_modo_cookie(client, web):
    access = client.post("/auth/refresh", json={}, headers=con_cookie(web)).json()["access_token"]

    respuesta = client.put("/auth/password", json={"password_actual": PASSWORD, "password_nueva": "otra-clave-larga-1"},
                           headers=WEB | {"Authorization": f"Bearer {access}"})

    assert respuesta.status_code == 200
    assert respuesta.json()["refresh_token"] is None
    assert client.post("/auth/refresh", json={}, headers=con_cookie(valor_de(respuesta))).status_code == 200


def test_login_con_passkey_en_modo_cookie(client, crear_usuario):
    usuario = crear_usuario("Detective_Ana")
    autenticador = Autenticador()
    client.headers.update(cabeceras(usuario))
    assert registrar_passkey(client, autenticador).status_code == 201
    client.headers.update(WEB)

    respuesta = entrar_con_passkey(client, autenticador)

    assert respuesta.status_code == 200
    assert respuesta.json()["refresh_token"] is None
    assert valor_de(respuesta)


# ---------- Refresh con la cookie ----------

def test_refresh_con_la_cookie(client, web):
    respuesta = client.post("/auth/refresh", json={}, headers=con_cookie(web))

    assert respuesta.status_code == 200
    assert respuesta.json()["refresh_token"] is None
    nueva = valor_de(respuesta)
    assert nueva != web
    assert client.post("/auth/refresh", json={}, headers=con_cookie(nueva)).status_code == 200


def test_dos_pestanas_a_la_vez_reciben_la_misma_cookie(client, web):
    """La ventana idempotente de 120 s también vale con la cookie."""
    primera = client.post("/auth/refresh", json={}, headers=con_cookie(web))
    segunda = client.post("/auth/refresh", json={}, headers=con_cookie(web))

    assert valor_de(primera) == valor_de(segunda)


def test_reusar_una_cookie_vieja_cierra_la_sesion_y_la_borra(client, web, reloj):
    nueva = valor_de(client.post("/auth/refresh", json={}, headers=con_cookie(web)))
    reloj.avanzar(seconds=121)

    respuesta = client.post("/auth/refresh", json={}, headers=con_cookie(web))

    assert respuesta.status_code == 401
    assert codigo(respuesta) == "sesion_revocada"
    assert cookie_de(respuesta)[cookie_refresh.NOMBRE]["max-age"] == "0"
    assert client.post("/auth/refresh", json={}, headers=con_cookie(nueva)).status_code == 401


def test_refresh_en_modo_cookie_sin_cookie(client):
    respuesta = client.post("/auth/refresh", json={}, headers=WEB)

    assert respuesta.status_code == 401
    assert codigo(respuesta) == "refresh_invalido"
    assert respuesta.headers["WWW-Authenticate"] == "Bearer"


def test_cookie_basura(client):
    for valor in ["no-es-un-refresh", "x" * 600]:
        respuesta = client.post("/auth/refresh", json={}, headers=con_cookie(valor))
        assert respuesta.status_code == 401
        assert codigo(respuesta) == "refresh_invalido"


def test_sin_la_cabecera_la_cookie_se_ignora(client, web):
    """Sin X-Refresh-En-Cookie el refresh sigue siendo obligatorio en el cuerpo."""
    respuesta = client.post("/auth/refresh", json={}, headers={"Cookie": f"{cookie_refresh.NOMBRE}={web}"})

    assert respuesta.status_code == 422


def test_el_refresh_del_cuerpo_sigue_funcionando(client, crear_usuario):
    crear_usuario("jugador_1")
    refresh = login(client).json()["refresh_token"]

    respuesta = client.post("/auth/refresh", json={"refresh_token": refresh})

    assert respuesta.status_code == 200
    assert respuesta.json()["refresh_token"]
    assert "set-cookie" not in respuesta.headers


# ---------- Defensas contra CSRF ----------

@pytest.mark.parametrize("origen", [None, "https://otra-web.example", "null", "https://app.ejemplo.org.evil.example"])
def test_modo_cookie_solo_desde_un_origen_permitido(client, web, origen):
    cab = con_cookie(web)
    del cab["Origin"]
    if origen:
        cab["Origin"] = origen

    for ruta in ["/auth/refresh", "/auth/logout"]:
        respuesta = client.post(ruta, json={}, headers=cab)
        assert respuesta.status_code == 403
        assert codigo(respuesta) == "origen_no_permitido"
    assert client.post("/auth/refresh", json={}, headers=con_cookie(web)).status_code == 200  # la sesión sigue


@pytest.mark.parametrize("tipo", ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x"])
def test_modo_cookie_exige_json(client, web, tipo):
    for ruta in ["/auth/refresh", "/auth/logout"]:
        respuesta = client.post(ruta, content=b"{}", headers=con_cookie(web, **{"Content-Type": tipo}))
        assert respuesta.status_code == 415
        assert codigo(respuesta) == "json_obligatorio"


def test_cuerpo_json_sin_content_type(client, web):
    """FastAPI lee como JSON un cuerpo sin Content-Type (p. ej. sendBeacon con
    un Blob sin tipo): en modo cookie se rechaza igual."""
    respuesta = client.post("/auth/refresh", content=b"{}", headers=con_cookie(web))

    assert respuesta.status_code == 415


def test_con_cors_abierto_no_hay_modo_cookie(client, web, monkeypatch):
    monkeypatch.setattr(settings, "CORS_ORIGINS", "*")

    respuesta = client.post("/auth/refresh", json={}, headers=con_cookie(web))

    assert respuesta.status_code == 403
    assert codigo(respuesta) == "origen_no_permitido"


def test_preflight_permite_la_cabecera_y_las_credenciales(client):
    respuesta = client.options("/auth/refresh", headers={
        "Origin": ORIGEN, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-refresh-en-cookie"})

    assert respuesta.status_code == 200
    assert respuesta.headers["Access-Control-Allow-Origin"] == ORIGEN
    assert respuesta.headers["Access-Control-Allow-Credentials"] == "true"
    assert "x-refresh-en-cookie" in respuesta.headers["Access-Control-Allow-Headers"].lower()


def test_preflight_de_otro_origen_se_rechaza(client):
    respuesta = client.options("/auth/refresh", headers={
        "Origin": "https://otra-web.example", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-refresh-en-cookie"})

    assert respuesta.status_code == 400
    assert "Access-Control-Allow-Origin" not in respuesta.headers


def test_cors_con_asterisco_no_permite_credenciales():
    """Con CORS_ORIGINS="*", ningún origen recibe cookies (ver app/main.py)."""
    from app.config import Settings

    abierta = Settings(DB_USER="u", DB_PASSWORD="p", DB_NAME="d", CORS_ORIGINS="*",
                       CLAVE_TOKENS=settings.CLAVE_TOKENS)
    explicita = Settings(DB_USER="u", DB_PASSWORD="p", DB_NAME="d", CORS_ORIGINS="http://localhost:8080, *",
                         CLAVE_TOKENS=settings.CLAVE_TOKENS)

    assert abierta.origenes_con_credenciales == []
    assert explicita.origenes_con_credenciales == []


# ---------- Cerrar sesión ----------

def test_logout_con_la_cookie(client, web):
    respuesta = client.post("/auth/logout", json={}, headers=con_cookie(web))

    assert respuesta.status_code == 204
    assert cookie_de(respuesta)[cookie_refresh.NOMBRE]["max-age"] == "0"
    assert "Partitioned" in respuesta.headers["set-cookie"]  # si no, el navegador no la borra
    assert client.post("/auth/refresh", json={}, headers=con_cookie(web)).status_code == 401


def test_logout_en_modo_cookie_sin_cookie(client):
    respuesta = client.post("/auth/logout", json={}, headers=WEB)

    assert respuesta.status_code == 204
    assert cookie_de(respuesta)[cookie_refresh.NOMBRE]["max-age"] == "0"


def test_logout_sin_cabecera_sigue_exigiendo_el_refresh(client):
    assert client.post("/auth/logout", json={}).status_code == 422


def test_logout_todo_borra_la_cookie(client, web):
    access = client.post("/auth/refresh", json={}, headers=con_cookie(web)).json()["access_token"]

    respuesta = client.post("/auth/logout-todo", headers=WEB | {"Authorization": f"Bearer {access}"})

    assert respuesta.status_code == 204
    assert cookie_de(respuesta)[cookie_refresh.NOMBRE]["max-age"] == "0"


def test_la_cookie_dura_lo_que_el_refresh(client, web, reloj):
    reloj.avanzar(days=1)

    respuesta = client.post("/auth/refresh", json={}, headers=con_cookie(web))

    restante = respuesta.json()["refresh_expires_in"]
    assert int(cookie_de(respuesta)[cookie_refresh.NOMBRE]["max-age"]) == restante
    assert restante <= timedelta(days=14).total_seconds()
