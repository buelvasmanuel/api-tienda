"""Duelo de cartas contra la máquina (docs/CONTRATO_DUELO.md)."""

import json
from decimal import Decimal

import pytest
from sqlalchemy import select

from app import duelos as reglas
from app.models import Duelo, Estudiante
from tests.ayudas import cabeceras, codigo

ATRIBUTOS = ["promedio", "asistencia", "horas_estudio", "semestre"]


def estudiante(i: int, **campos) -> Estudiante:
    datos = {"nombre": f"Est{i}", "apellido": "Prueba", "edad": 20, "carrera": "Derecho", "semestre": 1 + i % 10,
             "promedio": Decimal("3.00") + Decimal(i) / 20, "ciudad": "Medellín",
             "horas_estudio": 5 + i, "asistencia": 50 + 2 * i} | campos
    return Estudiante(**datos)


@pytest.fixture
def estudiantes(db_session):
    """12 estudiantes: cuanto mayor el índice, más alto en todo (salvo semestre, que da vueltas)."""
    filas = [estudiante(i) for i in range(12)]
    db_session.add_all(filas)
    db_session.flush()
    return filas


@pytest.fixture
def jugador(client, crear_usuario):
    usuario = crear_usuario("duelista_1")
    client.headers.update(cabeceras(usuario))
    client.usuario = usuario
    return client


@pytest.fixture
def mazos_fijos(monkeypatch, db_session, estudiantes):
    """Reparto conocido: yo, los 5 mejores (gano todas las rondas que elija bien);
    la máquina, los 5 peores."""
    def repartir(poblacion):
        cartas = sorted((reglas.carta(e, poblacion) for e in poblacion),
                        key=lambda c: c["atributos"]["promedio"]["valor"])
        return cartas[-5:][::-1], cartas[:5]

    monkeypatch.setattr(reglas, "repartir", repartir)


def nuevo(client):
    respuesta = client.post("/duelos", json={"modo": "maquina"})
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


def jugada(client, duelo, ronda, atributo):
    return client.post(f"/duelos/{duelo['id']}/jugadas", json={"ronda": ronda, "atributo": atributo})


def jugar_entero(client, duelo, mi_atributo="promedio"):
    """Juega las 5 rondas (yo elijo `mi_atributo`; en las pares, la máquina)."""
    for ronda in range(1, 6):
        respuesta = jugada(client, duelo, ronda, mi_atributo if ronda % 2 else None)
        assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


# ---------- Reglas (funciones puras) ----------

def test_percentil():
    valores = [1, 2, 2, 3]

    assert [reglas.percentil(v, valores) for v in (1, 2, 3)] == [12, 50, 88]


def test_la_maquina_elige_su_mejor_percentil():
    carta_ = {"atributos": {"promedio": {"percentil": 40}, "asistencia": {"percentil": 90},
                            "horas_estudio": {"percentil": 90}, "semestre": {"percentil": 10}}}

    # Empate entre asistencia y horas → la primera en el orden de ATRIBUTOS.
    assert reglas.eleccion_de_la_maquina(carta_) == "asistencia"


@pytest.mark.parametrize(("a", "b", "esperado"), [(5, 3, "a"), (3, 5, "b"), (4, 4, "empate")])
def test_gana_el_valor_mas_alto(a, b, esperado):
    def c(v):
        return {"atributos": {"asistencia": {"valor": v}}}

    assert reglas.ganador(c(a), c(b), "asistencia") == esperado


def test_significado_de_cada_atributo():
    assert "el efecto, no con la causa" in reglas.significado("promedio", 1.0)
    assert "mejor promedio (r = 0,62)" in reglas.significado("asistencia", 0.62)
    assert "casi no se relaciona" in reglas.significado("semestre", -0.03)
    assert "peor promedio" in reglas.significado("horas_estudio", -0.4)


