"""Fase 5: casos, XP, niveles, racha, insignias, ranking y rescates guardados
(docs/CONTRATO_JUEGO.md)."""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app import casos, progreso
from app.main import app
from app.modelo_juego import ModeloJuego
from app.models import Estudiante, InsigniaObtenida, IntentoCaso, Rescate
from app.routers.prediccion import obtener_modelo_juego
from tests.ayudas import cabeceras, codigo
from tests.test_laboratorio import MODELO as MODELO_SINTETICO

MODELO = ModeloJuego.cargar()


def estudiante(**campos) -> dict:
    return {"nombre": "Ana", "apellido": "Pérez", "edad": 20, "carrera": "Derecho", "semestre": 3,
            "promedio": Decimal("3.50"), "ciudad": "Medellín", "horas_estudio": 12, "asistencia": 85} | campos


@pytest.fixture
def datos(db_session):
    """Datos pequeños con respuestas conocidas:
    - carreras: Derecho 3, Medicina 2 (Medicina 50 % en riesgo, Derecho 0 %).
    - semestres: 3 y 5 empatan (2 y 2), 1 tiene 1.
    - ciudades: Medellín 3, Cali 2.
    """
    filas = [
        estudiante(carrera="Derecho", semestre=3, promedio=Decimal("4.20"), asistencia=95, horas_estudio=20),
        estudiante(carrera="Derecho", semestre=3, promedio=Decimal("3.80"), asistencia=90, horas_estudio=18),
        estudiante(carrera="Derecho", semestre=5, promedio=Decimal("3.40"), asistencia=85, ciudad="Cali", horas_estudio=14),
        estudiante(carrera="Medicina", semestre=5, promedio=Decimal("2.50"), asistencia=60, ciudad="Cali", horas_estudio=4, edad=19),
        estudiante(carrera="Medicina", semestre=1, promedio=Decimal("3.10"), asistencia=80, horas_estudio=10, edad=22),
    ]
    db_session.add_all(Estudiante(**f) for f in filas)
    db_session.flush()
    return filas


def evaluar(codigo_caso: str, db_session, modelo=MODELO) -> casos.Evaluacion:
    return casos.CASOS[codigo_caso].evaluar(casos.cargar_estudiantes(db_session), modelo)


# ---------- Casos: respuestas calculadas con los datos ----------

def test_catalogo_de_9_casos_en_3_niveles():
    niveles = [c.nivel for c in casos.CASOS.values()]

    assert len(casos.CASOS) == 9
    assert niveles == ["novato"] * 3 + ["investigador"] * 3 + ["experto"] * 3
    assert {c.xp for c in casos.CASOS.values()} == {20, 40, 60}


def test_carrera_con_mas_estudiantes(datos, db_session):
    ev = evaluar("carrera_mas_estudiantes", db_session)

    assert ev.correctas == {"Derecho"}
    assert [o.valor for o in ev.opciones] == ["Derecho", "Medicina"]
    assert ev.datos["Medicina"] == "Medicina: 2 estudiantes."


def test_empate_todas_las_empatadas_son_correctas(datos, db_session):
    ev = evaluar("semestre_mas_estudiantes", db_session)

    assert ev.correctas == {"3", "5"}
    assert "empatan" in ev.explicacion
    assert [o.texto for o in ev.opciones] == ["Semestre 1", "Semestre 3", "Semestre 5"]


def test_las_respuestas_cambian_si_cambian_los_datos(datos, db_session):
    assert evaluar("carrera_mas_estudiantes", db_session).correctas == {"Derecho"}

    db_session.add_all([Estudiante(**estudiante(carrera="Medicina")) for _ in range(2)])
    db_session.flush()

    assert evaluar("carrera_mas_estudiantes", db_session).correctas == {"Medicina"}


def test_carrera_mas_riesgo_es_por_porcentaje(datos, db_session):
    ev = evaluar("carrera_mas_riesgo", db_session)

    assert ev.correctas == {"Medicina"}
    assert ev.datos["Medicina"] == "Medicina: 50,00 % de sus estudiantes está en riesgo."


def test_mejor_promedio(datos, db_session):
    assert evaluar("carrera_mejor_promedio", db_session).correctas == {"Derecho"}


