"""Logs estructurados con id de petición y sin datos personales (app/logs.py)."""

import io
import json
import logging

import pytest
from fastapi.testclient import TestClient

from app import logs
from app.config import settings
from app.main import app
from tests.ayudas import PASSWORD, cabeceras


@pytest.fixture
def lineas():
    """Captura lo que se escribe en el log, ya en JSON (con el mismo formatter
    que en producción). Devuelve una función que da las líneas parseadas."""
    salida = io.StringIO()
    manejador = logging.StreamHandler(salida)
    manejador.setFormatter(logs.formateador())
    raiz = logging.getLogger()
    raiz.addHandler(manejador)
    yield lambda: [json.loads(l) for l in salida.getvalue().splitlines() if l.strip()]
    raiz.removeHandler(manejador)


# ---------- X-Request-ID ----------

@pytest.mark.parametrize(("metodo", "ruta"), [("GET", "/"), ("GET", "/estudiantes/999"), ("POST", "/auth/login"),
                                             ("GET", "/auth/yo"), ("GET", "/no-existe")])
def test_toda_respuesta_lleva_x_request_id(client, metodo, ruta):
    respuesta = client.request(metodo, ruta, json={})

    assert len(respuesta.headers["X-Request-ID"]) == 32


def test_cada_peticion_tiene_su_id(client):
    ids = {client.get("/").headers["X-Request-ID"] for _ in range(5)}

    assert len(ids) == 5


def test_se_reutiliza_un_id_valido_del_cliente(client):
    assert client.get("/", headers={"X-Request-ID": "app-1234-abcd"}).headers["X-Request-ID"] == "app-1234-abcd"


@pytest.mark.parametrize("enviado", ["corto", "con espacios 123456", "x" * 65, "inyeccion\"falsa=1", "a;b=c-123456"])
def test_un_id_invalido_se_sustituye(client, enviado):
    devuelto = client.get("/", headers={"X-Request-ID": enviado}).headers["X-Request-ID"]

    assert devuelto != enviado and len(devuelto) == 32


def test_el_preflight_de_cors_y_los_413_tambien(client):
    preflight = client.options("/auth/login", headers={"Origin": "https://app.ejemplo.org",
                                                       "Access-Control-Request-Method": "POST"})
    grande = client.post("/auth/login", content=b"x" * (70 * 1024), headers={"Content-Type": "application/json"})

    assert "X-Request-ID" in preflight.headers and "X-Request-ID" in grande.headers


def test_el_500_lleva_id_y_su_traza_va_al_log_con_ese_id(client, lineas):
    from app.database import get_db

    def roto():
        raise RuntimeError("fallo con Bearer v4.local.secreto123 dentro")

    app.dependency_overrides[get_db] = roto
    try:
        respuesta = TestClient(app, raise_server_exceptions=False).get("/estudiantes/")
    finally:
        app.dependency_overrides.clear()

    id_ = respuesta.headers["X-Request-ID"]
    error = next(l for l in lineas() if l["event"] == "error_no_controlado")
    assert error["request_id"] == id_ and error["level"] == "error"
    assert "RuntimeError" in error["exception"]
    assert "secreto123" not in json.dumps(lineas())  # el token del mensaje de la excepción se borra


# ---------- Formato de las líneas ----------

def test_linea_de_acceso_por_peticion(client, lineas):
    respuesta = client.get("/estudiantes/999")

    acceso = [l for l in lineas() if l["event"] == "peticion"][-1]
    assert acceso["request_id"] == respuesta.headers["X-Request-ID"]
    assert (acceso["metodo"], acceso["ruta"], acceso["estado"], acceso["logger"]) == ("GET", "/estudiantes/999", 404, "app.acceso")
    assert isinstance(acceso["ms"], float) and acceso["momento"].endswith("Z")


def test_los_logs_de_dentro_de_la_peticion_llevan_su_id(client, crear_usuario, lineas):
    usuario = crear_usuario("jugador_1")
    for _ in range(5):
        respuesta = client.post("/auth/login", json={"alias": "jugador_1", "password": "intento-fallido"})

    bloqueo = next(l for l in lineas() if l["event"] == "cuenta_bloqueada")
    assert bloqueo["request_id"] == respuesta.headers["X-Request-ID"]
    assert (bloqueo["usuario_id"], bloqueo["fallos"], bloqueo["segundos"]) == (usuario.id, 5, 30)


# ---------- Sin datos personales ni secretos ----------

