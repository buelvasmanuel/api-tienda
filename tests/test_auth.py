"""Registro, login, /auth/yo y permisos por rol (docs/CONTRATO_AUTH.md §4)."""

from datetime import timedelta

import pytest
from argon2 import PasswordHasher
from sqlalchemy import select

from app import seguridad
from app.models import SesionRefresh, Usuario
from app.routers.auth import duracion_bloqueo
from tests.ayudas import PASSWORD, cabeceras, codigo

ESTUDIANTE = {
    "nombre": "Ana", "apellido": "Restrepo", "edad": 20, "carrera": "Derecho", "semestre": 3,
    "promedio": "4.10", "ciudad": "Medellín", "horas_estudio": 12, "asistencia": 90,
}


def registrar(client, alias="Detective_Ana", password=PASSWORD, **extra):
    return client.post("/auth/registro", json={"alias": alias, "password": password} | extra)


def login(client, alias="jugador_1", password=PASSWORD):
    return client.post("/auth/login", json={"alias": alias, "password": password})


# ---------- Registro ----------

def test_registro_crea_jugador_y_abre_sesion(client):
    respuesta = registrar(client)

    assert respuesta.status_code == 201
    datos = respuesta.json()
    assert datos["usuario"] == {"id": datos["usuario"]["id"], "alias": "Detective_Ana", "rol": "jugador"}
    assert (datos["token_type"], datos["expires_in"]) == ("bearer", 900)
    assert datos["refresh_expires_in"] == 14 * 24 * 3600
    assert respuesta.headers["Cache-Control"] == "no-store"

    yo = client.get("/auth/yo", headers={"Authorization": f"Bearer {datos['access_token']}"})
    assert yo.json() == datos["usuario"]


def test_registro_no_guarda_secretos_en_claro(client, db_session):
    datos = registrar(client).json()

    usuario = db_session.scalar(select(Usuario))
    sesion = db_session.scalar(select(SesionRefresh))
    assert usuario.hash_password.startswith("$argon2id$")
    assert PASSWORD not in usuario.hash_password
    assert sesion.hash_token == seguridad.hash_refresh(datos["refresh_token"])
    assert datos["refresh_token"] not in sesion.hash_token


@pytest.mark.parametrize("alias", ["Detective_Ana", "detective_ana", "DETECTIVE_ANA"])
def test_alias_repetido_sin_distinguir_mayusculas(client, alias):
    registrar(client, "Detective_Ana")

    respuesta = registrar(client, alias)

    assert respuesta.status_code == 409
    assert codigo(respuesta) == "alias_en_uso"


@pytest.mark.parametrize(
    "cuerpo",
    [{"alias": "ab"}, {"alias": "con espacio"}, {"alias": "tildé"}, {"alias": "x" * 31},
     {"password": "corta"}, {"password": "x" * 129},
     {"alias": "Ana_Maria_12", "password": "ana_maria_12"},  # igual al alias
     {"rol": "admin"}, {"activo": True}],  # asignación masiva: campos que no se aceptan
    ids=["alias-corto", "alias-espacio", "alias-tilde", "alias-largo", "password-corta",
         "password-larga", "password-igual-alias", "rol", "activo"],
)
def test_registro_invalido_devuelve_422(client, db_session, cuerpo):
    respuesta = client.post("/auth/registro", json={"alias": "Detective_Ana", "password": PASSWORD} | cuerpo)

    assert respuesta.status_code == 422
    assert db_session.scalar(select(Usuario)) is None


def test_password_admite_espacios_y_cualquier_caracter(client):
    password = "  mi clave ñandú 🦉  "

    registrar(client, "Con_Espacios", password)

    assert login(client, "Con_Espacios", password).status_code == 200
    assert login(client, "Con_Espacios", password.strip()).status_code == 401


# ---------- Login ----------

def test_login_devuelve_tokens(client, crear_usuario):
    usuario = crear_usuario("jugador_1", rol="docente")

    respuesta = login(client)

    assert respuesta.status_code == 200
    assert respuesta.json()["usuario"] == {"id": usuario.id, "alias": "jugador_1", "rol": "docente"}
    assert respuesta.headers["Cache-Control"] == "no-store"


def test_login_sin_distinguir_mayusculas_en_el_alias(client, crear_usuario):
    crear_usuario("jugador_1")

    assert login(client, "JUGADOR_1").status_code == 200


def test_cada_login_abre_una_sesion_distinta(client, crear_usuario, db_session):
    crear_usuario()

    login(client)
    login(client)

    familias = db_session.scalars(select(SesionRefresh.familia_id)).all()
    assert len(set(familias)) == 2


