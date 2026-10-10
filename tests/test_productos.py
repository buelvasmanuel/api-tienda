import pytest

PRODUCTO = {"nombre": "Teclado", "precio": "49.90", "stock": 5}


def crear_producto(client, **cambios):
    respuesta = client.post("/productos/", json=PRODUCTO | cambios)
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


def crear_categoria(client, nombre="Periféricos"):
    respuesta = client.post("/categorias/", json={"nombre": nombre})
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


# ---------- 201 ----------

def test_crear_producto_devuelve_201(client):
    respuesta = client.post("/productos/", json=PRODUCTO)

    assert respuesta.status_code == 201
    datos = respuesta.json()
    assert datos == PRODUCTO | {"id": datos["id"], "categoria_id": None}


def test_crear_producto_con_categoria_devuelve_201(client):
    categoria = crear_categoria(client)

    producto = crear_producto(client, categoria_id=categoria["id"])

    assert producto["categoria_id"] == categoria["id"]


def test_crear_producto_sin_stock_usa_0_por_defecto(client):
    respuesta = client.post("/productos/", json={"nombre": "Ratón", "precio": "15"})

    assert respuesta.status_code == 201
    assert respuesta.json()["stock"] == 0
    assert respuesta.json()["precio"] == "15.00"  # Decimal(10,2) desde la base


def test_crear_producto_quita_espacios_del_nombre(client):
    assert crear_producto(client, nombre="  Teclado  ")["nombre"] == "Teclado"


# ---------- 200 ----------

def test_listar_productos_empieza_vacio(client):
    respuesta = client.get("/productos/")

    assert respuesta.status_code == 200
    assert respuesta.json() == []


def test_listar_productos_devuelve_200(client):
    teclado = crear_producto(client, nombre="Teclado")
    raton = crear_producto(client, nombre="Ratón")

    respuesta = client.get("/productos/")

    assert respuesta.status_code == 200
    assert sorted(respuesta.json(), key=lambda p: p["id"]) == [teclado, raton]


def test_obtener_producto_devuelve_200(client):
    producto = crear_producto(client)

    respuesta = client.get(f"/productos/{producto['id']}")

    assert respuesta.status_code == 200
    assert respuesta.json() == producto


def test_actualizar_producto_devuelve_200(client):
    producto = crear_producto(client)
    categoria = crear_categoria(client)
    nuevos = {"nombre": "Teclado mecánico", "precio": "89.00", "stock": 2,
              "categoria_id": categoria["id"]}

    respuesta = client.put(f"/productos/{producto['id']}", json=nuevos)

    assert respuesta.status_code == 200
    assert respuesta.json() == nuevos | {"id": producto["id"]}
    assert client.get(f"/productos/{producto['id']}").json() == nuevos | {"id": producto["id"]}


# ---------- 404 ----------

@pytest.mark.parametrize(
    ("metodo", "cuerpo"),
    [("GET", None), ("PUT", PRODUCTO), ("DELETE", None)],
)
def test_producto_inexistente_devuelve_404(client, metodo, cuerpo):
    respuesta = client.request(metodo, "/productos/999", json=cuerpo)

    assert respuesta.status_code == 404
    assert respuesta.json() == {"detail": "Producto no encontrado"}


# ---------- 422 ----------

@pytest.mark.parametrize(
    "cambios",
    [
        {"precio": "0"},
        {"precio": "-5"},
        {"precio": "abc"},
        {"precio": "1.999"},
        {"stock": -1},
        {"nombre": ""},
        {"nombre": "   "},
        {"nombre": "x" * 101},
    ],
    ids=["precio-cero", "precio-negativo", "precio-texto", "precio-3-decimales",
         "stock-negativo", "nombre-vacio", "nombre-solo-espacios", "nombre-largo"],
)
def test_crear_producto_invalido_devuelve_422(client, cambios):
    respuesta = client.post("/productos/", json=PRODUCTO | cambios)

    assert respuesta.status_code == 422


def test_crear_producto_sin_campos_obligatorios_devuelve_422(client):
    respuesta = client.post("/productos/", json={"stock": 1})

    assert respuesta.status_code == 422
    faltan = {error["loc"][-1] for error in respuesta.json()["detail"]}
    assert faltan == {"nombre", "precio"}


def test_crear_producto_con_categoria_inexistente_devuelve_422(client):
    respuesta = client.post("/productos/", json=PRODUCTO | {"categoria_id": 999})

    assert respuesta.status_code == 422
    assert respuesta.json() == {"detail": "La categoría 999 no existe"}


def test_actualizar_producto_con_categoria_inexistente_devuelve_422(client):
    producto = crear_producto(client)

    respuesta = client.put(f"/productos/{producto['id']}", json=PRODUCTO | {"categoria_id": 999})

    assert respuesta.status_code == 422
    assert client.get(f"/productos/{producto['id']}").json()["categoria_id"] is None


def test_id_no_numerico_devuelve_422(client):
    assert client.get("/productos/abc").status_code == 422


# ---------- 204 ----------

def test_eliminar_producto_devuelve_204(client):
    producto = crear_producto(client)

    respuesta = client.delete(f"/productos/{producto['id']}")

    assert respuesta.status_code == 204
    assert respuesta.content == b""
    assert client.get(f"/productos/{producto['id']}").status_code == 404