def test_explicacion_desde_cada_lado():
    mia = {"atributos": {"asistencia": {"valor": 92, "percentil": 81}}}
    otra = {"atributos": {"asistencia": {"valor": 70, "percentil": 22}}}
    ronda = {"numero": 1, "elegido_por": "a", "atributo": "asistencia", "ganador": "a", "r": 0.62}

    desde_a = reglas.explicacion(ronda, mia, otra, "a")
    desde_b = reglas.explicacion(ronda, otra, mia, "b")

    assert desde_a.startswith("Ganaste con asistencia: 92 % (percentil 81) frente a 70 % (percentil 22). Elegiste")
    assert desde_b.startswith("El rival ganó con asistencia: 70 % (percentil 22) frente a 92 % (percentil 81). El rival eligió")


# ---------- Crear y ver ----------

def test_duelo_exige_sesion(client):
    assert codigo(client.post("/duelos", json={"modo": "maquina"})) == "no_autenticado"
    assert codigo(client.get("/duelos")) == "no_autenticado"


def test_sin_suficientes_estudiantes(jugador, db_session):
    db_session.add_all(estudiante(i) for i in range(9))
    db_session.flush()

    respuesta = jugador.post("/duelos", json={"modo": "maquina"})

    assert respuesta.status_code == 409
    assert codigo(respuesta) == "sin_datos"


def test_crear_duelo(jugador, estudiantes):
    d = nuevo(jugador)

    assert (d["modo"], d["estado"], d["ronda"], d["turno"], d["rondas_total"]) == ("maquina", "en_juego", 1, "yo", 5)
    assert d["rival"] == {"tipo": "maquina", "alias": "Búho Máquina"}
    assert d["marcador"] == {"yo": 0, "rival": 0} and d["rondas"] == [] and d["resultado"] is None
    assert d["duelos_con_xp_hoy"] == {"jugados": 0, "maximo": 3}
    assert [a["codigo"] for a in d["atributos"]] == ATRIBUTOS
    assert set(d["mi_carta"]["atributos"]) == set(ATRIBUTOS)
    assert len(d["id"]) == 32 and d["expira_turno_en"] is None


def test_la_respuesta_no_revela_ninguna_otra_carta(jugador, estudiantes, db_session):
    d = nuevo(jugador)
    duelo = db_session.get(Duelo, d["id"])
    otras = {c["estudiante_id"] for c in duelo.mazo_a[1:] + duelo.mazo_b}

    texto = json.dumps(d)

    assert all(f'"estudiante_id": {i},' not in texto for i in otras)
    assert d["mi_carta"]["estudiante_id"] == duelo.mazo_a[0]["estudiante_id"]


def test_el_reparto_usa_10_estudiantes_distintos(jugador, estudiantes, db_session):
    duelo = db_session.get(Duelo, nuevo(jugador)["id"])

    ids = [c["estudiante_id"] for c in duelo.mazo_a + duelo.mazo_b]
    assert len(ids) == len(set(ids)) == 10


def test_los_percentiles_son_sobre_todos_los_estudiantes(jugador, estudiantes, db_session):
    duelo = db_session.get(Duelo, nuevo(jugador)["id"])
    carta_ = duelo.mazo_a[0]
    e = next(x for x in estudiantes if x.id == carta_["estudiante_id"])

    esperado = reglas.percentil(e.asistencia, [x.asistencia for x in estudiantes])
    assert carta_["atributos"]["asistencia"] == {"valor": e.asistencia, "percentil": esperado}


def test_modo_desconocido(jugador, estudiantes):
    assert jugador.post("/duelos", json={"modo": "jugador"}).status_code == 422


def test_un_duelo_ajeno_no_se_ve(jugador, estudiantes, client, crear_usuario):
    d = nuevo(jugador)
    otro = cabeceras(crear_usuario("otro_1"))

    assert codigo(client.get(f"/duelos/{d['id']}", headers=otro)) == "duelo_no_encontrado"
    assert codigo(client.post(f"/duelos/{d['id']}/jugadas", json={"ronda": 1, "atributo": "promedio"},
                              headers=otro)) == "duelo_no_encontrado"
    assert codigo(jugador.get("/duelos/" + "0" * 32)) == "duelo_no_encontrado"


