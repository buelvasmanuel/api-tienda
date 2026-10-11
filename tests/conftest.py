"""Configuración compartida de los tests.

- Los tests usan SIEMPRE la base tienda_test (nunca la del .env).
- El esquema se crea con las migraciones de Alembic al empezar la sesión de
  tests y se elimina al terminar.
- Cada test corre dentro de una transacción que se deshace al final, así que
  todos empiezan con la base vacía.
- Clave de tokens fija y Argon2 barato (los valores reales tardan ~100 ms por
  hash y harían lentos los tests). Se fijan antes de importar la app, y las
  variables de entorno tienen prioridad sobre el .env.
"""

import os
from datetime import timedelta

os.environ["CLAVE_TOKENS"] = "dGVzdHMtdGVzdHMtdGVzdHMtdGVzdHMtdGVzdHMtMzI"  # 32 bytes, solo tests
os.environ["ARGON2_TIEMPO"] = "1"
os.environ["ARGON2_MEMORIA_KIB"] = "64"
os.environ["CORS_ORIGINS"] = "https://app.ejemplo.org"
os.environ["IP_CLIENTE"] = "conexion"

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.engine import Connection, make_url  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import limites  # noqa: E402
from app import reloj as reloj_app  # noqa: E402
from app import seguridad  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import crear_engine, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Usuario  # noqa: E402
from tests.ayudas import PASSWORD, cabeceras  # noqa: E402

# Misma URL que la app (mismas credenciales del .env), pero otra base.
TEST_DATABASE_URL = make_url(settings.database_url).set(database="tienda_test")

# Cinturón de seguridad: jamás ejecutar los tests contra una base que no sea de pruebas.
assert TEST_DATABASE_URL.database.endswith("_test"), (
    f"Los tests solo pueden usar una base *_test, no {TEST_DATABASE_URL.database!r}"
)


def _alembic(connection: Connection, accion: str, revision: str) -> None:
    """Ejecuta 'alembic upgrade/downgrade' usando la conexión de los tests."""
    config = Config("alembic.ini")
    config.attributes["connection"] = connection  # env.py usará esta conexión
    config.attributes["configure_logger"] = False
    getattr(command, accion)(config, revision)


@pytest.fixture(scope="session")
def engine():
    """Engine de tienda_test con el esquema migrado (una vez por sesión)."""
    engine = crear_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    with engine.begin() as conn:
        assert conn.exec_driver_sql("SELECT DATABASE()").scalar() == "tienda_test"
        _alembic(conn, "upgrade", "head")

    yield engine

    with engine.begin() as conn:
        _alembic(conn, "downgrade", "base")
    engine.dispose()


@pytest.fixture
def db_session(engine):
    """Sesión dentro de una transacción que se deshace al acabar el test.

    Con join_transaction_mode="create_savepoint", cada db.commit() de los
    routers solo libera un SAVEPOINT; la transacción exterior nunca se confirma
    y el rollback final borra todo lo que hizo el test.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(
        bind=connection, join_transaction_mode="create_savepoint", autoflush=False
    )

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(db_session):
    """TestClient cuyos endpoints reciben la sesión de test en vez de get_db()."""
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()



@pytest.fixture
def crear_usuario(db_session):
    """Crea usuarios directamente en la base: crear_usuario("Ana", rol="admin")."""

    def crear(alias: str = "jugador_1", rol: str = "jugador", password: str = PASSWORD, **campos) -> Usuario:
        usuario = Usuario(alias=alias, rol=rol, hash_password=seguridad.hashear_password(password), **campos)
        db_session.add(usuario)
        db_session.flush()
        return usuario

    return crear


@pytest.fixture
def admin_client(client, crear_usuario):
    """Cliente autenticado como admin (para crear y modificar estudiantes)."""
    admin = crear_usuario("admin_tests", rol="admin")
    return TestClient(app, headers=cabeceras(admin))


class RelojFalso:
    """Sustituye a app.reloj.ahora: el tiempo solo avanza cuando el test lo dice."""

    def __init__(self, inicio):
        self.momento = inicio

    def __call__(self):
        return self.momento

    def avanzar(self, **duracion) -> None:
        self.momento += timedelta(**duracion)


@pytest.fixture
def reloj(monkeypatch) -> RelojFalso:
    falso = RelojFalso(reloj_app.ahora())
    monkeypatch.setattr(reloj_app, "ahora", falso)
    return falso


@pytest.fixture(autouse=True)
def sin_limite_por_ip():
    """El límite por IP (5 registros/hora...) rompería los tests que registran
    o inician sesión muchas veces: se apaga, salvo en tests/test_limites.py,
    que lo enciende con la fixture `limite_por_ip`."""
    limites.limiter.enabled = False
    limites.limiter.reset()
    yield
    limites.limiter.enabled = True


@pytest.fixture
def limite_por_ip():
    limites.limiter.enabled = True