def test_ciudad_solo_ofrece_las_6_con_mas_estudiantes(db_session):
    db_session.add_all(Estudiante(**estudiante(ciudad=f"Ciudad {i}")) for i in range(8))
    db_session.add(Estudiante(**estudiante(ciudad="Ciudad 7")))
    db_session.flush()

    ev = evaluar("ciudad_mas_estudiantes", db_session)

    assert len(ev.opciones) == 6
    assert ev.correctas == {"Ciudad 7"}
    assert "Ciudad 7" in {o.valor for o in ev.opciones}


@pytest.mark.parametrize(("asistencia_riesgo", "correcta"), [(60, "menos"), (95, "mas"), (89, "igual")])
def test_asistencia_en_riesgo(db_session, asistencia_riesgo, correcta):
    db_session.add_all([Estudiante(**estudiante(asistencia=90)),
                        Estudiante(**estudiante(asistencia=asistencia_riesgo, promedio=Decimal("2.00")))])
    db_session.flush()

    ev = evaluar("asistencia_en_riesgo", db_session)

    assert ev.correctas == {correcta}
    assert ev.datos == {}  # al fallar no se muestra ningún dato: revelaría la respuesta


@pytest.mark.parametrize(
    ("puntos", "correcta"),
    [([(2, "2.0"), (10, "3.0"), (20, "4.5")], "positiva"),
     ([(2, "4.5"), (10, "3.0"), (20, "2.0")], "negativa"),
     ([(2, "3.0"), (10, "4.0"), (20, "3.0"), (30, "4.0"), (40, "3.0")], "ninguna")],
)
def test_horas_y_promedio(db_session, puntos, correcta):
    db_session.add_all(Estudiante(**estudiante(horas_estudio=h, promedio=Decimal(p))) for h, p in puntos)
    db_session.flush()

    assert evaluar("horas_y_promedio", db_session).correctas == {correcta}


def test_variable_mas_influyente_sale_del_modelo(datos, db_session):
    # En el modelo sintético: horas −0.25, asistencia −0.125 → gana horas.
    assert evaluar("variable_mas_influyente", db_session, MODELO_SINTETICO).correctas == {"horas_estudio"}
    # En el modelo congelado real gana la asistencia (coeficiente −2.07).
    assert evaluar("variable_mas_influyente", db_session).correctas == {"asistencia"}


def test_edad_patron_espurio(db_session):
    # Edad sin relación con el promedio (r = 0) → "espurio".
    for edad, promedio in [(18, "3.0"), (18, "4.0"), (30, "3.0"), (30, "4.0")]:
        db_session.add(Estudiante(**estudiante(edad=edad, promedio=Decimal(promedio))))
    db_session.flush()

    ev = evaluar("edad_y_riesgo", db_session)

    assert ev.correctas == {"espurio"}
    assert "menos riesgo" in ev.enunciado  # el coeficiente real de la edad es negativo
    assert "causa" not in ev.correctas


def test_edad_con_relacion_real(db_session):
    for edad, promedio in [(18, "2.5"), (22, "3.2"), (26, "3.9"), (30, "4.6")]:
        db_session.add(Estudiante(**estudiante(edad=edad, promedio=Decimal(promedio))))
    db_session.flush()

    assert evaluar("edad_y_riesgo", db_session).correctas == {"asociacion"}


# ---------- Reglas de progreso ----------

@pytest.mark.parametrize(("xp", "nivel"), [(0, "Novato"), (149, "Novato"), (150, "Investigador"),
                                            (399, "Investigador"), (400, "Inspector"), (5000, "Jefe de datos")])
def test_niveles(xp, nivel):
    assert progreso.nivel_de(xp).nombre == nivel


def test_ultimo_nivel_no_tiene_siguiente():
    assert progreso.siguiente_nivel(progreso.nivel_de(800)) is None
    assert progreso.siguiente_nivel(progreso.nivel_de(0)).nombre == "Investigador"


@pytest.mark.parametrize(("intento", "xp"), [(1, 40), (2, 20), (3, 10), (7, 10)])
def test_xp_segun_el_intento(intento, xp):
    assert progreso.xp_de_caso(40, intento) == xp


