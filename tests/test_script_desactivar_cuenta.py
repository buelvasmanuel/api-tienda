"""scripts/desactivar_cuenta.py contra la app de verdad (TestClient en vez de HTTP)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.models import SesionRefresh
from scripts import desactivar_cuenta
from scripts.desactivar_cuenta import desactivar
from tests.ayudas import PASSWORD


@pytest.fixture
def pedir(client):
    """Mismo contrato que cliente_http(), pero sobre TestClient. Guarda las peticiones."""
    hechas = []
    http = TestClient(app)

    def pedir(metodo, ruta, cuerpo=None, token=None):
        hechas.append((metodo, ruta))
        cabeceras = {"Authorization": f"Bearer {token}"} if token else {}
        r = http.request(metodo, ruta, json=cuerpo, headers=cabeceras)
        return r.status_code, (r.json() if r.content else None)

    pedir.hechas = hechas
    return pedir


@pytest.fixture
def cuentas(crear_usuario):
    admin = crear_usuario("Admin_Script", rol="admin")
    prueba = crear_usuario("Prueba_Script_1")
    otra = crear_usuario("Otra_Persona")
    return admin, prueba, otra


def sesiones_abiertas(db, usuario):
    return db.scalar(select(func.count()).select_from(SesionRefresh)
                     .where(SesionRefresh.usuario_id == usuario.id, SesionRefresh.revocado_en.is_(None)))


def test_desactiva_y_lista_las_activas(pedir, cuentas, db_session, capsys):
    admin, prueba, otra = cuentas
    assert desactivar(pedir, prueba.id, "Prueba_Script_1", "Admin_Script", PASSWORD) == 0
    db_session.refresh(prueba)
    assert prueba.activo is False
    salida = capsys.readouterr().out
    activas = salida.split("Cuentas activas")[1]
    assert "Admin_Script" in activas and "Otra_Persona" in activas and "Prueba_Script_1" not in activas
    assert "Sesión del admin cerrada." in salida


def test_cierra_la_sesion_del_admin(pedir, cuentas, db_session):
    admin, prueba, _ = cuentas
    desactivar(pedir, prueba.id, "Prueba_Script_1", "Admin_Script", PASSWORD)
    assert pedir.hechas[-1] == ("POST", "/auth/logout")
    assert sesiones_abiertas(db_session, admin) == 0


def test_no_imprime_tokens_ni_la_password(pedir, cuentas, capsys, monkeypatch):
    _, prueba, _ = cuentas
    vistos = []
    original = pedir

    def espiar(metodo, ruta, cuerpo=None, token=None):
        estado, respuesta = original(metodo, ruta, cuerpo, token)
        if ruta == "/auth/login" and estado == 200:
            vistos.extend([respuesta["access_token"], respuesta["refresh_token"]])
        return estado, respuesta

    desactivar(espiar, prueba.id, "Prueba_Script_1", "Admin_Script", PASSWORD)
    salida = capsys.readouterr().out
    assert vistos and all(t not in salida for t in vistos)
    assert PASSWORD not in salida


def test_alias_que_no_coincide_no_toca_nada(pedir, cuentas, db_session, capsys):
    _, prueba, otra = cuentas
    assert desactivar(pedir, otra.id, "Prueba_Script_1", "Admin_Script", PASSWORD) == 1
    db_session.refresh(otra)
    assert otra.activo is True
    assert ("PATCH", f"/usuarios/{otra.id}") not in pedir.hechas
    assert "no se toca nada" in capsys.readouterr().out
    assert pedir.hechas[-1] == ("POST", "/auth/logout")


def test_id_inexistente(pedir, cuentas, capsys):
    assert desactivar(pedir, 999_999, "Prueba_Script_1", "Admin_Script", PASSWORD) == 1
    assert "No existe ninguna cuenta con id 999999" in capsys.readouterr().out
    assert not any(m == "PATCH" for m, _ in pedir.hechas)


def test_ya_desactivada(pedir, cuentas, db_session, capsys):
    _, prueba, _ = cuentas
    prueba.activo = False
    db_session.flush()
    assert desactivar(pedir, prueba.id, "Prueba_Script_1", "Admin_Script", PASSWORD) == 0
    assert "Ya estaba desactivada" in capsys.readouterr().out
    assert not any(m == "PATCH" for m, _ in pedir.hechas)


def test_password_mal(pedir, cuentas, db_session, capsys):
    _, prueba, _ = cuentas
    assert desactivar(pedir, prueba.id, "Prueba_Script_1", "Admin_Script", "otra-clave-cualquiera") == 1
    assert "401 credenciales_invalidas" in capsys.readouterr().out
    db_session.refresh(prueba)
    assert prueba.activo is True
    assert pedir.hechas == [("POST", "/auth/login")]


def test_sin_rol_de_admin(pedir, cuentas, crear_usuario, db_session, capsys):
    _, prueba, _ = cuentas
    crear_usuario("Un_Jugador")
    assert desactivar(pedir, prueba.id, "Prueba_Script_1", "Un_Jugador", PASSWORD) == 1
    assert "403" in capsys.readouterr().out
    db_session.refresh(prueba)
    assert prueba.activo is True
    assert pedir.hechas[-1] == ("POST", "/auth/logout")


def test_main_contra_produccion_por_defecto(monkeypatch):
    usadas = []
    monkeypatch.setattr("sys.argv", ["desactivar_cuenta", "20", "Prueba_V1101_20261024"])
    monkeypatch.setattr("builtins.input", lambda _: "")
    monkeypatch.setattr(desactivar_cuenta.getpass, "getpass", lambda _: "secreta")
    monkeypatch.setattr(desactivar_cuenta, "cliente_http", lambda url: usadas.append(url) or "cliente")
    monkeypatch.setattr(desactivar_cuenta, "desactivar", lambda *a: usadas.append(a) or 0)
    with pytest.raises(SystemExit) as salida:
        desactivar_cuenta.main()
    assert salida.value.code == 0
    assert usadas == ["https://api-tienda-wjac.onrender.com",
                      ("cliente", 20, "Prueba_V1101_20261024", "Detective_Manu", "secreta")]
