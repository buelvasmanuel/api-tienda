"""POST /prediccion/explicacion y /prediccion/riesgo/lote con el modelo congelado
del juego (app/modelo_juego.json). Contrato: docs/CONTRATO_LABORATORIO.md §3."""

import pytest

from app.modelo_juego import AVISO, ModeloJuego

ENTRADA = {"horas_estudio": 6, "asistencia": 62, "semestre": 2, "edad": 18, "carrera": "Medicina"}
MODELO = ModeloJuego.cargar()


def explicar(client, **cambios):
    return client.post("/prediccion/explicacion", json=ENTRADA | cambios)


def test_explicacion(client):
    respuesta = explicar(client)

    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["modelo"] == {"nombre": "Regresión Logística (juego)", "version": "2026-10-11"}
    assert datos["aviso"] == AVISO
    assert datos["probabilidad"] == round(MODELO.probabilidad(ENTRADA), 4)
    assert datos["en_riesgo"] is (MODELO.probabilidad(ENTRADA) >= 0.5)
    assert {a["variable"] for a in datos["aportes"]} == {"horas_estudio", "asistencia", "semestre", "edad", "carrera"}
    assert next(a for a in datos["aportes"] if a["variable"] == "carrera")["valor"] == "Medicina"


def test_la_explicacion_cuadra(client):
    datos = explicar(client).json()
    aportes = datos["aportes"]

    # base + Σ aportes = logit (salvo el redondeo a 4 decimales de cada número)
    assert datos["base"]["logit"] + sum(a["aporte"] for a in aportes) == pytest.approx(datos["logit"], abs=1e-3)
    assert sum(a["peso"] for a in aportes) == pytest.approx(1, abs=1e-3)
    assert [abs(a["aporte"]) for a in aportes] == sorted((abs(a["aporte"]) for a in aportes), reverse=True)
    for a in aportes:
        assert a["efecto"] == ("sube" if a["aporte"] > 0 else "baja")


def test_poca_asistencia_aparece_como_lo_que_mas_sube_el_riesgo(client):
    aportes = explicar(client, asistencia=40, horas_estudio=14).json()["aportes"]

    assert (aportes[0]["variable"], aportes[0]["efecto"]) == ("asistencia", "sube")


def test_explicacion_carrera_desconocida(client):
    respuesta = explicar(client, carrera="Astrología")

    assert respuesta.status_code == 422
    assert respuesta.json()["detail"]["codigo"] == "carrera_desconocida"
    assert respuesta.json()["detail"]["carreras_validas"] == MODELO.carreras_validas


@pytest.mark.parametrize("cambios", [{"asistencia": 101}, {"edad": 15}, {"horas_estudio": -1}])
def test_explicacion_fuera_de_rango(client, cambios):
    assert explicar(client, **cambios).status_code == 422


def test_explicacion_ignora_authorization(client):
    con = client.post("/prediccion/explicacion", json=ENTRADA, headers={"Authorization": "Bearer basura"})

    assert con.status_code == 200 and con.json() == explicar(client).json()


# ---------- Lote ----------

def test_lote_en_el_mismo_orden(client):
    entradas = [ENTRADA | {"asistencia": a} for a in (40, 70, 100)]

    respuesta = client.post("/prediccion/riesgo/lote", json={"entradas": entradas})

    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert [r["probabilidad"] for r in datos["resultados"]] == [round(MODELO.probabilidad(e), 4) for e in entradas]
    assert datos["resultados"][0]["probabilidad"] > datos["resultados"][2]["probabilidad"]
    assert datos["aviso"] == AVISO and datos["modelo"]["version"] == "2026-10-11"


def test_lote_coincide_con_la_explicacion(client):
    lote = client.post("/prediccion/riesgo/lote", json={"entradas": [ENTRADA]}).json()["resultados"][0]

    assert lote["probabilidad"] == explicar(client).json()["probabilidad"]


@pytest.mark.parametrize("cuantas", [0, 51])
def test_lote_de_1_a_50(client, cuantas):
    assert client.post("/prediccion/riesgo/lote", json={"entradas": [ENTRADA] * cuantas}).status_code == 422


def test_lote_de_50(client):
    assert len(client.post("/prediccion/riesgo/lote", json={"entradas": [ENTRADA] * 50}).json()["resultados"]) == 50


def test_lote_carrera_desconocida_dice_cuales(client):
    entradas = [ENTRADA, ENTRADA | {"carrera": "Astrología"}, ENTRADA | {"carrera": "Magia"}]

    respuesta = client.post("/prediccion/riesgo/lote", json={"entradas": entradas})

    assert respuesta.status_code == 422
    assert respuesta.json()["detail"]["indices"] == [1, 2]


def test_lote_una_entrada_invalida_invalida_todo(client):
    respuesta = client.post("/prediccion/riesgo/lote", json={"entradas": [ENTRADA, ENTRADA | {"edad": 99}]})

    assert respuesta.status_code == 422
    assert respuesta.json()["detail"][0]["loc"][:3] == ["body", "entradas", 1]
