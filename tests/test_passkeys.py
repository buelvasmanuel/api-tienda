"""Passkeys (docs/CONTRATO_PASSKEYS.md), con un autenticador simulado que
produce firmas reales: la API las verifica con la librería `webauthn`."""

import pytest
from sqlalchemy import select

from app import seguridad
from app.models import Passkey, RetoWebAuthn
from tests.autenticador import Autenticador, b64url
from tests.ayudas import PASSWORD, cabeceras, codigo


@pytest.fixture
def jugador(client, crear_usuario):
    usuario = crear_usuario("Detective_Ana")
    client.headers.update(cabeceras(usuario))
    client.usuario = usuario
    return client


def registrar(client, autenticador, nombre=None, **trampas):
    datos = client.post("/passkeys/registro/opciones").json()
    credencial = autenticador.crear(datos["opciones"], **trampas)
    cuerpo = {"reto_id": datos["reto_id"], "credencial": credencial} | ({"nombre": nombre} if nombre else {})
    return client.post("/passkeys/registro/verificacion", json=cuerpo)


def entrar(client, autenticador, **trampas):
    datos = client.post("/passkeys/login/opciones", headers={"Authorization": ""}).json()
    credencial = autenticador.firmar(datos["opciones"], **trampas)
    return client.post("/passkeys/login/verificacion", json={"reto_id": datos["reto_id"], "credencial": credencial},
                       headers={"Authorization": ""})


@pytest.fixture
def con_passkey(jugador):
    autenticador = Autenticador()
    assert registrar(jugador, autenticador, "Portátil").status_code == 201
    return autenticador


# ---------- Registro ----------

def test_opciones_de_registro(jugador):
    respuesta = jugador.post("/passkeys/registro/opciones")

    assert respuesta.status_code == 200 and respuesta.headers["Cache-Control"] == "no-store"
    datos = respuesta.json()
    opciones = datos["opciones"]
    assert len(datos["reto_id"]) == 32
    assert opciones["rp"] == {"id": "buelvasmanuel.github.io", "name": "Estudiantes"}
    assert opciones["user"]["name"] == "Detective_Ana"
    assert opciones["authenticatorSelection"]["residentKey"] == "required"
    assert opciones["authenticatorSelection"]["userVerification"] == "required"
    assert opciones["attestation"] == "none"


def test_el_user_id_de_webauthn_no_lleva_datos_personales(jugador):
    opciones = jugador.post("/passkeys/registro/opciones").json()["opciones"]

    assert opciones["user"]["id"] == b64url(seguridad.identificador_webauthn(jugador.usuario.id))
    # Ni el id tal cual ni el alias: un HMAC opaco de 32 bytes.
    for personal in (str(jugador.usuario.id), "Detective_Ana"):
        assert opciones["user"]["id"] != b64url(personal.encode())
    assert len(seguridad.identificador_webauthn(jugador.usuario.id)) == 32


def test_registrar_una_passkey(jugador, db_session):
    respuesta = registrar(jugador, Autenticador(), "  Portátil de casa  ")

    assert respuesta.status_code == 201
    datos = respuesta.json()
    assert (datos["nombre"], datos["ultimo_uso"], datos["sincronizada"]) == ("Portátil de casa", None, True)
    guardada = db_session.scalar(select(Passkey))
    assert guardada.usuario_id == jugador.usuario.id
    assert "PRIVATE" not in guardada.clave_publica  # solo la clave pública


def test_nombre_por_defecto(jugador):
    assert registrar(jugador, Autenticador()).json()["nombre"] == "Passkey"


def test_registrar_exige_sesion(client):
    assert codigo(client.post("/passkeys/registro/opciones")) == "no_autenticado"


def test_las_ya_registradas_van_excluidas(jugador, con_passkey):
    opciones = jugador.post("/passkeys/registro/opciones").json()["opciones"]

    assert [c["id"] for c in opciones["excludeCredentials"]] == [b64url(con_passkey.credencial_id)]


def test_la_misma_passkey_dos_veces(jugador, con_passkey):
    respuesta = registrar(jugador, con_passkey)

    assert respuesta.status_code == 409 and codigo(respuesta) == "passkey_repetida"


