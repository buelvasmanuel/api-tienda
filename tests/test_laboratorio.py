"""Reglas del rescate y endpoints del Laboratorio (docs/CONTRATO_LABORATORIO.md).

Se usa un modelo sintético con números redondos para que cada caso sea exacto:
    logit = c0 − 0.25 · horas − 0.125 · asistencia
Así, `estudio` (+2 h) resta 0.5 al logit por 1 ficha y `tutoria` (+10 puntos)
resta 1.25 por 2 fichas. Son potencias de 2: las cuentas son exactas en coma
flotante, y "logit 0 justo" es 0 de verdad. Rescatado = logit < 0.
"""

import math

import pytest

from app import laboratorio as lab
from app.main import app
from app.modelo_juego import ModeloJuego
from app.routers.prediccion import obtener_modelo_juego
from tests.ayudas import cabeceras, codigo


def modelo_sintetico(c0: float) -> ModeloJuego:
    neutra = {"media": 0.0, "desviacion": 1.0, "coef": 0.0}
    return ModeloJuego({
        "version": "test", "nombre": "Sintético",
        "intercepto": c0,
        "numericas": {
            "horas_estudio": {"media": 0.0, "desviacion": 1.0, "coef": -0.25},
            "asistencia": {"media": 0.0, "desviacion": 1.0, "coef": -0.125},
            "semestre": neutra, "edad": neutra,
        },
        "carreras": {"Derecho": {"coef": 0.0, "frecuencia": 1.0}},
    })


def estudiante(horas=10, asistencia=50) -> dict:
    return {"horas_estudio": horas, "asistencia": asistencia, "semestre": 3, "edad": 20, "carrera": "Derecho"}


def c0_para(logit_inicial: float, horas=10, asistencia=50) -> float:
    """Intercepto que hace que el estudiante empiece con ese logit."""
    return logit_inicial + 0.25 * horas + 0.125 * asistencia


MODELO = modelo_sintetico(c0_para(1.0))  # logit 1.0 → probabilidad 0.731


# ---------- Reglas ----------

def test_combinaciones_dentro_del_presupuesto():
    combinaciones = lab.combinaciones()

    assert len(combinaciones) == 12
    assert all(lab.fichas(c) <= 5 for c in combinaciones)
    assert {"estudio": 1, "tutoria": 2} in combinaciones
    assert {"estudio": 0, "tutoria": 3} not in combinaciones


def test_topes():
    entrada = estudiante(horas=39, asistencia=95)

    final = lab.aplicar(entrada, {"estudio": 2, "tutoria": 1})

    assert (final["horas_estudio"], final["asistencia"]) == (40, 100)
    # En el tope, no tiene efecto pero cuesta igual.
    intento = lab.intentar(MODELO, estudiante(horas=40, asistencia=100), ["estudio", "tutoria"])
    assert intento.fichas == 3
    assert (intento.entrada["horas_estudio"], intento.entrada["asistencia"]) == (40, 100)


def test_el_orden_no_importa():
    a = lab.intentar(MODELO, estudiante(), ["tutoria", "estudio", "estudio"])
    b = lab.intentar(MODELO, estudiante(), ["estudio", "tutoria", "estudio"])

    assert a.probabilidad == b.probabilidad and a.fichas == b.fichas == 4


def test_solucion_optima_con_menos_fichas():
    optima = lab.solucion_optima(MODELO, estudiante())

    # 1 ficha (−0.5) no basta; 2 fichas: estudio×2 deja el logit en 0 exacto
    # (probabilidad 0.5, NO rescatado) y tutoría×1 lo deja en −0.25.
    assert optima.conteo == {"estudio": 0, "tutoria": 1}
    assert optima.fichas == 2
    assert optima.probabilidad == pytest.approx(1 / (1 + math.exp(0.25)))