def test_un_flujo_de_sesion_no_deja_secretos_ni_alias_en_los_logs(client, lineas):
    datos = client.post("/auth/registro", json={"alias": "Alias_Secreto", "password": PASSWORD}).json()
    client.post("/auth/login", json={"alias": "Alias_Secreto", "password": "otra-clave-mala"})
    nuevo = client.post("/auth/refresh", json={"refresh_token": datos["refresh_token"]}).json()
    client.get("/auth/yo", headers={"Authorization": f"Bearer {nuevo['access_token']}"})

    texto = json.dumps(lineas(), ensure_ascii=False)
    for secreto in (PASSWORD, "otra-clave-mala", datos["access_token"], datos["refresh_token"],
                    nuevo["access_token"], nuevo["refresh_token"], "Alias_Secreto"):
        assert secreto not in texto
    assert any(l["event"] == "registro" and l["usuario_id"] == datos["usuario"]["id"] for l in lineas())


def test_las_ips_se_registran_como_hash(client, monkeypatch, lineas):
    from app import limites

    monkeypatch.setattr(settings, "IP_CLIENTE", "x-forwarded-for:1")
    limites.limitador.activo = True
    for i in range(31):
        client.post("/auth/login", json={"alias": f"nadie_{i}", "password": "x"},
                    headers={"X-Forwarded-For": "198.51.100.1, 203.0.113.7"})

    evento = next(l for l in lineas() if l["event"] == "demasiados_logins_fallidos")
    assert evento["ip_hash"] == logs.hash_ip("203.0.113.7")
    assert evento["x_forwarded_for"] == [logs.hash_ip("198.51.100.1"), logs.hash_ip("203.0.113.7")]
    assert "203.0.113.7" not in json.dumps(lineas()) and "198.51.100.1" not in json.dumps(lineas())


def test_el_hash_de_ip_es_estable_y_no_es_la_ip():
    assert logs.hash_ip("203.0.113.7") == logs.hash_ip("203.0.113.7") != logs.hash_ip("203.0.113.8")
    assert len(logs.hash_ip("203.0.113.7")) == 12 and logs.hash_ip(None) is None


def test_los_mensajes_de_otras_librerias_tambien_se_limpian(lineas):
    # Como el de uvicorn al aceptar un WebSocket, que lleva la IP del cliente.
    logging.getLogger("uvicorn.error").info('%s - "WebSocket %s" [accepted]', "203.0.113.7:0", "/duelos/x/ws")

    linea = lineas()[-1]
    assert "color_message" not in linea
    assert "203.0.113.7" not in linea["event"]
    assert f"ip:{logs.hash_ip('203.0.113.7')}" in linea["event"]


@pytest.mark.parametrize(
    ("entrada", "salida"),
    [("token v4.local.abc_DEF-123.xyz", "token [token]"), ("Authorization: Bearer abc.def", "Authorization: Bearer [token]"),
     ("desde 10.0.0.1", f"desde ip:{logs.hash_ip('10.0.0.1')}"), ("sin nada raro", "sin nada raro")],
)
def test_limpiar_textos(entrada, salida):
    assert logs.limpiar(entrada) == salida


def test_los_campos_sensibles_se_ocultan(lineas):
    logs.obtener("app.prueba").info("prueba", password="no-debe-salir", alias="Ana_1", token="t", usuario_id=7)

    linea = lineas()[-1]
    assert (linea["password"], linea["alias"], linea["token"], linea["usuario_id"]) == ("[oculto]", "[oculto]", "[oculto]", 7)


# ---------- WebSocket ----------

def test_el_websocket_tiene_su_propio_id_en_los_logs(client, crear_usuario, lineas, db_session):
    from tests.test_duelos import estudiante

    db_session.add_all(estudiante(i) for i in range(12))
    db_session.flush()
    usuario = crear_usuario("ws_logs")
    http = TestClient(app, headers=cabeceras(usuario))
    duelo = http.post("/duelos", json={"modo": "jugador"}).json()

    with http.websocket_connect(f"/duelos/{duelo['id']}/ws", headers={"X-Request-ID": "ws-conexion-0001"}) as ws:
        ws.send_json({"tipo": "auth", "token": cabeceras(usuario)["Authorization"][7:]})
        assert ws.receive_json()["tipo"] == "listo"

    autenticado = next(l for l in lineas() if l["event"] == "ws_autenticado")
    assert autenticado["request_id"] == "ws-conexion-0001" and autenticado["usuario_id"] == usuario.id