@pytest.mark.parametrize(
    "trampa",
    [{"origen": "https://phishing.example"}, {"origen": "http://buelvasmanuel.github.io"},
     {"rp_id": "otro.github.io"}, {"verificado": False}],
    ids=["otro-origen", "sin-https", "otro-dominio", "sin-verificacion-del-usuario"],
)
def test_registro_rechazado(jugador, trampa):
    respuesta = registrar(jugador, Autenticador(), **trampa)

    assert respuesta.status_code == 422 and codigo(respuesta) == "passkey_invalida"


def test_el_reto_de_registro_es_de_un_solo_uso(jugador):
    datos = jugador.post("/passkeys/registro/opciones").json()
    credencial = Autenticador().crear(datos["opciones"])
    cuerpo = {"reto_id": datos["reto_id"], "credencial": credencial}

    assert jugador.post("/passkeys/registro/verificacion", json=cuerpo).status_code == 201
    otra = jugador.post("/passkeys/registro/verificacion", json=cuerpo)
    assert otra.status_code == 401 and codigo(otra) == "reto_invalido"


def test_un_reto_fallido_tambien_queda_gastado(jugador):
    datos = jugador.post("/passkeys/registro/opciones").json()
    autenticador = Autenticador()
    mala = autenticador.crear(datos["opciones"], origen="https://phishing.example")
    buena = autenticador.crear(datos["opciones"])

    jugador.post("/passkeys/registro/verificacion", json={"reto_id": datos["reto_id"], "credencial": mala})
    respuesta = jugador.post("/passkeys/registro/verificacion", json={"reto_id": datos["reto_id"], "credencial": buena})

    assert codigo(respuesta) == "reto_invalido"


def test_el_reto_de_registro_es_del_usuario_que_lo_pidio(jugador, client, crear_usuario):
    datos = jugador.post("/passkeys/registro/opciones").json()
    otro = cabeceras(crear_usuario("Otro_1"))

    respuesta = client.post("/passkeys/registro/verificacion", headers=otro,
                            json={"reto_id": datos["reto_id"], "credencial": Autenticador().crear(datos["opciones"])})

    assert codigo(respuesta) == "reto_invalido"


def test_el_reto_caduca_a_los_5_minutos(jugador, reloj):
    datos = jugador.post("/passkeys/registro/opciones").json()
    reloj.avanzar(minutes=5)
    jugador.headers.update(cabeceras(jugador.usuario))

    respuesta = jugador.post("/passkeys/registro/verificacion",
                             json={"reto_id": datos["reto_id"], "credencial": Autenticador().crear(datos["opciones"])})

    assert codigo(respuesta) == "reto_invalido"


@pytest.mark.parametrize("cuerpo", [{"reto_id": "no-es-hex", "credencial": {}}, {"reto_id": "0" * 32},
                                    {"reto_id": "0" * 32, "credencial": {}, "extra": 1},
                                    {"reto_id": "0" * 32, "credencial": {}, "nombre": "x" * 61}])
def test_cuerpo_mal_formado(jugador, cuerpo):
    assert jugador.post("/passkeys/registro/verificacion", json=cuerpo).status_code == 422


def test_credencial_basura(jugador):
    datos = jugador.post("/passkeys/registro/opciones").json()

    respuesta = jugador.post("/passkeys/registro/verificacion",
                             json={"reto_id": datos["reto_id"], "credencial": {"id": "x", "response": "basura"}})

    assert respuesta.status_code == 422 and codigo(respuesta) == "passkey_invalida"


# ---------- Entrar ----------

def test_opciones_de_login_sin_sesion(client):
    respuesta = client.post("/passkeys/login/opciones")

    assert respuesta.status_code == 200
    opciones = respuesta.json()["opciones"]
    assert (opciones["rpId"], opciones["userVerification"]) == ("buelvasmanuel.github.io", "required")
    assert opciones.get("allowCredentials", []) == []