def test_empate_en_fichas_gana_la_menor_probabilidad():
    modelo = modelo_sintetico(c0_para(0.9))  # con 2 fichas valen las dos opciones

    optima = lab.solucion_optima(modelo, estudiante())

    assert optima.fichas == 2 and optima.conteo == {"estudio": 0, "tutoria": 1}  # −0.35 < −0.1


def test_el_objetivo_es_estrictamente_menor_que_50():
    intento = lab.intentar(MODELO, estudiante(), ["estudio", "estudio"])

    assert intento.probabilidad == 0.5
    assert not intento.logrado


@pytest.mark.parametrize(
    ("intervenciones", "estrellas"),
    [
        (["tutoria"], 3),                       # 2 fichas = óptimo
        (["estudio", "estudio", "estudio"], 2),  # 3 fichas = una de más
        (["estudio", "tutoria"], 2),
        (["tutoria", "tutoria"], 1),            # 4 fichas
        (["estudio"] * 5, 1),
        (["estudio", "estudio"], 0),            # se queda en 0.5 justo
        (["estudio"], 0),
        ([], 0),
    ],
)
def test_estrellas(intervenciones, estrellas):
    optima = lab.solucion_optima(MODELO, estudiante())
    intento = lab.intentar(MODELO, estudiante(), intervenciones)

    assert lab.estrellas(intento, optima) == estrellas


@pytest.mark.parametrize(
    ("logit", "es_caso"),
    [(1.0, True),     # 0.73 y se rescata con 2 fichas
     (0.3, False),    # 0.57: por debajo del 60 %
     (2.95, True),    # 0.95: se rescata con 5 fichas (estudio + 2 tutorías = −3.0)
     (3.05, False)],  # 0.95 pero ni con 5 fichas baja de 0.5
)
def test_que_es_un_caso(logit, es_caso):
    assert lab.es_caso(modelo_sintetico(c0_para(logit)), estudiante()) is es_caso


def test_carrera_desconocida_no_es_caso():
    assert not lab.es_caso(MODELO, estudiante() | {"carrera": "Astrología"})


# ---------- Endpoints ----------

ESTUDIANTE_DB = {"nombre": "Ana", "apellido": "Gómez", "edad": 20, "carrera": "Derecho", "semestre": 3,
                 "promedio": "2.50", "ciudad": "Medellín"}


@pytest.fixture
def con_modelo_sintetico():
    app.dependency_overrides[obtener_modelo_juego] = lambda: MODELO
    yield
    app.dependency_overrides.pop(obtener_modelo_juego, None)


@pytest.fixture
def jugador(client, crear_usuario, con_modelo_sintetico):
    """Cliente con sesión de jugador y modelo sintético."""
    client.headers.update(cabeceras(crear_usuario("jugador_lab")))
    return client


@pytest.fixture
def casos(admin_client):
    """Tres estudiantes: rescatable (logit 1.0), riesgo bajo, e imposible."""
    def crear(horas, asistencia):
        datos = ESTUDIANTE_DB | {"horas_estudio": horas, "asistencia": asistencia}
        return admin_client.post("/estudiantes/", json=datos).json()["id"]

    # Con c0 = c0_para(1.0) = 9.75: logit = 9.75 − 0.25 h − 0.125 a
    return {
        "rescatable": crear(10, 50),   # logit 1.0
        "bajo": crear(20, 60),         # logit −2.75
        "imposible": crear(0, 30),     # logit 6.0: ni con 5 fichas
    }


def test_laboratorio_exige_sesion(client, con_modelo_sintetico):
    for metodo, ruta in [("GET", "/laboratorio/casos"), ("GET", "/laboratorio/casos/1"),
                         ("POST", "/laboratorio/casos/1/resolver")]:
        respuesta = client.request(metodo, ruta, json={"intervenciones": []})
        assert respuesta.status_code == 401
        assert codigo(respuesta) == "no_autenticado"


def test_listar_casos(jugador, casos):
    respuesta = jugador.get("/laboratorio/casos")

    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert [c["estudiante_id"] for c in datos["casos"]] == [casos["rescatable"]]
    assert (datos["presupuesto"], datos["objetivo"]) == (5, 0.5)
    assert datos["casos"][0]["probabilidad"] == round(1 / (1 + math.exp(-1.0)), 4)


