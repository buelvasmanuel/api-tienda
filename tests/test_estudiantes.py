import pytest
from sqlalchemy.exc import DBAPIError

from app.models import Estudiante

ESTUDIANTE = {
    "nombre": "Ana",
    "apellido": "Restrepo",
    "edad": 20,
    "carrera": "Derecho",
    "semestre": 3,
    "promedio": "4.10",
    "ciudad": "Medellín",
    "horas_estudio": 12,
    "asistencia": 90,
}


def crear_estudiante(admin_client, **cambios):
    respuesta = admin_client.post("/estudiantes/", json=ESTUDIANTE | cambios)
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


# ---------- 201 ----------

def test_crear_estudiante_devuelve_201(admin_client):
    respuesta = admin_client.post("/estudiantes/", json=ESTUDIANTE)

    assert respuesta.status_code == 201
    datos = respuesta.json()
    assert datos == ESTUDIANTE | {"id": datos["id"]}


def test_crear_estudiante_quita_espacios(admin_client):
    datos = crear_estudiante(admin_client, nombre="  Ana ", ciudad=" Bello  ")

    assert (datos["nombre"], datos["ciudad"]) == ("Ana", "Bello")


@pytest.mark.parametrize(
    "cambios",
    [{"edad": 16}, {"edad": 35}, {"semestre": 1}, {"semestre": 10},
     {"promedio": "0.00"}, {"promedio": "5.00"}, {"horas_estudio": 0},
     {"horas_estudio": 40}, {"asistencia": 0}, {"asistencia": 100}],
    ids=lambda c: "-".join(f"{k}={v}" for k, v in c.items()),
)
def test_valores_en_el_limite_se_aceptan(admin_client, cambios):
    crear_estudiante(admin_client, **cambios)


# ---------- 200 ----------

def test_listar_estudiantes_empieza_vacio(admin_client):
    respuesta = admin_client.get("/estudiantes/")

    assert respuesta.status_code == 200
    assert respuesta.json() == []


def test_listar_estudiantes_ordenados_por_id(admin_client):
    creados = [crear_estudiante(admin_client, nombre=n) for n in ("Zoe", "Ana", "Luis")]

    respuesta = admin_client.get("/estudiantes/")

    assert respuesta.status_code == 200
    assert respuesta.json() == creados  # mismo orden de creación = orden por id
    ids = [e["id"] for e in respuesta.json()]
    assert ids == sorted(ids)


def test_obtener_estudiante_devuelve_200(admin_client):
    estudiante = crear_estudiante(admin_client)

    respuesta = admin_client.get(f"/estudiantes/{estudiante['id']}")

    assert respuesta.status_code == 200
    assert respuesta.json() == estudiante


def test_actualizar_estudiante_devuelve_200(admin_client):
    estudiante = crear_estudiante(admin_client)
    nuevos = ESTUDIANTE | {"semestre": 4, "promedio": "3.85", "asistencia": 95}

    respuesta = admin_client.put(f"/estudiantes/{estudiante['id']}", json=nuevos)

    assert respuesta.status_code == 200
    assert respuesta.json() == nuevos | {"id": estudiante["id"]}


# ---------- 404 ----------

@pytest.mark.parametrize(("metodo", "cuerpo"), [("GET", None), ("PUT", ESTUDIANTE), ("DELETE", None)])
def test_estudiante_inexistente_devuelve_404(admin_client, metodo, cuerpo):
    respuesta = admin_client.request(metodo, "/estudiantes/999", json=cuerpo)

    assert respuesta.status_code == 404
    assert respuesta.json() == {"detail": {"codigo": "estudiante_no_encontrado", "mensaje": "Estudiante no encontrado"}}


# ---------- 422 ----------

@pytest.mark.parametrize(
    "cambios",
    [{"edad": 15}, {"edad": 36}, {"semestre": 0}, {"semestre": 11},
     {"promedio": "-0.01"}, {"promedio": "5.01"}, {"promedio": "4.555"},
     {"horas_estudio": -1}, {"horas_estudio": 41},
     {"asistencia": -1}, {"asistencia": 101},
     {"nombre": ""}, {"apellido": "   "}, {"carrera": ""}, {"ciudad": "x" * 101}],
    ids=lambda c: "-".join(f"{k}={str(v)[:8]}" for k, v in c.items()),
)
def test_crear_estudiante_invalido_devuelve_422(admin_client, cambios):
    respuesta = admin_client.post("/estudiantes/", json=ESTUDIANTE | cambios)

    assert respuesta.status_code == 422


def test_crear_estudiante_sin_campos_devuelve_422(admin_client):
    respuesta = admin_client.post("/estudiantes/", json={})

    assert respuesta.status_code == 422
    assert {e["loc"][-1] for e in respuesta.json()["detail"]} == set(ESTUDIANTE)


def test_id_no_numerico_devuelve_422(admin_client):
    assert admin_client.get("/estudiantes/abc").status_code == 422


# ---------- 204 ----------

def test_eliminar_estudiante_devuelve_204(admin_client):
    estudiante = crear_estudiante(admin_client)

    respuesta = admin_client.delete(f"/estudiantes/{estudiante['id']}")

    assert respuesta.status_code == 204
    assert respuesta.content == b""
    assert admin_client.get(f"/estudiantes/{estudiante['id']}").status_code == 404


# ---------- CHECK en la base ----------

@pytest.mark.parametrize(
    "cambios",
    [{"edad": 50}, {"semestre": 11}, {"promedio": 5.5}, {"horas_estudio": 41}, {"asistencia": 120}],
    ids=lambda c: next(iter(c)),
)
def test_la_base_rechaza_valores_fuera_de_rango(db_session, cambios):
    # Saltándonos la API (y Pydantic), el CHECK de la base sigue protegiendo los datos.
    datos = ESTUDIANTE | {"promedio": 4.1} | cambios
    db_session.add(Estudiante(**datos))

    # MariaDB: "CONSTRAINT `ck_estudiantes_edad` failed ..."
    # MySQL 8: "Check constraint 'ck_estudiantes_edad' is violated."
    with pytest.raises(DBAPIError, match=f"ck_estudiantes_{next(iter(cambios))}"):
        db_session.flush()