# ---------- Jugar ----------

def test_partida_completa(jugador, mazos_fijos):
    d = nuevo(jugador)

    r1 = jugada(jugador, d, 1, "promedio").json()
    ronda1 = r1["rondas"][0]
    assert (ronda1["elegido_por"], ronda1["atributo"], ronda1["ganador"]) == ("yo", "promedio", "yo")
    assert ronda1["explicacion"].startswith("Ganaste con promedio")
    assert (r1["ronda"], r1["turno"], r1["marcador"]) == (2, "rival", {"yo": 1, "rival": 0})

    r2 = jugada(jugador, d, 2, None).json()
    ronda2 = r2["rondas"][1]
    assert ronda2["elegido_por"] == "rival"
    assert ronda2["atributo"] == reglas.eleccion_de_la_maquina(ronda2["carta_rival"])

    for ronda in (3, 4, 5):  # las rondas 1 y 2 ya están jugadas
        final = jugada(jugador, d, ronda, "promedio" if ronda % 2 else None).json()

    assert (final["estado"], final["ronda"], final["turno"], final["mi_carta"]) == ("terminado", None, None, None)
    assert len(final["rondas"]) == 5
    yo = sum(r["ganador"] == "yo" for r in final["rondas"])
    rival = sum(r["ganador"] == "rival" for r in final["rondas"])
    assert final["marcador"] == {"yo": yo, "rival": rival}
    assert final["resultado"] == ("victoria" if yo > rival else "derrota" if rival > yo else "empate")
    assert final["xp_ganada"] == reglas.XP_POR_RESULTADO[final["resultado"]]
    assert final["progreso"]["xp"] == final["xp_ganada"]


def test_el_ganador_sale_de_los_valores(jugador, estudiantes):
    final = jugar_entero(jugador, nuevo(jugador), "asistencia")

    for r in final["rondas"]:
        mio = r["mi_carta"]["atributos"][r["atributo"]]["valor"]
        suyo = r["carta_rival"]["atributos"][r["atributo"]]["valor"]
        assert r["ganador"] == ("yo" if mio > suyo else "rival" if suyo > mio else "empate")


def test_primera_victoria_da_la_insignia_duelista(jugador, mazos_fijos):
    final = jugar_entero(jugador, nuevo(jugador))

    assert final["resultado"] == "victoria"
    assert [i["codigo"] for i in final["insignias_nuevas"]] == ["duelista"]
    assert jugador.get("/juego/progreso").json()["xp"] == 30


def test_insignias_y_progreso_solo_en_la_jugada_final(jugador, mazos_fijos):
    d = nuevo(jugador)

    intermedia = jugada(jugador, d, 1, "promedio").json()

    assert intermedia["insignias_nuevas"] == [] and intermedia["progreso"] is None


def test_no_es_tu_turno(jugador, estudiantes):
    d = nuevo(jugador)
    jugada(jugador, d, 1, "promedio")

    respuesta = jugada(jugador, d, 2, "asistencia")

    assert respuesta.status_code == 409 and codigo(respuesta) == "no_es_tu_turno"


def test_en_tu_turno_hay_que_elegir(jugador, estudiantes):
    respuesta = jugada(jugador, nuevo(jugador), 1, None)

    assert respuesta.status_code == 422 and codigo(respuesta) == "atributo_requerido"


def test_ronda_incorrecta(jugador, estudiantes):
    respuesta = jugada(jugador, nuevo(jugador), 3, "promedio")

    assert respuesta.status_code == 409
    assert (codigo(respuesta), respuesta.json()["detail"]["ronda_actual"]) == ("ronda_incorrecta", 1)


