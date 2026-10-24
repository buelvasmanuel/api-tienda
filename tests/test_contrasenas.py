"""Contraseñas demasiado comunes (auditoría C1): 422 password_comun al
fijarlas; el login de quien ya la tenía sigue funcionando."""

import pytest

from app import seguridad
from app.contrasenas import _COMUNES, es_comun
from tests.ayudas import PASSWORD, cabeceras, codigo

COMUNES = ["1234567890", "qwertyuiop", "iloveyou12", "contraseña123", "Universidad1", "PASSWORD123"]


def test_la_lista_se_carga():
    assert len(_COMUNES) > 9000
    assert all(10 <= len(p) <= 128 and p == p.lower() for p in _COMUNES)


@pytest.mark.parametrize("password", COMUNES)
def test_son_comunes_sin_distinguir_mayusculas(password):
    assert es_comun(password)


@pytest.mark.parametrize("password", [PASSWORD, "k7mq-x3hp-9tfa", "Mi gato Bigotes 2026"])
def test_no_son_comunes(password):
    assert not es_comun(password)


def test_las_contrasenas_generadas_para_el_lote_nunca_son_comunes():
    assert not any(es_comun(seguridad.generar_password()) for _ in range(200))


def comprobar(respuesta, campo, password):
    assert respuesta.status_code == 422
    assert codigo(respuesta) == "password_comun"
    assert respuesta.json()["detail"]["campo"] == campo
    assert password.lower() not in respuesta.text.lower()  # no se repite la contraseña


@pytest.mark.parametrize("password", COMUNES)
def test_registro_con_una_contrasena_comun(client, password):
    respuesta = client.post("/auth/registro", json={"alias": "Nuevo_1", "password": password})

    comprobar(respuesta, "password", password)
    assert client.post("/auth/login", json={"alias": "Nuevo_1", "password": password}).status_code == 401


def test_cambiar_a_una_contrasena_comun(client, crear_usuario):
    usuario = crear_usuario("jugador_1")

    respuesta = client.put("/auth/password", json={"password_actual": PASSWORD, "password_nueva": "qwertyuiop"},
                           headers=cabeceras(usuario))

    comprobar(respuesta, "password_nueva", "qwertyuiop")
    assert client.post("/auth/login", json={"alias": "jugador_1", "password": PASSWORD}).status_code == 200


def test_el_admin_tampoco_puede_poner_una_comun(client, crear_usuario):
    admin, jugador = crear_usuario("admin_1", rol="admin"), crear_usuario("jugador_1")

    respuesta = client.put(f"/usuarios/{jugador.id}/password", json={"password_nueva": "1234567890"},
                           headers=cabeceras(admin))

    comprobar(respuesta, "password_nueva", "1234567890")


def test_quien_ya_la_tenia_sigue_entrando(client, crear_usuario):
    crear_usuario("Veterano_1", password="1234567890")  # creada antes de existir la lista

    assert client.post("/auth/login", json={"alias": "Veterano_1", "password": "1234567890"}).status_code == 200


def test_la_contrasena_se_comprueba_antes_que_el_alias(client, crear_usuario):
    """Una contraseña común da 422 aunque el alias esté en uso: no se gasta un
    hash ni se revela nada más de lo que ya revela el 409."""
    crear_usuario("Ocupado_1")

    respuesta = client.post("/auth/registro", json={"alias": "Ocupado_1", "password": "qwertyuiop"})

    assert codigo(respuesta) == "password_comun"
