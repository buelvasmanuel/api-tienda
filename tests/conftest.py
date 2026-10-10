"""Configuración compartida de los tests.

- Los tests usan SIEMPRE la base tienda_test (nunca la del .env).
- El esquema se crea con las migraciones de Alembic al empezar la sesión de
  tests y se elimina al terminar.
- Cada test corre dentro de una transacción que se deshace al final, así que
  todos empiezan con la base vacía.
"""

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.main import app

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
    engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
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