def test_racha():
    hoy = datetime(2026, 10, 14).date()
    dias = {hoy - timedelta(days=n) for n in (0, 1, 2, 4)}

    assert progreso.racha(dias, hoy) == 3
    assert progreso.racha(dias - {hoy}, hoy) == 2  # si hoy aún no jugó, cuenta hasta ayer
    assert progreso.racha({hoy - timedelta(days=2)}, hoy) == 0


def test_posiciones_con_empate():
    filas = [(1, "a", 50), (2, "b", 30), (3, "c", 30), (4, "d", 10)]

    assert progreso.posiciones(filas) == [1, 2, 2, 4]


def test_inicio_de_semana_en_hora_de_colombia(reloj):
    # Lunes 12/10/2026 a las 03:00 UTC = domingo 11/10 a las 22:00 en Colombia.
    reloj.momento = datetime(2026, 10, 12, 3, 0)
    assert progreso.inicio_semana() == datetime(2026, 10, 5, 5, 0)

    reloj.momento = datetime(2026, 10, 12, 5, 0)  # lunes 00:00 en Colombia
    assert progreso.inicio_semana() == datetime(2026, 10, 12, 5, 0)


# ---------- Endpoints de casos ----------

@pytest.fixture
def jugador(client, crear_usuario):
    usuario = crear_usuario("detective_1")
    client.headers.update(cabeceras(usuario))
    client.usuario = usuario
    return client


def responder(client, codigo_caso, respuesta):
    return client.post(f"/juego/casos/{codigo_caso}/responder", json={"respuesta": respuesta})


def test_juego_exige_sesion(client):
    for ruta in ["/juego/casos", "/juego/casos/carrera_mas_estudiantes", "/juego/progreso", "/juego/ranking"]:
        assert codigo(client.get(ruta)) == "no_autenticado"


def test_listar_casos(jugador, datos):
    lista = jugador.get("/juego/casos").json()["casos"]

    assert [c["codigo"] for c in lista] == list(casos.CASOS)
    assert lista[0] == {"codigo": "carrera_mas_estudiantes", "nivel": "novato", "titulo": "El edificio más lleno",
                        "xp": 20, "resuelto": False, "intentos": 0, "xp_ganada": 0}


def test_detalle_de_un_caso_no_revela_la_respuesta(jugador, datos):
    datos_caso = jugador.get("/juego/casos/carrera_mas_riesgo").json()

    assert datos_caso["grafica"] == "/estudiantes/estadisticas/riesgo-por-carrera"
    assert datos_caso["opciones"] == [{"valor": "Derecho", "texto": "Derecho"},
                                      {"valor": "Medicina", "texto": "Medicina"}]
    assert set(datos_caso) == {"codigo", "nivel", "titulo", "enunciado", "opciones", "grafica", "xp",
                               "resuelto", "intentos"}


def test_caso_inexistente(jugador, datos):
    assert codigo(jugador.get("/juego/casos/no_existe")) == "caso_no_encontrado"
    assert codigo(responder(jugador, "no_existe", "x")) == "caso_no_encontrado"


def test_sin_estudiantes(jugador):
    respuesta = jugador.get("/juego/casos/carrera_mas_estudiantes")

    assert respuesta.status_code == 409
    assert codigo(respuesta) == "sin_datos"


def test_acertar_a_la_primera(jugador, datos):
    respuesta = responder(jugador, "carrera_mas_estudiantes", "Derecho")

    assert respuesta.status_code == 200
    r = respuesta.json()
    assert (r["correcto"], r["xp_ganada"], r["intento"], r["pista"]) == (True, 20, 1, None)
    assert r["dato"] == "Derecho: 3 estudiantes."
    assert "Derecho tiene 3" in r["explicacion"]
    assert [i["codigo"] for i in r["insignias_nuevas"]] == ["primera_pista"]
    assert r["progreso"] == {"xp": 20, "nivel": "Novato", "subio_de_nivel": False}


def test_fallar_da_pista_y_el_dato_de_la_opcion_elegida(jugador, datos):
    r = responder(jugador, "carrera_mas_estudiantes", "Medicina").json()

    assert (r["correcto"], r["xp_ganada"], r["explicacion"]) == (False, 0, None)
    assert r["dato"] == "Medicina: 2 estudiantes."
    assert "barra más larga" in r["pista"]
    assert r["insignias_nuevas"] == []