def test_entrar_con_passkey(jugador, con_passkey, db_session):
    respuesta = entrar(jugador, con_passkey)

    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["usuario"] == {"id": jugador.usuario.id, "alias": "Detective_Ana", "rol": "jugador"}
    assert respuesta.headers["Cache-Control"] == "no-store"
    # Los tokens son los de siempre: sirven para la API y para refrescar.
    assert jugador.get("/auth/yo", headers={"Authorization": f"Bearer {datos['access_token']}"}).status_code == 200
    assert jugador.post("/auth/refresh", json={"refresh_token": datos["refresh_token"]}).status_code == 200
    guardada = db_session.scalar(select(Passkey))
    db_session.refresh(guardada)
    assert guardada.contador == 1 and guardada.ultimo_uso is not None


def test_entrar_varias_veces(jugador, con_passkey):
    assert [entrar(jugador, con_passkey).status_code for _ in range(3)] == [200, 200, 200]


@pytest.mark.parametrize(
    "trampa",
    [{"origen": "https://phishing.example"}, {"rp_id": "otro.github.io"}, {"verificado": False},
     {"reto": b64url(b"otro reto distinto del pedido a la API")}],
    ids=["otro-origen", "otro-dominio", "sin-verificacion-del-usuario", "otro-desafio"],
)
def test_login_rechazado(jugador, con_passkey, trampa):
    respuesta = entrar(jugador, con_passkey, **trampa)

    assert respuesta.status_code == 401 and codigo(respuesta) == "passkey_invalida"
    assert respuesta.headers["WWW-Authenticate"] == "Bearer"


def test_firma_de_otra_clave(jugador, con_passkey):
    impostor = Autenticador()
    impostor.credencial_id = con_passkey.credencial_id  # mismo id, otra clave privada

    assert codigo(entrar(jugador, impostor)) == "passkey_invalida"


def test_passkey_desconocida(client):
    assert codigo(entrar(client, Autenticador())) == "passkey_invalida"


def test_contador_que_retrocede_es_una_copia(jugador, con_passkey):
    entrar(jugador, con_passkey, contador=10)

    assert codigo(entrar(jugador, con_passkey, contador=5)) == "passkey_invalida"


def test_el_reto_de_login_es_de_un_solo_uso(jugador, con_passkey):
    datos = jugador.post("/passkeys/login/opciones").json()
    credencial = con_passkey.firmar(datos["opciones"])
    cuerpo = {"reto_id": datos["reto_id"], "credencial": credencial}

    assert jugador.post("/passkeys/login/verificacion", json=cuerpo).status_code == 200
    assert codigo(jugador.post("/passkeys/login/verificacion", json=cuerpo)) == "reto_invalido"


def test_un_reto_de_registro_no_sirve_para_entrar(jugador, con_passkey):
    reto_id = jugador.post("/passkeys/registro/opciones").json()["reto_id"]
    opciones = jugador.post("/passkeys/login/opciones").json()["opciones"]

    respuesta = jugador.post("/passkeys/login/verificacion",
                             json={"reto_id": reto_id, "credencial": con_passkey.firmar(opciones)})

    assert codigo(respuesta) == "reto_invalido"


def test_cuenta_desactivada_no_entra_con_passkey(jugador, con_passkey, db_session):
    jugador.usuario.activo = False
    db_session.flush()

    respuesta = entrar(jugador, con_passkey)

    assert respuesta.status_code == 403 and codigo(respuesta) == "cuenta_desactivada"


def test_los_retos_caducados_se_limpian(jugador, reloj, db_session):
    for _ in range(3):
        jugador.post("/passkeys/login/opciones")
    reloj.avanzar(minutes=6)

    jugador.post("/passkeys/login/opciones")

    assert len(db_session.scalars(select(RetoWebAuthn)).all()) == 1


def test_limite_por_ip_en_el_login_con_passkey(client, limite_por_ip):
    for _ in range(60):
        assert client.post("/passkeys/login/opciones").status_code == 200

    assert client.post("/passkeys/login/opciones").status_code == 429


# ---------- Gestionar ----------