@pytest.mark.parametrize("cuerpo", [{"ronda": 1, "atributo": "edad"}, {"ronda": 6, "atributo": "promedio"},
                                    {"ronda": 1, "atributo": "promedio", "ganador": "yo"}])
def test_jugadas_invalidas(jugador, estudiantes, cuerpo):
    d = nuevo(jugador)

    assert jugador.post(f"/duelos/{d['id']}/jugadas", json=cuerpo).status_code == 422


def test_repetir_la_misma_jugada_no_la_juega_dos_veces(jugador, estudiantes):
    d = nuevo(jugador)
    primera = jugada(jugador, d, 1, "promedio").json()
    jugada(jugador, d, 2, None)

    otra_vez = jugada(jugador, d, 1, "promedio")          # reintento de la ronda 1
    otra_vez_rival = jugada(jugador, d, 2, None)          # reintento de la ronda 2

    assert otra_vez.status_code == otra_vez_rival.status_code == 200
    assert len(otra_vez.json()["rondas"]) == 2 and otra_vez.json()["ronda"] == 3
    assert otra_vez.json()["rondas"][0] == primera["rondas"][0]


def test_repetir_una_ronda_con_otro_atributo(jugador, estudiantes):
    d = nuevo(jugador)
    jugada(jugador, d, 1, "promedio")

    respuesta = jugada(jugador, d, 1, "asistencia")

    assert codigo(respuesta) == "ronda_incorrecta"


def test_tras_terminar(jugador, estudiantes):
    d = nuevo(jugador)
    final = jugar_entero(jugador, d)

    assert jugada(jugador, d, 5, "promedio").json() == final | {"insignias_nuevas": [], "progreso": None,
                                                               "duelos_con_xp_hoy": final["duelos_con_xp_hoy"]}
    assert codigo(jugador.post(f"/duelos/{d['id']}/abandonar")) == "duelo_terminado"


# ---------- Abandonar y un solo duelo en juego ----------

def test_abandonar(jugador, estudiantes):
    d = nuevo(jugador)
    jugada(jugador, d, 1, "promedio")

    abandonado = jugador.post(f"/duelos/{d['id']}/abandonar").json()
    otra_vez = jugador.post(f"/duelos/{d['id']}/abandonar")

    assert (abandonado["estado"], abandonado["resultado"], abandonado["xp_ganada"]) == ("abandonado", "derrota", 0)
    assert otra_vez.status_code == 200 and otra_vez.json()["estado"] == "abandonado"
    assert codigo(jugada(jugador, d, 2, None)) == "duelo_terminado"
    assert jugador.get("/juego/progreso").json()["xp"] == 0


def test_un_duelo_nuevo_abandona_el_que_estaba_en_juego(jugador, estudiantes):
    primero = nuevo(jugador)
    segundo = nuevo(jugador)

    assert jugador.get(f"/duelos/{primero['id']}").json()["estado"] == "abandonado"
    en_juego = jugador.get("/duelos?estado=en_juego").json()["duelos"]
    assert [x["id"] for x in en_juego] == [segundo["id"]]


def test_listar_mis_duelos(jugador, estudiantes):
    terminado = jugar_entero(jugador, nuevo(jugador))
    en_juego = nuevo(jugador)

    todos = jugador.get("/duelos").json()["duelos"]
    solo_terminados = jugador.get("/duelos?estado=terminado").json()["duelos"]

    assert {x["id"] for x in todos} == {terminado["id"], en_juego["id"]}
    assert [x["id"] for x in solo_terminados] == [terminado["id"]]
    assert solo_terminados[0]["xp_ganada"] == terminado["xp_ganada"]


# ---------- XP: máximo diario ----------