def test_la_xp_baja_con_los_intentos_y_solo_se_gana_una_vez(jugador, datos):
    responder(jugador, "carrera_mas_riesgo", "Derecho")  # falla
    responder(jugador, "carrera_mas_riesgo", "Derecho")  # falla

    tercera = responder(jugador, "carrera_mas_riesgo", "Medicina").json()
    otra_vez = responder(jugador, "carrera_mas_riesgo", "Medicina").json()

    assert (tercera["correcto"], tercera["xp_ganada"], tercera["intento"]) == (True, 10, 3)
    assert (otra_vez["correcto"], otra_vez["xp_ganada"], otra_vez["intento"]) == (True, 0, 4)
    resumen = next(c for c in jugador.get("/juego/casos").json()["casos"] if c["codigo"] == "carrera_mas_riesgo")
    assert (resumen["resuelto"], resumen["intentos"], resumen["xp_ganada"]) == (True, 4, 10)


def test_en_empate_valen_todas(jugador, datos):
    assert responder(jugador, "semestre_mas_estudiantes", "5").json()["correcto"] is True


def test_en_casos_de_si_o_no_fallar_no_revela_datos(jugador, datos):
    r = responder(jugador, "asistencia_en_riesgo", "mas").json()

    assert r["correcto"] is False and r["dato"] is None


def test_respuesta_que_no_es_una_opcion(jugador, datos):
    respuesta = responder(jugador, "carrera_mas_estudiantes", "Astrología")

    assert respuesta.status_code == 422
    assert respuesta.json()["detail"]["codigo"] == "respuesta_invalida"
    assert respuesta.json()["detail"]["opciones"] == ["Derecho", "Medicina"]


def test_el_cliente_no_puede_enviar_xp(jugador, datos):
    respuesta = jugador.post("/juego/casos/carrera_mas_estudiantes/responder",
                             json={"respuesta": "Derecho", "xp": 1000, "correcto": True})

    assert respuesta.status_code == 422


def test_subir_de_nivel(jugador, datos, db_session):
    # 140 XP previos (inventados para el test) + 20 de este caso = 160 → Investigador.
    db_session.add(IntentoCaso(usuario_id=jugador.usuario.id, caso="otro", respuesta="x", correcto=False, xp=140))
    db_session.flush()

    r = responder(jugador, "carrera_mas_estudiantes", "Derecho").json()

    assert r["progreso"] == {"xp": 160, "nivel": "Investigador", "subio_de_nivel": True}


# ---------- Insignias ----------

def resolver_todos(jugador, de_nivel=None):
    respuestas = {"carrera_mas_estudiantes": "Derecho", "ciudad_mas_estudiantes": "Medellín",
                  "semestre_mas_estudiantes": "3", "carrera_mas_riesgo": "Medicina",
                  "carrera_mejor_promedio": "Derecho", "asistencia_en_riesgo": "menos",
                  "horas_y_promedio": "positiva", "variable_mas_influyente": "asistencia",
                  "edad_y_riesgo": "asociacion"}
    nuevas = []
    for caso, respuesta in respuestas.items():
        if de_nivel is None or casos.CASOS[caso].nivel == de_nivel:
            r = responder(jugador, caso, respuesta).json()
            assert r["correcto"], (caso, r)
            nuevas += [i["codigo"] for i in r["insignias_nuevas"]]
    return nuevas


def test_ojo_de_halcon_y_mente_experta(jugador, datos):
    nuevas = resolver_todos(jugador)

    assert nuevas == ["primera_pista", "ojo_de_halcon", "mente_experta"]
    progreso_ = jugador.get("/juego/progreso").json()
    assert progreso_["xp"] == 3 * 20 + 3 * 40 + 3 * 60
    assert progreso_["casos_resueltos"] == progreso_["casos_total"] == 9


def test_ojo_de_halcon_solo_cuenta_aciertos_al_primer_intento(jugador, datos):
    responder(jugador, "carrera_mas_estudiantes", "Medicina")  # primeros intentos fallidos
    responder(jugador, "carrera_mas_riesgo", "Derecho")
    nuevas = resolver_todos(jugador, "novato") + resolver_todos(jugador, "investigador")

    assert "ojo_de_halcon" not in nuevas  # 6 resueltos, pero solo 4 al primer intento…

    nuevas = resolver_todos(jugador, "experto")
    assert "ojo_de_halcon" in nuevas  # …y con los 3 expertos ya son 7