def test_listar_mis_passkeys(jugador, con_passkey, reloj):
    reloj.avanzar(seconds=1)
    jugador.headers.update(cabeceras(jugador.usuario))
    registrar(jugador, Autenticador(), "Móvil")

    lista = jugador.get("/passkeys").json()

    assert [p["nombre"] for p in lista] == ["Móvil", "Portátil"]  # la más reciente primero
    assert set(lista[0]) == {"id", "nombre", "creada_en", "ultimo_uso", "sincronizada"}


def test_borrar_una_passkey(jugador, con_passkey):
    passkey_id = jugador.get("/passkeys").json()[0]["id"]

    assert jugador.delete(f"/passkeys/{passkey_id}").status_code == 204
    assert jugador.get("/passkeys").json() == []
    assert codigo(entrar(jugador, con_passkey)) == "passkey_invalida"


def test_no_se_borra_la_passkey_de_otro(jugador, con_passkey, client, crear_usuario):
    passkey_id = jugador.get("/passkeys").json()[0]["id"]
    otro = cabeceras(crear_usuario("Otro_1"))

    respuesta = client.delete(f"/passkeys/{passkey_id}", headers=otro)

    assert respuesta.status_code == 404 and codigo(respuesta) == "passkey_no_encontrada"
    assert jugador.get("/passkeys").json() != []


def test_restablecer_la_cuenta_borra_sus_passkeys(jugador, con_passkey, client, crear_usuario):
    admin = cabeceras(crear_usuario("admin_1", rol="admin"))

    client.put(f"/usuarios/{jugador.usuario.id}/password", json={"password_nueva": "nueva-clave-789"}, headers=admin)

    assert codigo(entrar(client, con_passkey)) == "passkey_invalida"
    nuevo = client.post("/auth/login", json={"alias": "Detective_Ana", "password": "nueva-clave-789"})
    assert nuevo.status_code == 200
    assert client.get("/passkeys", headers={"Authorization": f"Bearer {nuevo.json()['access_token']}"}).json() == []


def test_el_login_con_contrasena_sigue_igual(jugador, con_passkey):
    respuesta = jugador.post("/auth/login", json={"alias": "Detective_Ana", "password": PASSWORD},
                             headers={"Authorization": ""})

    assert respuesta.status_code == 200 and respuesta.json()["usuario"]["alias"] == "Detective_Ana"


# ---------- Límites (auditoría B5) ----------

def test_maximo_10_passkeys_por_usuario(jugador):
    for _ in range(10):
        assert registrar(jugador, Autenticador()).status_code == 201

    respuesta = jugador.post("/passkeys/registro/opciones")

    assert respuesta.status_code == 409
    assert codigo(respuesta) == "demasiadas_passkeys"
    borrar = jugador.get("/passkeys").json()[0]["id"]
    assert jugador.delete(f"/passkeys/{borrar}").status_code == 204
    assert registrar(jugador, Autenticador()).status_code == 201  # al borrar una, cabe otra


def test_el_maximo_se_comprueba_tambien_al_verificar(jugador):
    """Opciones pedidas con 9 passkeys y otra registrada entretanto: la
    verificación también cuenta."""
    for _ in range(9):
        registrar(jugador, Autenticador())
    datos = jugador.post("/passkeys/registro/opciones").json()
    assert registrar(jugador, Autenticador()).status_code == 201  # la 10.ª, con otras opciones

    respuesta = jugador.post("/passkeys/registro/verificacion",
                             json={"reto_id": datos["reto_id"], "credencial": Autenticador().crear(datos["opciones"])})

    assert respuesta.status_code == 409
    assert codigo(respuesta) == "demasiadas_passkeys"


def test_limite_de_opciones_de_registro_por_usuario(jugador, client, crear_usuario, limite_por_ip):
    from app import limites

    for _ in range(limites.PASSKEYS_REGISTRO.amount):
        assert jugador.post("/passkeys/registro/opciones").status_code == 200

    respuesta = jugador.post("/passkeys/registro/opciones")

    assert respuesta.status_code == 429
    assert codigo(respuesta) == "demasiados_intentos"
    otro = crear_usuario("Detective_Beto")
    assert client.post("/passkeys/registro/opciones", headers=cabeceras(otro)).status_code == 200