def test_alias_inexistente_y_password_mala_son_indistinguibles(client, crear_usuario, monkeypatch):
    crear_usuario()
    llamadas = []
    original = seguridad.gastar_tiempo_de_verificacion
    monkeypatch.setattr(seguridad, "gastar_tiempo_de_verificacion",
                        lambda p: (llamadas.append(p), original(p)))

    mala = login(client, password="no-es-la-clave")
    inexistente = login(client, alias="no_existe")

    assert mala.status_code == inexistente.status_code == 401
    assert mala.json() == inexistente.json()
    assert codigo(mala) == "credenciales_invalidas"
    assert mala.headers["WWW-Authenticate"] == inexistente.headers["WWW-Authenticate"] == "Bearer"
    # Con el alias inexistente también se calcula un hash (mismo tiempo de respuesta).
    assert llamadas == ["clave-de-pruebas-123"]


def test_cuenta_desactivada(client, crear_usuario):
    crear_usuario(activo=False)

    respuesta = login(client)

    assert respuesta.status_code == 403
    assert codigo(respuesta) == "cuenta_desactivada"
    # Con la contraseña mala no se revela que la cuenta está desactivada.
    assert codigo(login(client, password="no-es-la-clave")) == "credenciales_invalidas"


def test_login_rehace_el_hash_si_cambian_los_parametros(client, crear_usuario, db_session):
    usuario = crear_usuario()
    usuario.hash_password = PasswordHasher(time_cost=3, memory_cost=128, parallelism=1).hash(PASSWORD)
    db_session.flush()
    viejo = usuario.hash_password

    assert login(client).status_code == 200

    db_session.refresh(usuario)
    assert usuario.hash_password != viejo
    assert not seguridad.necesita_rehash(usuario.hash_password)
    assert login(client).status_code == 200


# ---------- Fuerza bruta: bloqueo por cuenta ----------

@pytest.mark.parametrize(
    ("fallos", "segundos"),
    [(1, None), (4, None), (5, 30), (6, 60), (7, 60), (10, 60), (500, 60)],
)
def test_duracion_del_bloqueo(fallos, segundos):
    esperado = timedelta(seconds=segundos) if segundos else None

    assert duracion_bloqueo(fallos) == esperado


def test_fuerza_bruta_bloquea_la_cuenta(client, crear_usuario, reloj):
    crear_usuario()
    for _ in range(5):
        assert login(client, password="intento-fallido").status_code == 401

    # Bloqueada: 429 aunque la contraseña sea la correcta.
    bloqueada = login(client)
    assert bloqueada.status_code == 429
    assert codigo(bloqueada) == "demasiados_intentos"
    assert bloqueada.headers["Retry-After"] == "30"

    reloj.avanzar(seconds=10)
    assert login(client).headers["Retry-After"] == "20"

    reloj.avanzar(seconds=20)
    assert login(client).status_code == 200


def test_cada_fallo_tras_el_bloqueo_lo_duplica(client, crear_usuario, reloj):
    crear_usuario()
    for _ in range(5):
        login(client, password="intento-fallido")
    reloj.avanzar(seconds=30)

    assert login(client, password="otro-intento").status_code == 401  # 6.º fallo

    assert login(client).headers["Retry-After"] == "60"


def test_un_login_correcto_pone_el_contador_a_cero(client, crear_usuario, db_session):
    usuario = crear_usuario()
    for _ in range(4):
        login(client, password="intento-fallido")

    assert login(client).status_code == 200

    db_session.refresh(usuario)
    assert (usuario.intentos_fallidos, usuario.bloqueado_hasta) == (0, None)
    assert login(client, password="intento-fallido").status_code == 401  # no bloquea: va por 1


# ---------- /auth/yo y validación del access en cada petición ----------

@pytest.mark.parametrize("cabecera", [None, "Basic dXN1YXJpbzpjbGF2ZQ==", "Bearer", "token-sin-esquema"])
def test_sin_token_valido_devuelve_no_autenticado(client, cabecera):
    respuesta = client.get("/auth/yo", headers={"Authorization": cabecera} if cabecera else {})

    assert respuesta.status_code == 401
    assert codigo(respuesta) == "no_autenticado"
    assert respuesta.headers["WWW-Authenticate"] == "Bearer"


def test_usuario_borrado(client, crear_usuario, db_session):
    usuario = crear_usuario()
    token = cabeceras(usuario)
    db_session.delete(usuario)
    db_session.flush()

    respuesta = client.get("/auth/yo", headers=token)

    assert respuesta.status_code == 401
    assert codigo(respuesta) == "sesion_cerrada"


def test_usuario_desactivado_pierde_el_acceso_al_instante(client, crear_usuario, db_session):
    usuario = crear_usuario()
    token = cabeceras(usuario)
    usuario.activo = False
    db_session.flush()

    respuesta = client.get("/auth/yo", headers=token)

    assert respuesta.status_code == 403
    assert codigo(respuesta) == "cuenta_desactivada"