def test_solo_3_duelos_al_dia_dan_xp(jugador, mazos_fijos, reloj):
    xp = [jugar_entero(jugador, nuevo(jugador))["xp_ganada"] for _ in range(4)]

    assert xp == [30, 30, 30, 0]
    # (el reloj está quieto: los 4 duelos tienen la misma hora y el orden de la lista no sirve)
    assert sorted(x["xp_ganada"] for x in jugador.get("/duelos?estado=terminado").json()["duelos"]) == [0, 30, 30, 30]
    assert nuevo(jugador)["duelos_con_xp_hoy"] == {"jugados": 3, "maximo": 3}

    reloj.avanzar(days=1)
    jugador.headers.update(cabeceras(jugador.usuario))  # el access anterior caducó
    assert jugar_entero(jugador, nuevo(jugador))["xp_ganada"] == 30


def test_los_duelos_cuentan_para_la_racha_y_el_ranking(jugador, mazos_fijos):
    jugar_entero(jugador, nuevo(jugador))

    p = jugador.get("/juego/progreso").json()
    ranking = jugador.get("/juego/ranking").json()

    assert (p["xp"], p["racha_dias"]) == (30, 1)
    assert ranking["yo"] == {"posicion": 1, "xp": 30}


def test_los_abandonados_no_cuentan_para_la_racha(jugador, estudiantes):
    d = nuevo(jugador)
    jugador.post(f"/duelos/{d['id']}/abandonar")

    assert jugador.get("/juego/progreso").json()["racha_dias"] == 0


def test_borrar_un_estudiante_no_cambia_un_duelo_en_juego(jugador, estudiantes, admin_client, db_session):
    d = nuevo(jugador)

    admin_client.delete(f"/estudiantes/{d['mi_carta']['estudiante_id']}")

    assert jugador.get(f"/duelos/{d['id']}").json()["mi_carta"] == d["mi_carta"]
    assert jugada(jugador, d, 1, "promedio").status_code == 200


def test_dos_envios_simultaneos_de_la_jugada_final(engine):
    """Concurrencia real: la misma jugada final enviada dos veces a la vez se
    resuelve una sola vez (FOR UPDATE sobre el duelo + jugadas idempotentes).
    Fuera de la transacción del test: limpia lo que crea al final."""
    import threading

    from sqlalchemy.orm import Session

    from app import schemas
    from app.models import Usuario
    from app.routers import duelos as router_duelos

    with Session(engine) as db:
        usuario = Usuario(alias="concurrente_duelo", hash_password="$argon2id$x")
        db.add(usuario)
        db.add_all(estudiante(i) for i in range(12))
        db.flush()
        duelo = reglas.crear_contra_maquina(db, usuario.id)
        for ronda in range(1, 5):
            reglas.jugar(db, duelo, "a", ronda, "promedio" if ronda % 2 else None)
        db.commit()
        usuario_id, duelo_id = usuario.id, duelo.id

    barrera = threading.Barrier(2)
    resultados, errores = [], []

    def jugar_en_hilo():
        try:
            with Session(engine) as db:
                jugador = db.get(Usuario, usuario_id)
                barrera.wait()
                r = router_duelos.jugar(duelo_id, schemas.JugadaEntrada(ronda=5, atributo="promedio"), jugador, db)
                resultados.append(r)
        except Exception as e:  # noqa: BLE001  (se comprueba abajo)
            errores.append(e)

    hilos = [threading.Thread(target=jugar_en_hilo) for _ in range(2)]
    try:
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(timeout=30)

        assert errores == []
        assert len(resultados) == 2 and all(len(r.rondas) == 5 and r.estado == "terminado" for r in resultados)
        with Session(engine) as db:
            final = db.get(Duelo, duelo_id)
            assert len(final.rondas) == 5
            assert final.puntos_a + final.puntos_b <= 5
            assert final.xp_a == reglas.XP_POR_RESULTADO[final.resultado_a]
        # Solo una de las dos respuestas trae el progreso (la que terminó el duelo).
        assert sum(r.progreso is not None for r in resultados) == 1
    finally:
        with Session(engine) as db:
            db.delete(db.get(Usuario, usuario_id))  # su duelo se va en CASCADE
            for e in db.scalars(select(Estudiante)):
                db.delete(e)
            db.commit()
