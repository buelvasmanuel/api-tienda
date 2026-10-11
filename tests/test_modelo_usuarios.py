"""Restricciones de las tablas usuarios y sesiones_refresh en la propia base."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError

from app import reloj
from app.models import SesionRefresh, Usuario


def nuevo_usuario(db, alias="Ana_01", **campos) -> Usuario:
    usuario = Usuario(alias=alias, hash_password="$argon2id$x", **campos)
    db.add(usuario)
    db.flush()
    return usuario


def nueva_sesion(db, usuario, hash_token="a" * 64, **campos) -> SesionRefresh:
    ahora = reloj.ahora()
    datos = {
        "familia_id": "f" * 32, "hash_token": hash_token,
        "expira_en": ahora + timedelta(days=14), "familia_expira_en": ahora + timedelta(days=30),
    } | campos
    sesion = SesionRefresh(usuario_id=usuario.id, **datos)
    db.add(sesion)
    db.flush()
    return sesion


def test_valores_por_defecto(db_session):
    usuario = nuevo_usuario(db_session)
    db_session.refresh(usuario)

    assert (usuario.rol, usuario.activo, usuario.intentos_fallidos) == ("jugador", True, 0)
    assert usuario.sesiones_validas_desde is None


def test_las_fechas_guardan_microsegundos(db_session):
    momento = datetime(2026, 10, 11, 12, 0, 0, 123456)
    usuario = nuevo_usuario(db_session, sesiones_validas_desde=momento)
    db_session.expire(usuario)

    assert usuario.sesiones_validas_desde == momento


def test_alias_unico_sin_distinguir_mayusculas(db_session):
    nuevo_usuario(db_session, "Ana_01")

    with pytest.raises(IntegrityError):
        nuevo_usuario(db_session, "ana_01")


@pytest.mark.parametrize("alias", ["ab", "con espacio", "tildé", "ñu_", "guion-medio"])
def test_la_base_rechaza_alias_invalidos(db_session, alias):
    with pytest.raises(DBAPIError, match="ck_usuarios_alias"):
        nuevo_usuario(db_session, alias)


def test_la_base_rechaza_roles_desconocidos(db_session):
    with pytest.raises(DBAPIError, match="ck_usuarios_rol"):
        nuevo_usuario(db_session, rol="superadmin")


def test_revocado_y_motivo_van_juntos(db_session):
    usuario = nuevo_usuario(db_session)

    with pytest.raises(DBAPIError, match="ck_sesiones_refresh_revocacion"):
        nueva_sesion(db_session, usuario, revocado_en=reloj.ahora())


def test_la_base_rechaza_motivos_desconocidos(db_session):
    usuario = nuevo_usuario(db_session)

    with pytest.raises(DBAPIError, match="ck_sesiones_refresh_motivo_revocacion"):
        nueva_sesion(db_session, usuario, revocado_en=reloj.ahora(), motivo_revocacion="otro")


def test_un_token_no_puede_durar_mas_que_su_familia(db_session):
    usuario = nuevo_usuario(db_session)
    ahora = reloj.ahora()

    with pytest.raises(DBAPIError, match="ck_sesiones_refresh_expira_en"):
        nueva_sesion(db_session, usuario, expira_en=ahora + timedelta(days=31),
                     familia_expira_en=ahora + timedelta(days=30))


def test_hash_de_token_unico(db_session):
    usuario = nuevo_usuario(db_session)
    nueva_sesion(db_session, usuario, hash_token="b" * 64)

    with pytest.raises(IntegrityError):
        nueva_sesion(db_session, usuario, hash_token="b" * 64)


def test_borrar_un_usuario_borra_sus_sesiones(db_session):
    usuario = nuevo_usuario(db_session)
    nueva_sesion(db_session, usuario)

    db_session.delete(usuario)
    db_session.flush()

    assert db_session.scalars(select(SesionRefresh)).all() == []