def test_access_emitido_antes_de_cerrar_las_sesiones(client, crear_usuario, db_session, reloj):
    usuario = crear_usuario()
    viejo = cabeceras(usuario)
    reloj.avanzar(microseconds=1)
    usuario.sesiones_validas_desde = reloj.momento
    db_session.flush()
    nuevo = cabeceras(usuario)

    assert codigo(client.get("/auth/yo", headers=viejo)) == "sesion_cerrada"
    assert client.get("/auth/yo", headers=nuevo).status_code == 200


def test_el_rol_se_lee_de_la_base_no_del_token(client, crear_usuario, db_session):
    usuario = crear_usuario(rol="admin")
    token = cabeceras(usuario)
    assert client.post("/estudiantes/", json=ESTUDIANTE, headers=token).status_code == 201

    usuario.rol = "jugador"
    db_session.flush()

    assert client.post("/estudiantes/", json=ESTUDIANTE, headers=token).status_code == 403


# ---------- Permisos sobre /estudiantes ----------

ESCRITURAS = [("POST", "/estudiantes/", ESTUDIANTE), ("PUT", "/estudiantes/1", ESTUDIANTE),
              ("DELETE", "/estudiantes/1", None)]


@pytest.mark.parametrize(("metodo", "ruta", "cuerpo"), ESCRITURAS, ids=["POST", "PUT", "DELETE"])
def test_escribir_sin_token_devuelve_401(client, metodo, ruta, cuerpo):
    respuesta = client.request(metodo, ruta, json=cuerpo)

    assert respuesta.status_code == 401
    assert codigo(respuesta) == "no_autenticado"


@pytest.mark.parametrize("rol", ["jugador", "docente"])
@pytest.mark.parametrize(("metodo", "ruta", "cuerpo"), ESCRITURAS, ids=["POST", "PUT", "DELETE"])
def test_escribir_sin_ser_admin_devuelve_403(client, crear_usuario, rol, metodo, ruta, cuerpo):
    usuario = crear_usuario(rol=rol)

    respuesta = client.request(metodo, ruta, json=cuerpo, headers=cabeceras(usuario))

    assert respuesta.status_code == 403
    assert codigo(respuesta) == "permiso_denegado"


def test_sin_token_no_se_valida_el_cuerpo(client):
    # Primero la autenticación: un anónimo no recibe pistas sobre el esquema.
    assert client.post("/estudiantes/", json={"basura": 1}).status_code == 401


def test_leer_sigue_siendo_publico(client, admin_client):
    creado = admin_client.post("/estudiantes/", json=ESTUDIANTE).json()

    assert client.get("/estudiantes/").json() == [creado]
    assert client.get(f"/estudiantes/{creado['id']}").status_code == 200


# ---------- Los endpoints públicos ignoran Authorization (contrato §4.10) ----------

@pytest.mark.parametrize(
    ("metodo", "ruta", "cuerpo"),
    [("GET", "/", None), ("GET", "/estudiantes/", None),
     ("GET", "/estudiantes/estadisticas/por-carrera", None),
     ("POST", "/auth/login", {"alias": "jugador_1", "password": PASSWORD}),
     ("POST", "/auth/refresh", {"refresh_token": "x"})],
    ids=["raiz", "estudiantes", "estadisticas", "login", "refresh"],
)
@pytest.mark.parametrize("cabecera", ["Bearer token-basura", "Bearer v4.local.caducado", "Basic abc"])
def test_publicos_ignoran_un_token_invalido(client, crear_usuario, metodo, ruta, cuerpo, cabecera):
    crear_usuario("jugador_1")

    con = client.request(metodo, ruta, json=cuerpo, headers={"Authorization": cabecera})
    sin = client.request(metodo, ruta, json=cuerpo)

    # Misma respuesta con y sin la cabecera. (/auth/refresh da 401, pero por el
    # refresh del cuerpo, no por la cabecera.)
    assert con.status_code == sin.status_code
    if con.status_code == 401:
        assert codigo(con) == codigo(sin) == "refresh_invalido"


def test_publicos_ignoran_un_token_caducado(client, crear_usuario, reloj):
    usuario = crear_usuario()
    viejo = cabeceras(usuario)
    reloj.avanzar(hours=1)

    assert client.get("/estudiantes/", headers=viejo).status_code == 200


def test_registro_cerrado(client, crear_usuario, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "REGISTRO_ABIERTO", False)
    crear_usuario("jugador_1")

    respuesta = registrar(client)

    assert respuesta.status_code == 403
    assert codigo(respuesta) == "registro_cerrado"
    assert login(client).status_code == 200  # el login sigue funcionando
