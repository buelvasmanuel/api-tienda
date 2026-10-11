"""Estadísticas con un conjunto pequeño de estudiantes cuyos resultados se
calculan a mano.

    nombre  edad  carrera     semestre  promedio  ciudad
    Ana      18   Derecho        1        2.50    Medellín   ← en riesgo
    Luis     20   Derecho        3        4.00    Medellín
    Sara     20   Medicina       5        4.50    Bogotá
    Juan     22   Derecho        5        3.00    Envigado   ← justo en 3.00: NO está en riesgo
    Eva      18   Medicina       1        2.99    Bogotá     ← en riesgo
    Paco     25   Psicología     9        3.50    Cali
"""

import pytest

URL = "/estudiantes/estadisticas"

ESTUDIANTES = [
    ("Ana", 18, "Derecho", 1, "2.50", "Medellín"),
    ("Luis", 20, "Derecho", 3, "4.00", "Medellín"),
    ("Sara", 20, "Medicina", 5, "4.50", "Bogotá"),
    ("Juan", 22, "Derecho", 5, "3.00", "Envigado"),
    ("Eva", 18, "Medicina", 1, "2.99", "Bogotá"),
    ("Paco", 25, "Psicología", 9, "3.50", "Cali"),
]

ENDPOINTS = ["por-carrera", "por-edad", "por-semestre", "por-ciudad",
             "promedio-por-carrera", "riesgo-por-carrera"]


def crear_estudiante(client, nombre="Ana", edad=20, carrera="Derecho", semestre=1,
                     promedio="4.00", ciudad="Medellín"):
    respuesta = client.post("/estudiantes/", json={
        "nombre": nombre, "apellido": "Prueba", "edad": edad, "carrera": carrera,
        "semestre": semestre, "promedio": promedio, "ciudad": ciudad,
        "horas_estudio": 10, "asistencia": 80,
    })
    assert respuesta.status_code == 201, respuesta.text


@pytest.fixture
def con_estudiantes(client, admin_client):
    """Crea los estudiantes como admin y devuelve el cliente anónimo: las
    estadísticas son públicas."""
    for nombre, edad, carrera, semestre, promedio, ciudad in ESTUDIANTES:
        crear_estudiante(admin_client, nombre, edad, carrera, semestre, promedio, ciudad)
    return client


def estadistica(client, endpoint):
    respuesta = client.get(f"{URL}/{endpoint}")
    assert respuesta.status_code == 200
    return [(e["etiqueta"], e["valor"]) for e in respuesta.json()]


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_sin_estudiantes_devuelve_lista_vacia(client, endpoint):
    assert estadistica(client, endpoint) == []


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_formato_comun_etiqueta_y_valor(con_estudiantes, endpoint):
    for item in con_estudiantes.get(f"{URL}/{endpoint}").json():
        assert set(item) == {"etiqueta", "valor"}
        assert isinstance(item["etiqueta"], str)
        assert isinstance(item["valor"], (int, float))


def test_por_carrera(con_estudiantes):
    assert estadistica(con_estudiantes, "por-carrera") == [
        ("Derecho", 3), ("Medicina", 2), ("Psicología", 1),
    ]


def test_los_conteos_son_enteros(con_estudiantes):
    valores = [e["valor"] for e in con_estudiantes.get(f"{URL}/por-carrera").json()]
    assert all(type(v) is int for v in valores)  # 3, no 3.0


def test_por_edad_ordenado_por_edad(con_estudiantes):
    assert estadistica(con_estudiantes, "por-edad") == [
        ("18", 2), ("20", 2), ("22", 1), ("25", 1),
    ]


def test_por_semestre_ordenado_por_semestre(con_estudiantes):
    assert estadistica(con_estudiantes, "por-semestre") == [
        ("1", 2), ("3", 1), ("5", 2), ("9", 1),
    ]


def test_por_ciudad_desempata_por_nombre(con_estudiantes):
    # Bogotá y Medellín empatan a 2; Cali y Envigado a 1 → orden alfabético.
    assert estadistica(con_estudiantes, "por-ciudad") == [
        ("Bogotá", 2), ("Medellín", 2), ("Cali", 1), ("Envigado", 1),
    ]


def test_por_ciudad_devuelve_solo_top_10(client, admin_client):
    # 11 ciudades: "Ciudad 11" tiene 2 estudiantes, el resto 1.
    for i in range(1, 12):
        crear_estudiante(admin_client, ciudad=f"Ciudad {i:02d}")
    crear_estudiante(admin_client, ciudad="Ciudad 11")

    resultado = estadistica(client, "por-ciudad")

    assert len(resultado) == 10
    assert resultado[0] == ("Ciudad 11", 2)
    assert resultado[1:] == [(f"Ciudad {i:02d}", 1) for i in range(1, 10)]  # "Ciudad 10" queda fuera


def test_promedio_por_carrera(con_estudiantes):
    # Derecho:    (2.50 + 4.00 + 3.00) / 3 = 3.1666… → 3.17
    # Medicina:   (4.50 + 2.99) / 2       = 3.745   → 3.75 (DECIMAL exacto: redondea hacia arriba)
    # Psicología:  3.50                             → 3.5
    assert estadistica(con_estudiantes, "promedio-por-carrera") == [
        ("Medicina", 3.75), ("Psicología", 3.5), ("Derecho", 3.17),
    ]


def test_riesgo_por_carrera(con_estudiantes):
    # Derecho: Ana (2.50) en riesgo; Juan (3.00) NO → 1/3 = 33.33 %
    # Medicina: Eva (2.99) en riesgo                → 1/2 = 50 %
    # Psicología: nadie                             → 0 %
    assert estadistica(con_estudiantes, "riesgo-por-carrera") == [
        ("Medicina", 50.0), ("Derecho", 33.33), ("Psicología", 0.0),
    ]