def test_las_insignias_no_se_repiten(jugador, datos, db_session):
    responder(jugador, "carrera_mas_estudiantes", "Derecho")
    responder(jugador, "carrera_mas_estudiantes", "Derecho")

    assert db_session.scalars(select(InsigniaObtenida.codigo)).all() == ["primera_pista"]


def test_constancia_tras_3_dias_seguidos(jugador, datos, reloj):
    for dia, caso in enumerate(["carrera_mas_estudiantes", "carrera_mas_riesgo", "carrera_mejor_promedio"]):
        respuesta = {"carrera_mas_estudiantes": "Derecho", "carrera_mas_riesgo": "Medicina",
                     "carrera_mejor_promedio": "Derecho"}[caso]
        nuevas = [i["codigo"] for i in responder(jugador, caso, respuesta).json()["insignias_nuevas"]]
        if dia < 2:
            reloj.avanzar(days=1)
            jugador.headers.update(cabeceras(jugador.usuario))  # el access anterior ya caducó

    assert "constancia" in nuevas
    assert jugador.get("/juego/progreso").json()["racha_dias"] == 3


# ---------- Progreso ----------

def test_progreso_inicial(jugador):
    p = jugador.get("/juego/progreso").json()

    assert p["alias"] == "detective_1"
    assert (p["xp"], p["racha_dias"], p["casos_resueltos"], p["rescates_logrados"]) == (0, 0, 0, 0)
    assert p["nivel"] == {"numero": 1, "nombre": "Novato", "xp_desde": 0}
    assert p["siguiente_nivel"] == {"numero": 2, "nombre": "Investigador", "xp_desde": 150}
    assert len(p["insignias"]) == 8
    assert all(i["obtenida_en"] is None for i in p["insignias"])


def test_progreso_muestra_las_insignias_con_fecha(jugador, datos):
    responder(jugador, "carrera_mas_estudiantes", "Derecho")

    insignias = {i["codigo"]: i for i in jugador.get("/juego/progreso").json()["insignias"]}

    assert insignias["primera_pista"]["obtenida_en"].endswith("Z")
    assert insignias["primer_rescate"]["obtenida_en"] is None


# ---------- Ranking y vista del docente ----------

def dar_xp(db_session, usuario, xp, cuando=None):
    db_session.add(IntentoCaso(usuario_id=usuario.id, caso="x", respuesta="x", correcto=True, xp=xp,
                               **({"creado_en": cuando} if cuando else {})))
    db_session.flush()


def test_ranking(client, crear_usuario, db_session, reloj):
    ana, beto, caro = crear_usuario("Ana_1"), crear_usuario("Beto_1"), crear_usuario("Caro_1")
    profe = crear_usuario("Profe_1", rol="docente")
    inactivo = crear_usuario("Inactivo_1", activo=False)
    dar_xp(db_session, ana, 50)
    dar_xp(db_session, beto, 30)
    dar_xp(db_session, caro, 30)
    dar_xp(db_session, profe, 999)     # no es jugador: no aparece
    dar_xp(db_session, inactivo, 999)  # desactivado: no aparece
    dar_xp(db_session, ana, 500, cuando=progreso.inicio_semana() - timedelta(seconds=1))  # semana pasada

    semana = client.get("/juego/ranking", headers=cabeceras(beto)).json()
    siempre = client.get("/juego/ranking?periodo=siempre", headers=cabeceras(beto)).json()

    assert [(j["posicion"], j["alias"], j["xp"], j["soy_yo"]) for j in semana["jugadores"]] == [
        (1, "Ana_1", 50, False), (2, "Beto_1", 30, True), (2, "Caro_1", 30, False)]
    assert semana["yo"] == {"posicion": 2, "xp": 30}
    assert semana["desde"].endswith("Z")
    assert siempre["jugadores"][0] == {"posicion": 1, "alias": "Ana_1", "xp": 550, "nivel": "Inspector",
                                       "soy_yo": False}
    assert siempre["desde"] is None