def test_detalle_de_un_caso(jugador, casos):
    datos = jugador.get(f"/laboratorio/casos/{casos['rescatable']}").json()

    assert "promedio" not in datos["estudiante"]
    assert datos["estudiante"]["horas_estudio"] == 10
    assert [i["codigo"] for i in datos["intervenciones"]] == ["estudio", "tutoria"]
    assert datos["intervenciones"][1] == {
        "codigo": "tutoria", "nombre": "Tutoría", "fichas": 2, "variable": "asistencia",
        "incremento": 10, "tope": 100, "efecto": "+10 puntos de asistencia"}
    assert datos["explicacion"]["probabilidad"] == datos["probabilidad"]
    assert "no causas" in datos["aviso"]
    assert "solucion" not in str(datos)  # la solución no se revela antes del intento


@pytest.mark.parametrize("cual", ["bajo", "imposible", "no_existe"])
def test_caso_no_disponible(jugador, casos, cual):
    estudiante_id = casos.get(cual, 999999)

    for respuesta in (jugador.get(f"/laboratorio/casos/{estudiante_id}"),
                      jugador.post(f"/laboratorio/casos/{estudiante_id}/resolver", json={"intervenciones": []})):
        assert respuesta.status_code == 404
        assert codigo(respuesta) == "caso_no_disponible"


def test_resolver_con_la_solucion_optima(jugador, casos):
    respuesta = jugador.post(f"/laboratorio/casos/{casos['rescatable']}/resolver",
                             json={"intervenciones": ["tutoria"]})

    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["logrado"] is True
    assert datos["estrellas"] == 3
    assert (datos["fichas_usadas"], datos["fichas_optimas"]) == (2, 2)
    assert datos["estado_final"] == {"horas_estudio": 10, "asistencia": 60}
    assert datos["solucion_optima"] == {"estudio": 0, "tutoria": 1, "fichas": 2,
                                        "probabilidad": round(1 / (1 + math.exp(0.25)), 4)}
    assert datos["probabilidad_final"] == datos["explicacion_final"]["probabilidad"]
    assert "no causas" in datos["aviso"]


@pytest.mark.parametrize(
    ("intervenciones", "estrellas", "logrado"),
    [(["estudio", "tutoria"], 2, True), (["tutoria", "tutoria"], 1, True),
     (["estudio", "estudio"], 0, False), ([], 0, False)],
)
def test_resolver_calcula_las_estrellas(jugador, casos, intervenciones, estrellas, logrado):
    datos = jugador.post(f"/laboratorio/casos/{casos['rescatable']}/resolver",
                         json={"intervenciones": intervenciones}).json()

    assert (datos["estrellas"], datos["logrado"]) == (estrellas, logrado)


def test_el_cliente_no_puede_enviar_estrellas(jugador, casos):
    respuesta = jugador.post(f"/laboratorio/casos/{casos['rescatable']}/resolver",
                             json={"intervenciones": [], "estrellas": 3, "logrado": True})

    assert respuesta.status_code == 422


def test_presupuesto_superado(jugador, casos):
    respuesta = jugador.post(f"/laboratorio/casos/{casos['rescatable']}/resolver",
                             json={"intervenciones": ["tutoria", "tutoria", "tutoria"]})

    assert respuesta.status_code == 422
    detalle = respuesta.json()["detail"]
    assert (detalle["codigo"], detalle["fichas"], detalle["presupuesto"]) == ("presupuesto_superado", 6, 5)


@pytest.mark.parametrize("intervenciones", [["magia"], ["estudio"] * 6, "tutoria"])
def test_intervenciones_invalidas(jugador, casos, intervenciones):
    respuesta = jugador.post(f"/laboratorio/casos/{casos['rescatable']}/resolver",
                             json={"intervenciones": intervenciones})

    assert respuesta.status_code == 422
