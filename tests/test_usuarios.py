"""Gestión de usuarios por docentes y admins (docs/CONTRATO_AUTH.md §4.9)."""

import re

import pytest
from sqlalchemy import select

from app.models import SesionRefresh, Usuario
from tests.ayudas import PASSWORD, cabeceras, codigo


@pytest.fixture
def admin(crear_usuario):
    return crear_usuario("admin_1", rol="admin")


@pytest.fixture
def jugador(crear_usuario):
    return crear_usuario("jugador_1")


def login(client, alias, password=PASSWORD):
    return client.post("/auth/login", json={"alias": alias, "password": password})


# ---------- GET /usuarios ----------

@pytest.mark.parametrize("rol", ["docente", "admin"])
def test_docente_y_admin_ven_a_todos(client, crear_usuario, jugador, rol):
    quien = crear_usuario("profe", rol=rol)

    respuesta = client.get("/usuarios", headers=cabeceras(quien))

    assert respuesta.status_code == 200
    lista = respuesta.json()
    assert [u["alias"] for u in lista] == ["jugador_1", "profe"]  # por id
    assert set(lista[0]) == {"id", "alias", "rol", "activo", "creado_en"}
    assert lista[0]["creado_en"].endswith("Z")


def test_un_jugador_no_ve_la_lista(client, jugador):
    respuesta = client.get("/usuarios", headers=cabeceras(jugador))

    assert respuesta.status_code == 403
    assert codigo(respuesta) == "permiso_denegado"


def test_la_lista_exige_token(client):
    assert codigo(client.get("/usuarios")) == "no_autenticado"


# ---------- PATCH /usuarios/{id} ----------

def test_admin_cambia_el_rol_con_efecto_inmediato(client, admin, jugador):
    token_jugador = cabeceras(jugador)
    assert client.get("/usuarios", headers=token_jugador).status_code == 403

    respuesta = client.patch(f"/usuarios/{jugador.id}", json={"rol": "docente"}, headers=cabeceras(admin))

    assert respuesta.status_code == 200
    assert respuesta.json()["rol"] == "docente"
    assert client.get("/usuarios", headers=token_jugador).status_code == 200  # mismo token


def test_desactivar_cierra_sus_sesiones(client, admin, jugador, db_session):
    sesion = login(client, "jugador_1").json()

    respuesta = client.patch(f"/usuarios/{jugador.id}", json={"activo": False}, headers=cabeceras(admin))

    assert respuesta.json()["activo"] is False
    acceso = {"Authorization": f"Bearer {sesion['access_token']}"}
    assert codigo(client.get("/auth/yo", headers=acceso)) == "cuenta_desactivada"
    assert codigo(client.post("/auth/refresh", json={"refresh_token": sesion["refresh_token"]})) == "sesion_revocada"
    assert db_session.scalar(select(SesionRefresh.motivo_revocacion)) == "desactivada"
    assert codigo(login(client, "jugador_1")) == "cuenta_desactivada"


def test_reactivar(client, admin, crear_usuario):
    inactivo = crear_usuario("inactivo", activo=False)

    client.patch(f"/usuarios/{inactivo.id}", json={"activo": True}, headers=cabeceras(admin))

    assert login(client, "inactivo").status_code == 200


def test_cuerpo_vacio_no_cambia_nada(client, admin, jugador):
    respuesta = client.patch(f"/usuarios/{jugador.id}", json={}, headers=cabeceras(admin))

    assert respuesta.status_code == 200
    assert (respuesta.json()["rol"], respuesta.json()["activo"]) == ("jugador", True)


@pytest.mark.parametrize("cuerpo", [{"rol": "superadmin"}, {"alias": "otro"}, {"activo": "quizas"}])
def test_cambios_invalidos(client, admin, jugador, cuerpo):
    assert client.patch(f"/usuarios/{jugador.id}", json=cuerpo, headers=cabeceras(admin)).status_code == 422


def test_un_admin_no_se_modifica_a_si_mismo(client, admin):
    respuesta = client.patch(f"/usuarios/{admin.id}", json={"rol": "jugador"}, headers=cabeceras(admin))

    assert respuesta.status_code == 409
    assert codigo(respuesta) == "no_puedes_modificarte"


def test_usuario_inexistente(client, admin):
    respuesta = client.patch("/usuarios/99999", json={"rol": "docente"}, headers=cabeceras(admin))

    assert respuesta.status_code == 404
    assert codigo(respuesta) == "usuario_no_encontrado"


@pytest.mark.parametrize("rol", ["jugador", "docente"])
def test_solo_un_admin_modifica(client, crear_usuario, jugador, rol):
    quien = crear_usuario("otro", rol=rol)

    respuesta = client.patch(f"/usuarios/{jugador.id}", json={"rol": "admin"}, headers=cabeceras(quien))

    assert codigo(respuesta) == "permiso_denegado"


# ---------- PUT /usuarios/{id}/password ----------