def test_ranking_yo_es_null_sin_xp(client, crear_usuario):
    assert client.get("/juego/ranking", headers=cabeceras(crear_usuario())).json()["yo"] is None


def test_ranking_periodo_invalido(jugador):
    assert jugador.get("/juego/ranking?periodo=mes").status_code == 422


def test_jugadores_para_el_docente(client, crear_usuario, db_session):
    ana = crear_usuario("Ana_1")
    crear_usuario("Beto_1")
    profe = crear_usuario("Profe_1", rol="docente")
    dar_xp(db_session, ana, 160)

    lista = client.get("/juego/jugadores", headers=cabeceras(profe)).json()

    assert [j["alias"] for j in lista] == ["Ana_1", "Beto_1"]  # solo jugadores
    assert (lista[0]["xp"], lista[0]["nivel"]) == (160, "Investigador")
    assert lista[0]["ultima_actividad"].endswith("Z")
    assert lista[1]["ultima_actividad"] is None


def test_un_jugador_no_ve_a_los_demas(jugador):
    assert codigo(jugador.get("/juego/jugadores")) == "permiso_denegado"


# ---------- Rescates guardados (CONTRATO_JUEGO §6) ----------

@pytest.fixture
def laboratorio(jugador, admin_client):
    """Jugador con el modelo sintético de tests/test_laboratorio.py y dos casos."""
    app.dependency_overrides[obtener_modelo_juego] = lambda: MODELO_SINTETICO
    ids = []
    for _ in range(2):
        ids.append(admin_client.post("/estudiantes/", json={
            "nombre": "Ana", "apellido": "Gómez", "edad": 20, "carrera": "Derecho", "semestre": 3,
            "promedio": "2.50", "ciudad": "Medellín", "horas_estudio": 10, "asistencia": 50}).json()["id"])
    jugador.casos = ids
    yield jugador
    app.dependency_overrides.pop(obtener_modelo_juego, None)


def rescatar(client, estudiante_id, intervenciones):
    return client.post(f"/laboratorio/casos/{estudiante_id}/resolver",
                       json={"intervenciones": intervenciones}).json()


def test_rescate_da_xp_por_mejorar_el_record(laboratorio):
    caso = laboratorio.casos[0]

    uno = rescatar(laboratorio, caso, ["tutoria", "tutoria"])      # 1 estrella
    tres = rescatar(laboratorio, caso, ["tutoria"])                # 3 estrellas: mejora 2
    otra = rescatar(laboratorio, caso, ["tutoria"])                # igual: no mejora
    peor = rescatar(laboratorio, caso, ["estudio"])                # no logrado

    assert [(r["estrellas"], r["xp_ganada"], r["record_estrellas"]) for r in (uno, tres, otra, peor)] == [
        (1, 10, 1), (3, 20, 3), (3, 0, 3), (0, 0, 3)]
    assert [i["codigo"] for i in uno["insignias_nuevas"]] == ["primer_rescate"]
    assert [i["codigo"] for i in tres["insignias_nuevas"]] == ["rescate_perfecto"]
    assert tres["progreso"]["xp"] == 30


def test_cada_intento_queda_guardado(laboratorio, db_session):
    rescatar(laboratorio, laboratorio.casos[0], ["tutoria"])
    rescatar(laboratorio, laboratorio.casos[0], ["estudio"])

    rescates = db_session.scalars(select(Rescate).order_by(Rescate.id)).all()
    assert [(r.estudio, r.tutoria, r.fichas, r.estrellas, r.logrado) for r in rescates] == [
        (0, 1, 2, 3, True), (1, 0, 1, 0, False)]
    assert rescates[0].version_modelo == "test"
    assert (rescates[0].horas_estudio_inicial, rescates[0].asistencia_inicial) == (10, 50)


def test_presupuesto_superado_no_guarda_nada(laboratorio, db_session):
    laboratorio.post(f"/laboratorio/casos/{laboratorio.casos[0]}/resolver",
                     json={"intervenciones": ["tutoria"] * 3})

    assert db_session.scalars(select(Rescate)).all() == []


