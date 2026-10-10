import pytest

from app.routers import categorias as router_categorias


def crear_categoria(client, nombre="Ropa"):
    respuesta = client.post("/categorias/", json={"nombre": nombre})
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


def crear_producto(client, **datos):
    datos = {"nombre": "Camiseta", "precio": "19.99", "stock": 10} | datos
    respuesta = client.post("/productos/", json=datos)
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


# ---------- 201 ----------

def test_crear_categoria_devuelve_201(client):
    respuesta = client.post("/categorias/", json={"nombre": "Ropa"})

    assert respuesta.status_code == 201
    datos = respuesta.json()
    assert datos["nombre"] == "Ropa"
    assert isinstance(datos["id"], int)


def test_crear_categoria_quita_espacios_del_nombre(client):
    assert crear_categoria(client, "  Ropa  ")["nombre"] == "Ropa"


# ---------- 200 ----------

def test_listar_categorias_empieza_vacio(client):
    # Cada test arranca con la base vacía gracias al rollback.
    respuesta = client.get("/categorias/")

    assert respuesta.status_code == 200
    assert respuesta.json() == []


def test_listar_categorias_devuelve_200(client):
    crear_categoria(client, "Ropa")
    crear_categoria(client, "Hogar")

    respuesta = client.get("/categorias/")

    assert respuesta.status_code == 200
    assert {c["nombre"] for c in respuesta.json()} == {"Ropa", "Hogar"}


def test_obtener_categoria_devuelve_200(client):
    categoria = crear_categoria(client)

    respuesta = client.get(f"/categorias/{categoria['id']}")

    assert respuesta.status_code == 200
    assert respuesta.json() == categoria


def test_actualizar_categoria_devuelve_200(client):
    categoria = crear_categoria(client, "Ropa")

    respuesta = client.put(f"/categorias/{categoria['id']}", json={"nombre": "Moda"})

    assert respuesta.status_code == 200
    assert respuesta.json() == {"id": categoria["id"], "nombre": "Moda"}


def test_actualizar_categoria_cambiando_solo_mayusculas_devuelve_200(client):
    # Su propio nombre no cuenta como duplicado (excluir_id).
    categoria = crear_categoria(client, "Ropa")

    respuesta = client.put(f"/categorias/{categoria['id']}", json={"nombre": "ROPA"})

    assert respuesta.status_code == 200
    assert respuesta.json()["nombre"] == "ROPA"


def test_listar_productos_de_categoria_devuelve_200(client):
    ropa = crear_categoria(client, "Ropa")
    hogar = crear_categoria(client, "Hogar")
    camiseta = crear_producto(client, nombre="Camiseta", categoria_id=ropa["id"])
    crear_producto(client, nombre="Taza", categoria_id=hogar["id"])
    crear_producto(client, nombre="Sin categoría")

    respuesta = client.get(f"/categorias/{ropa['id']}/productos")

    assert respuesta.status_code == 200
    assert respuesta.json() == [camiseta]


# ---------- 404 ----------

@pytest.mark.parametrize(
    ("metodo", "ruta", "cuerpo"),
    [
        ("GET", "/categorias/999", None),
        ("GET", "/categorias/999/productos", None),
        ("PUT", "/categorias/999", {"nombre": "Ropa"}),
        ("DELETE", "/categorias/999", None),
    ],
)
def test_categoria_inexistente_devuelve_404(client, metodo, ruta, cuerpo):
    respuesta = client.request(metodo, ruta, json=cuerpo)

    assert respuesta.status_code == 404
    assert respuesta.json() == {"detail": "Categoría no encontrada"}


# ---------- 422 ----------

@pytest.mark.parametrize(
    "cuerpo",
    [
        {"nombre": ""},
        {"nombre": "   "},  # tras quitar espacios queda vacío
        {"nombre": "x" * 51},
        {},
    ],
    ids=["vacio", "solo-espacios", "demasiado-largo", "sin-nombre"],
)
def test_crear_categoria_invalida_devuelve_422(client, cuerpo):
    respuesta = client.post("/categorias/", json=cuerpo)

    assert respuesta.status_code == 422


def test_id_no_numerico_devuelve_422(client):
    assert client.get("/categorias/abc").status_code == 422


# ---------- 409 ----------

@pytest.mark.parametrize("duplicado", ["Ropa", "ropa", "RÓPA", "  ROPA  "])
def test_crear_categoria_duplicada_devuelve_409(client, duplicado):
    # utf8mb4_unicode_ci no distingue mayúsculas ni acentos.
    crear_categoria(client, "Ropa")

    respuesta = client.post("/categorias/", json={"nombre": duplicado})

    assert respuesta.status_code == 409
    assert respuesta.json() == {"detail": "Ya existe una categoría con ese nombre"}


def test_actualizar_con_nombre_de_otra_categoria_devuelve_409(client):
    crear_categoria(client, "Ropa")
    hogar = crear_categoria(client, "Hogar")

    respuesta = client.put(f"/categorias/{hogar['id']}", json={"nombre": "ropa"})

    assert respuesta.status_code == 409


def test_unique_de_la_base_responde_409_aunque_falle_la_comprobacion(client, monkeypatch):
    # Simula dos peticiones simultáneas: la comprobación previa no ve el
    # duplicado, pero el índice UNIQUE de MariaDB lo rechaza (IntegrityError).
    crear_categoria(client, "Ropa")
    monkeypatch.setattr(router_categorias, "_comprobar_nombre_libre", lambda *a, **k: None)

    respuesta = client.post("/categorias/", json={"nombre": "ropa"})

    assert respuesta.status_code == 409
    # Tras el rollback del savepoint, la sesión sigue utilizable.
    assert [c["nombre"] for c in client.get("/categorias/").json()] == ["Ropa"]


# ---------- 204 ----------

def test_eliminar_categoria_devuelve_204(client):
    categoria = crear_categoria(client)

    respuesta = client.delete(f"/categorias/{categoria['id']}")

    assert respuesta.status_code == 204
    assert respuesta.content == b""
    assert client.get(f"/categorias/{categoria['id']}").status_code == 404


def test_eliminar_categoria_deja_sus_productos_sin_categoria(client):
    categoria = crear_categoria(client)
    producto = crear_producto(client, categoria_id=categoria["id"])

    assert client.delete(f"/categorias/{categoria['id']}").status_code == 204

    respuesta = client.get(f"/productos/{producto['id']}")
    assert respuesta.status_code == 200  # el producto NO se borra...
    assert respuesta.json()["categoria_id"] is None  # ...queda sin categoría (SET NULL)
