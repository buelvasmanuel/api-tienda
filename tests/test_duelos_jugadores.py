"""Duelo entre dos jugadores (CONTRATO_DUELO v2, §7 y §8): emparejamiento,
turnos con tiempo, abandonos, XP, WebSocket de avisos y concurrencia."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from app import duelos as reglas
from app.main import app
from app.models import Duelo, Estudiante
from tests.ayudas import cabeceras, codigo
from tests.test_duelos import estudiante


@pytest.fixture
def estudiantes(db_session):
    filas = [estudiante(i) for i in range(12)]
    db_session.add_all(filas)
    db_session.flush()
    return filas


class Jugador:
    """Un jugador con su propio TestClient (cabecera con su access token)."""

    def __init__(self, usuario):
        self.usuario = usuario
        self.http = TestClient(app, headers=cabeceras(usuario))

    def renovar_token(self):  # tras adelantar el reloj, el access anterior caduca
        self.http.headers.update(cabeceras(self.usuario))

    def buscar(self):
        return self.http.post("/duelos", json={"modo": "jugador"})

    def ver(self, duelo_id):
        return self.http.get(f"/duelos/{duelo_id}")

    def jugar(self, duelo_id, ronda, atributo):
        return self.http.post(f"/duelos/{duelo_id}/jugadas", json={"ronda": ronda, "atributo": atributo})

    def abandonar(self, duelo_id):
        return self.http.post(f"/duelos/{duelo_id}/abandonar")


@pytest.fixture
def ana(client, crear_usuario):
    return Jugador(crear_usuario("Ana_1"))


@pytest.fixture
def beto(client, crear_usuario):
    return Jugador(crear_usuario("Beto_1"))


@pytest.fixture
def emparejados(ana, beto, estudiantes):
    """Ana espera, Beto se une: duelo en juego; empieza Ana (lado "a")."""
    ana.buscar()
    duelo = beto.buscar().json()
    assert duelo["estado"] == "en_juego"
    return duelo["id"]


def jugar_ronda(ana, beto, duelo_id, ronda, atributo="promedio"):
    """Juega quien tiene el turno: Ana en las impares, Beto en las pares."""
    quien = ana if ronda % 2 else beto
    respuesta = quien.jugar(duelo_id, ronda, atributo)
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


# ---------- Emparejamiento ----------

def test_sin_nadie_esperando_quedas_esperando(ana, estudiantes, reloj):
    respuesta = ana.buscar()

    assert respuesta.status_code == 201
    d = respuesta.json()
    assert (d["estado"], d["modo"], d["ronda"], d["turno"], d["mi_carta"]) == ("esperando", "jugador", None, None, None)
    assert d["rival"] == {"tipo": "jugador", "alias": None} and d["rival_en_linea"] is None
    assert d["expira_turno_en"] == (reloj.momento + timedelta(seconds=60)).isoformat(timespec="microseconds") + "Z"


def test_buscar_dos_veces_devuelve_la_misma_espera(ana, estudiantes):
    primera = ana.buscar().json()
    segunda = ana.buscar()

    assert segunda.status_code == 200 and segunda.json()["id"] == primera["id"]


def test_el_segundo_se_empareja_con_el_que_espera(ana, beto, estudiantes):
    esperando = ana.buscar().json()

    respuesta = beto.buscar()

    assert respuesta.status_code == 201
    de_beto = respuesta.json()
    de_ana = ana.ver(esperando["id"]).json()
    assert de_beto["id"] == esperando["id"]
    assert (de_ana["estado"], de_ana["ronda"], de_ana["turno"]) == ("en_juego", 1, "yo")  # empieza quien esperaba
    assert de_beto["turno"] == "rival"
    assert de_ana["rival"] == {"tipo": "jugador", "alias": "Beto_1"}
    assert de_beto["rival"] == {"tipo": "jugador", "alias": "Ana_1"}
    assert de_ana["mi_carta"]["estudiante_id"] != de_beto["mi_carta"]["estudiante_id"]


def test_nadie_se_empareja_consigo_mismo(ana, estudiantes, db_session):
    ana.buscar()
    ana.buscar()

    assert len(db_session.scalars(select(Duelo)).all()) == 1


def test_la_espera_caduca_a_los_60_s(ana, beto, estudiantes, reloj):
    vieja = ana.buscar().json()
    reloj.avanzar(seconds=61)
    beto.renovar_token()
    ana.renovar_token()

    nueva = beto.buscar().json()

    assert nueva["id"] != vieja["id"] and nueva["estado"] == "esperando"
    caducada = ana.ver(vieja["id"]).json()
    assert (caducada["estado"], caducada["resultado"], caducada["xp_ganada"]) == ("abandonado", None, 0)


def test_un_duelo_en_curso_a_la_vez(ana, beto, emparejados):
    respuesta = ana.buscar()

    assert respuesta.status_code == 409
    assert codigo(respuesta) == "duelo_en_curso"
    assert respuesta.json()["detail"]["duelo_id"] == emparejados


def test_cancelar_la_busqueda(ana, estudiantes):
    d = ana.buscar().json()

    cancelado = ana.abandonar(d["id"])
    otra_vez = ana.abandonar(d["id"])

    assert cancelado.status_code == otra_vez.status_code == 200
    assert (cancelado.json()["estado"], cancelado.json()["resultado"]) == ("abandonado", None)


# ---------- Turnos ----------

def test_partida_completa_entre_dos(ana, beto, emparejados):
    for ronda in range(1, 6):
        jugar_ronda(ana, beto, emparejados, ronda, "asistencia")

    de_ana, de_beto = ana.ver(emparejados).json(), beto.ver(emparejados).json()
    assert de_ana["estado"] == de_beto["estado"] == "terminado"
    assert de_ana["marcador"] == {"yo": de_beto["marcador"]["rival"], "rival": de_beto["marcador"]["yo"]}
    opuesto = {"victoria": "derrota", "derrota": "victoria", "empate": "empate"}
    assert de_beto["resultado"] == opuesto[de_ana["resultado"]]
    xp = reglas.XP_POR_RESULTADO["jugador"]
    assert (de_ana["xp_ganada"], de_beto["xp_ganada"]) == (xp[de_ana["resultado"]], xp[de_beto["resultado"]])
    assert [r["elegido_por"] for r in de_ana["rondas"]] == ["yo", "rival", "yo", "rival", "yo"]
    assert all(r["motivo"] == "jugada" for r in de_ana["rondas"])


def test_solo_juega_quien_tiene_el_turno(ana, beto, emparejados):
    assert codigo(beto.jugar(emparejados, 1, "promedio")) == "no_es_tu_turno"
    assert codigo(beto.jugar(emparejados, 1, None)) == "no_es_tu_turno"  # contra un jugador no hay "ver jugada"
    jugar_ronda(ana, beto, emparejados, 1)

    assert codigo(ana.jugar(emparejados, 2, "promedio")) == "no_es_tu_turno"
    assert codigo(ana.jugar(emparejados, 2, None)) == "no_es_tu_turno"


def test_nadie_ve_la_carta_del_otro_antes_de_tiempo(ana, beto, emparejados, db_session):
    import json

    duelo = db_session.get(Duelo, emparejados)
    de_beto = json.dumps(beto.ver(emparejados).json())

    ocultas = [c["estudiante_id"] for c in duelo.mazo_a] + [c["estudiante_id"] for c in duelo.mazo_b[1:]]
    assert all(f'"estudiante_id": {i},' not in de_beto for i in ocultas)


def test_xp_del_modo_jugador_aparte_de_la_maquina(ana, beto, emparejados):
    for ronda in range(1, 6):
        jugar_ronda(ana, beto, emparejados, ronda)

    contra_maquina = ana.http.post("/duelos", json={"modo": "maquina"}).json()

    assert ana.ver(emparejados).json()["duelos_con_xp_hoy"] == {"jugados": 1, "maximo": 3}
    assert contra_maquina["duelos_con_xp_hoy"] == {"jugados": 0, "maximo": 3}


# ---------- Tiempo ----------

def test_si_vence_el_turno_la_ronda_es_del_rival(ana, beto, emparejados, reloj):
    vence = ana.ver(emparejados).json()["expira_turno_en"]
    reloj.avanzar(seconds=46)
    ana.renovar_token()
    beto.renovar_token()

    d = beto.ver(emparejados).json()

    ronda1 = d["rondas"][0]
    assert (ronda1["motivo"], ronda1["atributo"], ronda1["ganador"], ronda1["elegido_por"]) == ("tiempo", None, "yo", "rival")
    assert "se le acabó el tiempo" in ronda1["explicacion"]
    assert (d["ronda"], d["turno"], d["marcador"]) == (2, "yo", {"yo": 1, "rival": 0})
    # El turno siguiente cuenta desde el vencimiento, no desde la consulta.
    from datetime import datetime
    anterior = datetime.fromisoformat(vence.rstrip("Z"))
    assert d["expira_turno_en"] == (anterior + timedelta(seconds=45)).isoformat(timespec="microseconds") + "Z"
    assert codigo(ana.jugar(emparejados, 1, "promedio")) == "tiempo_agotado"


def test_dos_turnos_vencidos_seguidos_son_abandono(ana, beto, emparejados, reloj):
    reloj.avanzar(seconds=46)                     # ronda 1: a Ana se le acaba el tiempo
    beto.renovar_token()
    ana.renovar_token()
    jugar_ronda(ana, beto, emparejados, 2)        # ronda 2: Beto juega
    reloj.avanzar(seconds=46)                     # ronda 3: Ana otra vez sin jugar
    ana.renovar_token()
    beto.renovar_token()

    de_beto = beto.ver(emparejados).json()

    assert (de_beto["estado"], de_beto["resultado"]) == ("abandonado", "victoria")
    assert len(de_beto["rondas"]) == 3
    assert de_beto["xp_ganada"] == 40          # 3 rondas resueltas: el ganador recibe XP
    assert ana.ver(emparejados).json()["xp_ganada"] == 0
    assert beto.http.get("/juego/progreso").json()["xp"] == 40


def test_si_los_dos_se_van_se_resuelve_todo_al_volver(ana, beto, emparejados, reloj):
    reloj.avanzar(minutes=10)
    ana.renovar_token()

    d = ana.ver(emparejados).json()

    assert d["estado"] == "abandonado"
    assert [r["motivo"] for r in d["rondas"]] == ["tiempo", "tiempo", "tiempo"]
    assert d["resultado"] == "derrota"  # a Ana se le acabaron dos turnos suyos seguidos


def test_abandonar_pronto_no_regala_xp(ana, beto, emparejados):
    respuesta = beto.abandonar(emparejados)

    assert (respuesta.json()["estado"], respuesta.json()["resultado"]) == ("abandonado", "derrota")
    de_ana = ana.ver(emparejados).json()
    assert (de_ana["resultado"], de_ana["xp_ganada"]) == ("victoria", 0)  # menos de 3 rondas: sin XP


def test_abandonar_tarde_da_la_xp_al_otro(ana, beto, emparejados):
    for ronda in (1, 2, 3):
        jugar_ronda(ana, beto, emparejados, ronda)

    beto.abandonar(emparejados)

    assert ana.ver(emparejados).json()["xp_ganada"] == 40
    assert ana.http.get("/juego/progreso").json()["racha_dias"] == 1
    assert beto.http.get("/juego/progreso").json()["racha_dias"] == 0  # abandonar no cuenta como actividad


# ---------- WebSocket ----------

def abrir(jugador, duelo_id, **kw):
    return jugador.http.websocket_connect(f"/duelos/{duelo_id}/ws", **kw)


def autenticarse(ws, usuario):
    ws.send_json({"tipo": "auth", "token": cabeceras(usuario)["Authorization"].removeprefix("Bearer ")})
    return ws.receive_json()


def test_ws_autenticacion_y_ping(ana, beto, emparejados):
    with abrir(ana, emparejados) as ws:
        listo = autenticarse(ws, ana.usuario)
        assert listo["tipo"] == "listo" and listo["duelo"]["id"] == emparejados and listo["duelo"]["turno"] == "yo"
        ws.send_json({"tipo": "ping"})
        assert ws.receive_json() == {"tipo": "pong"}


def cierre(ws) -> int:
    """Primero llega el mensaje {"tipo": "error"} (en Render el marco de cierre
    no siempre llega) y después el cierre, con el mismo código."""
    aviso = ws.receive_json()
    assert aviso["tipo"] == "error" and isinstance(aviso["motivo"], str)
    with pytest.raises(WebSocketDisconnect) as e:
        ws.receive_json()
    assert e.value.code == aviso["codigo"]
    return e.value.code


@pytest.mark.parametrize("primero", [{"tipo": "ping"}, {"tipo": "auth"}, {"tipo": "auth", "token": 123}, ["auth"]])
def test_ws_sin_auth_valido_cierra_con_4401(ana, emparejados, primero):
    with abrir(ana, emparejados) as ws:
        ws.send_json(primero)
        assert cierre(ws) == 4401


def test_ws_token_invalido_o_caducado(ana, emparejados, reloj):
    viejo = cabeceras(ana.usuario)["Authorization"].removeprefix("Bearer ")
    reloj.avanzar(minutes=16)
    ana.renovar_token()
    with abrir(ana, emparejados) as ws:
        ws.send_json({"tipo": "auth", "token": viejo})
        assert cierre(ws) == 4401


def test_ws_el_token_en_la_url_no_sirve(ana, emparejados):
    token = cabeceras(ana.usuario)["Authorization"].removeprefix("Bearer ")
    with ana.http.websocket_connect(f"/duelos/{emparejados}/ws?token={token}") as ws:
        ws.send_json({"tipo": "ping"})
        assert cierre(ws) == 4401


def test_ws_duelo_ajeno_o_inexistente(ana, emparejados, crear_usuario):
    otro = Jugador(crear_usuario("Otro_1"))
    with abrir(otro, emparejados) as ws:
        ws.send_json({"tipo": "auth", "token": cabeceras(otro.usuario)["Authorization"][7:]})
        assert cierre(ws) == 4404


def test_ws_cuenta_desactivada(ana, emparejados, db_session):
    ana.usuario.activo = False
    db_session.flush()
    with abrir(ana, emparejados) as ws:
        ws.send_json({"tipo": "auth", "token": cabeceras(ana.usuario)["Authorization"][7:]})
        assert cierre(ws) == 4403


def test_ws_origen_no_permitido(ana, emparejados):
    with abrir(ana, emparejados, headers={"Origin": "https://malicioso.example"}) as ws:
        assert cierre(ws) == 4403


def test_ws_demasiadas_conexiones_desde_una_ip(ana, emparejados, limite_por_ip, monkeypatch):
    from limits import parse

    from app import limites

    monkeypatch.setattr(limites, "WS_CONEXIONES", parse("2/minute"))
    for _ in range(2):
        with abrir(ana, emparejados) as ws:
            assert autenticarse(ws, ana.usuario)["tipo"] == "listo"

    with abrir(ana, emparejados) as ws:
        assert cierre(ws) == 4429


def test_ws_origen_permitido(ana, emparejados):
    with abrir(ana, emparejados, headers={"Origin": "https://app.ejemplo.org"}) as ws:
        assert autenticarse(ws, ana.usuario)["tipo"] == "listo"


def test_ws_sin_auth_en_10_s_cierra_con_4408(ana, emparejados, monkeypatch):
    from app.routers import duelos as router_duelos

    monkeypatch.setattr(router_duelos, "ESPERA_AUTENTICACION", 0.1)
    with abrir(ana, emparejados) as ws:
        assert cierre(ws) == 4408


def test_ws_avisos_de_emparejamiento_rondas_y_presencia(ana, beto, estudiantes):
    esperando = ana.buscar().json()
    with abrir(ana, esperando["id"]) as ws_ana:
        assert autenticarse(ws_ana, ana.usuario)["duelo"]["estado"] == "esperando"

        beto.buscar()
        emparejado = ws_ana.receive_json()
        assert emparejado["tipo"] == "emparejado"
        assert emparejado["duelo"]["rival"]["alias"] == "Beto_1" and emparejado["duelo"]["turno"] == "yo"

        with abrir(beto, esperando["id"]) as ws_beto:
            assert autenticarse(ws_beto, beto.usuario)["duelo"]["rival_en_linea"] is True
            presencia = ws_ana.receive_json()
            assert presencia["tipo"] == "rival_conectado" and presencia["duelo"]["rival_en_linea"] is True

            ana.jugar(esperando["id"], 1, "asistencia")
            aviso_beto = ws_beto.receive_json()
            assert aviso_beto["tipo"] == "ronda_resuelta"
            assert aviso_beto["duelo"]["rondas"][0]["elegido_por"] == "rival"
            assert aviso_beto["duelo"]["turno"] == "yo"
            assert ws_ana.receive_json()["tipo"] == "ronda_resuelta"  # también a quien jugó (otros dispositivos)

        desconectado = ws_ana.receive_json()
        assert desconectado["tipo"] == "rival_desconectado" and desconectado["duelo"]["rival_en_linea"] is False


def test_ws_aviso_de_fin_por_abandono(ana, beto, emparejados):
    with abrir(ana, emparejados) as ws_ana:
        autenticarse(ws_ana, ana.usuario)
        beto.abandonar(emparejados)
        aviso = ws_ana.receive_json()
        assert (aviso["tipo"], aviso["duelo"]["estado"], aviso["duelo"]["resultado"]) == ("terminado", "abandonado", "victoria")


# ---------- Concurrencia real de dos jugadores ----------

def _sesion(engine):
    from sqlalchemy.orm import Session

    return Session(engine)


@pytest.fixture
def dos_reales(engine):
    """Dos usuarios y 12 estudiantes guardados de verdad (fuera de la
    transacción del test), para hilos con conexiones propias. Limpia al final."""
    from app.models import Usuario

    with _sesion(engine) as db:
        a = Usuario(alias="Hilo_A", hash_password="$argon2id$x")
        b = Usuario(alias="Hilo_B", hash_password="$argon2id$x")
        db.add_all([a, b, *(estudiante(i) for i in range(12))])
        db.commit()
        ids = (a.id, b.id)
    yield ids
    with _sesion(engine) as db:
        for usuario_id in ids:
            db.delete(db.get(Usuario, usuario_id))  # sus duelos se van en CASCADE
        for e in db.scalars(select(Estudiante)):
            db.delete(e)
        db.commit()


def _en_hilos(*funciones):
    import threading

    barrera = threading.Barrier(len(funciones))
    resultados, errores = [None] * len(funciones), []

    def correr(i, f):
        try:
            barrera.wait()
            resultados[i] = f()
        except Exception as e:  # noqa: BLE001  (se comprueba en el test)
            errores.append(e)

    hilos = [threading.Thread(target=correr, args=(i, f)) for i, f in enumerate(funciones)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=30)
    return resultados, errores


def test_dos_jugadores_buscan_a_la_vez_y_se_emparejan_entre_si(engine, dos_reales):
    def buscar(usuario_id):
        def f():
            with _sesion(engine) as db:
                with reglas.candado(db, reglas.CANDADO_EMPAREJAMIENTO):
                    duelo, _ = reglas.buscar_rival(db, usuario_id)
                    db.commit()
                return duelo.id
        return f

    resultados, errores = _en_hilos(buscar(dos_reales[0]), buscar(dos_reales[1]))

    assert errores == []
    assert resultados[0] == resultados[1]  # el mismo duelo, no dos esperas
    with _sesion(engine) as db:
        duelos = db.scalars(select(Duelo).where(Duelo.modo == "jugador")).all()
        assert len(duelos) == 1 and duelos[0].estado == "en_juego"
        assert {duelos[0].jugador_a_id, duelos[0].jugador_b_id} == set(dos_reales)


def test_jugada_y_vencimiento_a_la_vez_resuelven_la_ronda_una_sola_vez(engine, dos_reales, monkeypatch):
    from app import reloj as reloj_app
    from app import schemas
    from app.models import Usuario
    from app.routers import duelos as router_duelos

    with _sesion(engine) as db:
        with reglas.candado(db, reglas.CANDADO_EMPAREJAMIENTO):
            reglas.buscar_rival(db, dos_reales[0])
            duelo, _ = reglas.buscar_rival(db, dos_reales[1])
            db.commit()
        duelo_id, vence = duelo.id, duelo.expira_turno_en
    monkeypatch.setattr(reloj_app, "ahora", lambda: vence)  # justo en el límite del turno de A

    def jugar_a():
        with _sesion(engine) as db:
            return router_duelos.jugar(duelo_id, schemas.JugadaEntrada(ronda=1, atributo="promedio"),
                                       db.get(Usuario, dos_reales[0]), db)

    def mirar_b():
        with _sesion(engine) as db:
            return router_duelos.obtener(duelo_id, db.get(Usuario, dos_reales[1]), db)

    resultados, errores = _en_hilos(jugar_a, mirar_b)

    # La jugada de A llega con el turno ya vencido: o la rechaza (tiempo_agotado)
    # o la resuelve el vencimiento; nunca las dos cosas.
    assert all(getattr(e, "detail", {}).get("codigo") == "tiempo_agotado" for e in errores)
    with _sesion(engine) as db:
        final = db.get(Duelo, duelo_id)
        assert [r["numero"] for r in final.rondas] == [1]
        assert final.rondas[0]["motivo"] == "tiempo" and final.puntos_a + final.puntos_b == 1


def test_el_candado_de_emparejamiento_se_suelta_aunque_otro_coja_la_conexion(engine, dos_reales):
    """GET_LOCK es de la conexión. Antes se pedía en la conexión de la sesión,
    que el commit devuelve al pool; si otra petición la cogía (lo normal con
    tráfico), el RELEASE_LOCK iba a otra conexión y el candado quedaba cogido:
    en producción bloqueó todas las búsquedas de rival."""
    from sqlalchemy import text

    with _sesion(engine) as db:
        with reglas.candado(db, reglas.CANDADO_EMPAREJAMIENTO):
            reglas.buscar_rival(db, dos_reales[0])
            db.commit()  # devuelve la conexión de la sesión al pool…
            otra_peticion = engine.connect()  # …y otra petición se la queda
            otra_peticion.execute(text("SELECT 1"))
    try:
        with engine.connect() as observador:
            libre = observador.execute(text("SELECT IS_FREE_LOCK(:n)"), {"n": reglas.CANDADO_EMPAREJAMIENTO}).scalar()
            assert libre == 1
    finally:
        otra_peticion.close()


def test_el_candado_pone_en_fila_y_se_suelta_tras_un_error(engine, dos_reales):
    from sqlalchemy import text

    with _sesion(engine) as db:
        with pytest.raises(RuntimeError):
            with reglas.candado(db, reglas.CANDADO_EMPAREJAMIENTO):
                with engine.connect() as otra:  # mientras está cogido, otro no lo obtiene
                    assert otra.execute(text("SELECT GET_LOCK(:n, 0)"), {"n": reglas.CANDADO_EMPAREJAMIENTO}).scalar() == 0
                raise RuntimeError("fallo a mitad")
    with engine.connect() as otra:
        assert otra.execute(text("SELECT IS_FREE_LOCK(:n)"), {"n": reglas.CANDADO_EMPAREJAMIENTO}).scalar() == 1