def test_borrar_el_estudiante_no_borra_el_historial(laboratorio, admin_client, db_session):
    rescatar(laboratorio, laboratorio.casos[0], ["tutoria"])

    admin_client.delete(f"/estudiantes/{laboratorio.casos[0]}")

    rescate = db_session.scalar(select(Rescate))
    db_session.refresh(rescate)
    assert rescate.estudiante_id is None and rescate.estrellas == 3


def test_progreso_cuenta_rescates_y_estrellas(laboratorio):
    rescatar(laboratorio, laboratorio.casos[0], ["tutoria"])            # 3 estrellas
    rescatar(laboratorio, laboratorio.casos[1], ["estudio", "tutoria"])  # 2 estrellas

    p = laboratorio.get("/juego/progreso").json()

    assert (p["rescates_logrados"], p["estrellas_totales"], p["xp"]) == (2, 5, 50)


def test_dos_respuestas_simultaneas_no_dan_la_xp_dos_veces(engine):
    """Concurrencia real (dos conexiones a la vez): el FOR UPDATE sobre el
    jugador pone las respuestas en fila, así que solo la primera da XP.
    Fuera de la transacción del test: limpia lo que crea al final."""
    import threading

    from sqlalchemy.orm import Session

    from app import schemas
    from app.models import Usuario
    from app.routers import juego

    with Session(engine) as db:
        usuario = Usuario(alias="concurrente_juego", hash_password="$argon2id$x")
        db.add_all([usuario, Estudiante(**estudiante(carrera="Derecho")), Estudiante(**estudiante(carrera="Derecho")),
                    Estudiante(**estudiante(carrera="Medicina"))])
        db.commit()
        usuario_id = usuario.id

    barrera = threading.Barrier(2)
    resultados, errores = [], []

    def responder_en_hilo():
        try:
            with Session(engine) as db:
                jugador = db.get(Usuario, usuario_id)
                barrera.wait()
                r = juego.responder("carrera_mas_estudiantes", schemas.RespuestaEntrada(respuesta="Derecho"),
                                    jugador, db, MODELO)
                resultados.append(r.xp_ganada)
        except Exception as e:  # noqa: BLE001  (se comprueba abajo)
            errores.append(e)

    hilos = [threading.Thread(target=responder_en_hilo) for _ in range(2)]
    try:
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(timeout=30)

        assert errores == []
        assert sorted(resultados) == [0, 20]
    finally:
        with Session(engine) as db:
            db.delete(db.get(Usuario, usuario_id))  # sus intentos e insignias se van en CASCADE
            for e in db.scalars(select(Estudiante)):
                db.delete(e)
            db.commit()


def test_orden_natural_de_las_opciones():
    textos = ["Semestre 10", "Semestre 2", "Ingeniería", "Árbol", "administración", "Semestre 1"]

    assert sorted(textos, key=casos.orden_natural) == [
        "administración", "Árbol", "Ingeniería", "Semestre 1", "Semestre 2", "Semestre 10"]


def test_la_vista_del_docente_coincide_con_el_progreso_de_cada_uno(laboratorio, crear_usuario, datos):
    """resumen_de_jugadores (consultas agrupadas) debe dar lo mismo que /juego/progreso."""
    responder(laboratorio, "carrera_mas_estudiantes", "Medicina")
    responder(laboratorio, "carrera_mas_estudiantes", "Derecho")
    responder(laboratorio, "carrera_mas_riesgo", "Medicina")
    rescatar(laboratorio, laboratorio.casos[0], ["tutoria"])
    rescatar(laboratorio, laboratorio.casos[0], ["tutoria"])
    rescatar(laboratorio, laboratorio.casos[1], ["estudio"])  # no logrado
    profe = crear_usuario("Profe_1", rol="docente")

    fila = next(j for j in laboratorio.get("/juego/jugadores", headers=cabeceras(profe)).json()
                if j["alias"] == "detective_1")
    propio = laboratorio.get("/juego/progreso").json()

    assert (fila["xp"], fila["casos_resueltos"], fila["rescates_logrados"]) == (
        propio["xp"], propio["casos_resueltos"], propio["rescates_logrados"]) == (10 + 40 + 30, 2, 1)
    assert fila["insignias"] == sum(1 for i in propio["insignias"] if i["obtenida_en"])
