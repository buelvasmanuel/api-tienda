"""Refresh con rotación, reintentos, detección de reuso, logout y cambio de
contraseña (docs/CONTRATO_AUTH.md §4.3-4.8 y §5)."""

import threading
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import seguridad, sesiones
from app.models import SesionRefresh, Usuario
from tests.ayudas import PASSWORD, codigo


@pytest.fixture
def sesion(client, crear_usuario):
    """Usuario con una sesión abierta: devuelve el TokensOut del login."""
    crear_usuario("jugador_1")
    respuesta = client.post("/auth/login", json={"alias": "jugador_1", "password": PASSWORD},
                            headers={"User-Agent": "Dart/3.9 (dart:io)"})
    assert respuesta.status_code == 200
    return respuesta.json()


def refrescar(client, refresh: str):
    return client.post("/auth/refresh", json={"refresh_token": refresh})


def con_access(datos: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {datos['access_token']}"}


# ---------- Rotación ----------

def test_refresh_entrega_tokens_nuevos(client, sesion):
    respuesta = refrescar(client, sesion["refresh_token"])

    assert respuesta.status_code == 200
    nuevo = respuesta.json()
    assert nuevo["refresh_token"] != sesion["refresh_token"]
    assert nuevo["usuario"] == sesion["usuario"]
    assert respuesta.headers["Cache-Control"] == "no-store"
    assert client.get("/auth/yo", headers=con_access(nuevo)).status_code == 200


def test_la_cadena_de_refresh_sigue_funcionando(client, sesion, reloj):
    refresh = sesion["refresh_token"]
    for _ in range(5):
        reloj.avanzar(minutes=15)
        respuesta = refrescar(client, refresh)
        assert respuesta.status_code == 200
        refresh = respuesta.json()["refresh_token"]


def test_la_rotacion_mantiene_la_familia(client, sesion, db_session):
    refrescar(client, sesion["refresh_token"])

    familias = db_session.scalars(select(SesionRefresh.familia_id)).all()
    assert len(familias) == 2 and len(set(familias)) == 1


# ---------- Reintento dentro de la ventana de 30 s (refresh idempotente) ----------

def test_repetir_el_refresh_devuelve_el_mismo_sucesor(client, sesion, reloj):
    primera = refrescar(client, sesion["refresh_token"]).json()

    reloj.avanzar(seconds=30)
    segunda = refrescar(client, sesion["refresh_token"])

    assert segunda.status_code == 200
    assert segunda.json()["refresh_token"] == primera["refresh_token"]
    # El sucesor sigue funcionando: la sesión no se ha roto.
    assert refrescar(client, primera["refresh_token"]).status_code == 200


def test_reintento_tras_la_ventana_es_reuso(client, sesion, reloj, db_session):
    primera = refrescar(client, sesion["refresh_token"]).json()

    reloj.avanzar(seconds=31)
    reuso = refrescar(client, sesion["refresh_token"])

    assert reuso.status_code == 401
    assert codigo(reuso) == "sesion_revocada"
    # Se revoca la familia entera: el sucesor tampoco sirve ya.
    assert codigo(refrescar(client, primera["refresh_token"])) == "sesion_revocada"
    motivos = set(db_session.scalars(select(SesionRefresh.motivo_revocacion)).all())
    assert motivos == {"reuso"}


def test_reintento_cuando_el_sucesor_ya_se_uso_es_reuso(client, sesion):
    b = refrescar(client, sesion["refresh_token"]).json()["refresh_token"]
    c = refrescar(client, b).json()["refresh_token"]

    reuso = refrescar(client, sesion["refresh_token"])  # A otra vez, aún dentro de 30 s

    assert codigo(reuso) == "sesion_revocada"
    assert codigo(refrescar(client, c)) == "sesion_revocada"


def test_refresh_robado_y_usado_primero_por_el_atacante(client, sesion, reloj):
    robado = sesion["refresh_token"]
    del_atacante = refrescar(client, robado).json()["refresh_token"]

    reloj.avanzar(minutes=10)
    legitimo = refrescar(client, robado)  # el cliente legítimo refresca más tarde

    assert codigo(legitimo) == "sesion_revocada"
    assert codigo(refrescar(client, del_atacante)) == "sesion_revocada"


def test_dos_refresh_simultaneos_reciben_el_mismo_sucesor(engine):
    """Concurrencia real: dos conexiones a la vez con el mismo refresh.

    Fuera de la transacción del test (cada hilo necesita su propia conexión y
    ver los commits del otro), así que limpia lo que crea al final.
    """
    with Session(engine) as db:
        usuario = Usuario(alias="concurrente", hash_password="$argon2id$x")
        db.add(usuario)
        db.flush()
        refresh = sesiones.abrir(db, usuario, None).refresh
        db.commit()
        usuario_id = usuario.id

    barrera = threading.Barrier(2)
    resultados, errores = [], []

    def refrescar_en_hilo():
        try:
            with Session(engine) as db:
                barrera.wait()
                _, emitido = sesiones.rotar(db, refresh, None)
                resultados.append(emitido.refresh)
                db.commit()
        except Exception as e:  # noqa: BLE001  (se comprueba abajo)
            errores.append(e)

    hilos = [threading.Thread(target=refrescar_en_hilo) for _ in range(2)]
    try:
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(timeout=30)

        assert errores == []
        assert len(resultados) == 2 and resultados[0] == resultados[1]
        with Session(engine) as db:
            filas = db.scalars(select(SesionRefresh).where(SesionRefresh.usuario_id == usuario_id)).all()
            assert len(filas) == 2  # el original y un solo sucesor
            assert all(f.revocado_en is None for f in filas)
    finally:
        with Session(engine) as db:
            db.delete(db.get(Usuario, usuario_id))  # borra también sus sesiones (CASCADE)
            db.commit()


# ---------- Refresh rechazados ----------

@pytest.mark.parametrize("refresh", ["no-existe", "", "v4.local.abc"])
def test_refresh_desconocido(client, refresh):
    respuesta = refrescar(client, refresh)

    assert respuesta.status_code == 401
    assert codigo(respuesta) == "refresh_invalido"
    assert respuesta.headers["WWW-Authenticate"] == "Bearer"


def test_un_access_no_sirve_como_refresh(client, sesion):
    assert codigo(refrescar(client, sesion["access_token"])) == "refresh_invalido"


def test_refresh_demasiado_largo_es_422(client):
    assert refrescar(client, "x" * 513).status_code == 422


def test_el_refresh_caduca_a_los_14_dias(client, sesion, reloj):
    reloj.avanzar(days=14)

    assert codigo(refrescar(client, sesion["refresh_token"])) == "refresh_invalido"


def test_la_sesion_dura_como_maximo_30_dias(client, sesion, reloj):
    refresh = sesion["refresh_token"]
    for _ in range(2):  # días 13 y 26
        reloj.avanzar(days=13)
        datos = refrescar(client, refresh).json()
        refresh = datos["refresh_token"]

    # En el día 26 al nuevo refresh le quedan 4 días, no 14.
    assert datos["refresh_expires_in"] == 4 * 24 * 3600

    reloj.avanzar(days=4)
    assert codigo(refrescar(client, refresh)) == "refresh_invalido"


def test_refresh_de_cuenta_desactivada(client, sesion, db_session):
    usuario = db_session.get(Usuario, sesion["usuario"]["id"])
    usuario.activo = False
    db_session.flush()

    respuesta = refrescar(client, sesion["refresh_token"])

    assert respuesta.status_code == 403
    assert codigo(respuesta) == "cuenta_desactivada"


# ---------- Logout ----------

def test_logout_cierra_esa_sesion(client, sesion):
    otra = client.post("/auth/login", json={"alias": "jugador_1", "password": PASSWORD}).json()

    assert client.post("/auth/logout", json={"refresh_token": sesion["refresh_token"]}).status_code == 204

    assert codigo(refrescar(client, sesion["refresh_token"])) == "sesion_revocada"
    assert refrescar(client, otra["refresh_token"]).status_code == 200  # las demás siguen


def test_logout_cierra_tambien_los_refresh_ya_rotados(client, sesion):
    nuevo = refrescar(client, sesion["refresh_token"]).json()

    client.post("/auth/logout", json={"refresh_token": sesion["refresh_token"]})

    assert codigo(refrescar(client, nuevo["refresh_token"])) == "sesion_revocada"


@pytest.mark.parametrize("refresh", ["no-existe", ""])
def test_logout_siempre_204(client, refresh):
    assert client.post("/auth/logout", json={"refresh_token": refresh}).status_code == 204


def test_logout_todo(client, sesion):
    otra = client.post("/auth/login", json={"alias": "jugador_1", "password": PASSWORD}).json()

    assert client.post("/auth/logout-todo", headers=con_access(sesion)).status_code == 204

    for datos in (sesion, otra):
        assert codigo(refrescar(client, datos["refresh_token"])) == "sesion_revocada"
        assert codigo(client.get("/auth/yo", headers=con_access(datos))) == "sesion_cerrada"
    nueva = client.post("/auth/login", json={"alias": "jugador_1", "password": PASSWORD}).json()
    assert client.get("/auth/yo", headers=con_access(nueva)).status_code == 200


def test_logout_todo_exige_token(client):
    assert codigo(client.post("/auth/logout-todo")) == "no_autenticado"


# ---------- Sesiones abiertas ----------

def test_listar_sesiones(client, sesion, reloj):
    reloj.avanzar(minutes=1)
    otra = client.post("/auth/login", json={"alias": "jugador_1", "password": PASSWORD},
                       headers={"User-Agent": "Mozilla/5.0"}).json()
    refrescar(client, sesion["refresh_token"])  # rotar no crea otra sesión

    lista = client.get("/auth/sesiones", headers=con_access(otra)).json()

    assert len(lista) == 2
    assert [s["dispositivo"] for s in lista] == ["testclient", "Mozilla/5.0"]  # la más reciente primero
    assert [s["actual"] for s in lista] == [False, True]
    assert all(s["iniciada_en"].endswith("Z") and len(s["id"]) == 32 for s in lista)


def test_las_sesiones_cerradas_no_aparecen(client, sesion):
    client.post("/auth/logout", json={"refresh_token": sesion["refresh_token"]})
    otra = client.post("/auth/login", json={"alias": "jugador_1", "password": PASSWORD}).json()

    assert len(client.get("/auth/sesiones", headers=con_access(otra)).json()) == 1


def test_las_familias_caducadas_se_borran_al_abrir_sesion(client, sesion, reloj, db_session):
    reloj.avanzar(days=31)

    client.post("/auth/login", json={"alias": "jugador_1", "password": PASSWORD})

    assert len(db_session.scalars(select(SesionRefresh)).all()) == 1


# ---------- Cambio de contraseña ----------

def cambiar(client, datos, actual=PASSWORD, nueva="otra-clave-segura-456"):
    return client.put("/auth/password", headers=con_access(datos),
                      json={"password_actual": actual, "password_nueva": nueva})


def test_cambiar_password(client, sesion):
    respuesta = cambiar(client, sesion)

    assert respuesta.status_code == 200
    nueva = respuesta.json()
    # Las sesiones anteriores se cierran (refresh y access)...
    assert codigo(refrescar(client, sesion["refresh_token"])) == "sesion_revocada"
    assert codigo(client.get("/auth/yo", headers=con_access(sesion))) == "sesion_cerrada"
    # ...y la nueva funciona.
    assert client.get("/auth/yo", headers=con_access(nueva)).status_code == 200
    login = client.post("/auth/login", json={"alias": "jugador_1", "password": "otra-clave-segura-456"})
    assert login.status_code == 200


def test_cambiar_password_con_la_actual_mal(client, sesion):
    respuesta = cambiar(client, sesion, actual="no-es-la-clave")

    assert respuesta.status_code == 403
    assert codigo(respuesta) == "password_actual_incorrecta"


def test_cambiar_password_cuenta_para_el_bloqueo(client, sesion):
    for _ in range(5):
        cambiar(client, sesion, actual="no-es-la-clave")

    assert cambiar(client, sesion).status_code == 429


@pytest.mark.parametrize("nueva", ["corta", "JUGADOR_1"])
def test_cambiar_password_invalida(client, sesion, nueva):
    respuesta = cambiar(client, sesion, nueva=nueva)

    assert respuesta.status_code == 422
    assert nueva not in respuesta.text  # no se repite la contraseña en la respuesta


def test_cambio_de_password_no_deja_access_viejos_por_un_empate_de_reloj(client, sesion, reloj):
    # Con el reloj quieto, el access nuevo tiene el mismo iat que el corte:
    # debe valer (el corte rechaza solo los emitidos ANTES).
    nueva = cambiar(client, sesion).json()

    assert client.get("/auth/yo", headers=con_access(nueva)).status_code == 200


def test_sucesor_guardado_es_el_derivado(client, sesion, db_session):
    nuevo = refrescar(client, sesion["refresh_token"]).json()["refresh_token"]

    assert nuevo == seguridad.sucesor_refresh(sesion["refresh_token"])
    hashes = set(db_session.scalars(select(SesionRefresh.hash_token)).all())
    assert seguridad.hash_refresh(nuevo) in hashes


def test_el_sucesor_no_dura_mas_alla_de_14_dias_desde_su_emision(client, sesion, reloj, db_session):
    reloj.avanzar(days=1)
    refrescar(client, sesion["refresh_token"])

    ultima = db_session.scalars(select(SesionRefresh).order_by(SesionRefresh.id.desc())).first()
    assert ultima.expira_en - ultima.creado_en == timedelta(days=14)