def test_admin_restablece_la_password(client, admin, jugador, reloj):
    sesion = login(client, "jugador_1").json()
    for _ in range(5):
        login(client, "jugador_1", "intento-fallido")  # cuenta bloqueada

    respuesta = client.put(f"/usuarios/{jugador.id}/password",
                           json={"password_nueva": "nueva-clave-789"}, headers=cabeceras(admin))

    assert respuesta.status_code == 204
    assert codigo(client.post("/auth/refresh", json={"refresh_token": sesion["refresh_token"]})) == "sesion_revocada"
    assert login(client, "jugador_1", "nueva-clave-789").status_code == 200  # y ya no está bloqueada


def test_restablecer_password_invalida(client, admin, jugador):
    for nueva in ("corta", "Jugador_1"):
        respuesta = client.put(f"/usuarios/{jugador.id}/password",
                               json={"password_nueva": nueva}, headers=cabeceras(admin))
        assert respuesta.status_code == 422
        assert nueva not in respuesta.text


def test_un_admin_no_restablece_su_propia_password(client, admin):
    respuesta = client.put(f"/usuarios/{admin.id}/password",
                           json={"password_nueva": "nueva-clave-789"}, headers=cabeceras(admin))

    assert codigo(respuesta) == "no_puedes_modificarte"


def test_solo_un_admin_restablece(client, crear_usuario, jugador):
    docente = crear_usuario("profe", rol="docente")

    respuesta = client.put(f"/usuarios/{jugador.id}/password",
                           json={"password_nueva": "nueva-clave-789"}, headers=cabeceras(docente))

    assert codigo(respuesta) == "permiso_denegado"


# ---------- POST /usuarios/lote ----------

def crear_lote(client, quien, aliases):
    return client.post("/usuarios/lote", json={"aliases": aliases}, headers=cabeceras(quien))


@pytest.mark.parametrize("rol", ["docente", "admin"])
def test_lote_crea_jugadores_con_password_generada(client, crear_usuario, rol):
    quien = crear_usuario("profe", rol=rol)

    respuesta = crear_lote(client, quien, ["Alumno_01", "Alumno_02", "Alumno_03"])

    assert respuesta.status_code == 201
    assert respuesta.headers["Cache-Control"] == "no-store"
    cuentas = respuesta.json()["cuentas"]
    assert [c["alias"] for c in cuentas] == ["Alumno_01", "Alumno_02", "Alumno_03"]
    assert len({c["password"] for c in cuentas}) == 3
    for cuenta in cuentas:
        assert re.fullmatch(r"[a-hj-km-np-z2-9]{4}-[a-hj-km-np-z2-9]{4}-[a-hj-km-np-z2-9]{4}", cuenta["password"])
        login_ok = login(client, cuenta["alias"], cuenta["password"])
        assert login_ok.status_code == 200
        assert login_ok.json()["usuario"]["rol"] == "jugador"


def test_lote_todo_o_nada_si_un_alias_existe(client, crear_usuario, admin, db_session):
    crear_usuario("Alumno_02")

    respuesta = crear_lote(client, admin, ["Alumno_01", "alumno_02", "Alumno_03"])

    assert respuesta.status_code == 409
    assert respuesta.json()["detail"]["codigo"] == "alias_en_uso"
    assert respuesta.json()["detail"]["aliases"] == ["Alumno_02"]
    assert db_session.scalar(select(Usuario).where(Usuario.alias == "Alumno_01")) is None


@pytest.mark.parametrize(
    "aliases",
    [[], ["ok_1", "OK_1"], ["con espacio"], [f"alumno_{i}" for i in range(41)]],
    ids=["vacia", "repetidos", "alias-invalido", "mas-de-40"],
)
def test_lote_invalido(client, admin, aliases):
    assert crear_lote(client, admin, aliases).status_code == 422


def test_un_jugador_no_crea_cuentas(client, jugador):
    assert codigo(crear_lote(client, jugador, ["Alumno_01"])) == "permiso_denegado"


def test_lote_de_40_cuentas(client, admin):
    respuesta = crear_lote(client, admin, [f"alumno_{i:02d}" for i in range(40)])

    assert respuesta.status_code == 201
    assert len(respuesta.json()["cuentas"]) == 40


def test_las_acciones_de_admin_quedan_en_el_log(client, admin, jugador, caplog):
    with caplog.at_level("INFO", logger="app.auth"):
        client.patch(f"/usuarios/{jugador.id}", json={"rol": "docente", "activo": False}, headers=cabeceras(admin))
        client.put(f"/usuarios/{jugador.id}/password", json={"password_nueva": "nueva-clave-789"},
                   headers=cabeceras(admin))

    eventos = [r.msg for r in caplog.records if r.name == "app.auth" and isinstance(r.msg, dict)]
    assert {"event": "admin_modifica_usuario", "admin_id": admin.id, "usuario_id": jugador.id, "rol_antes": "jugador",
            "rol": "docente", "activo_antes": True, "activo": False}.items() <= eventos[0].items()
    assert {"event": "admin_restablece_password", "admin_id": admin.id, "usuario_id": jugador.id}.items() <= eventos[-1].items()
    assert "nueva-clave-789" not in str(eventos)
