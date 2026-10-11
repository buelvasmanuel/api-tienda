from sqlalchemy import Engine, MetaData, create_engine, event
from sqlalchemy.engine import URL
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

# Collation explícita: el valor por defecto cambia según el servidor (MariaDB
# 11+: utf8mb4_uca1400_ai_ci; MySQL 8: utf8mb4_0900_ai_ci, que no ignora los
# espacios finales). Así las comparaciones de texto son iguales en todos.
COLLATION = "utf8mb4_unicode_ci"


def _fijar_collation(dbapi_connection, _registro) -> None:
    # Va en un evento porque SQLAlchemy, al conectar, ejecuta su propio
    # "SET NAMES utf8mb4" y deshace la collation que se pase a PyMySQL.
    with dbapi_connection.cursor() as cursor:
        cursor.execute(f"SET NAMES utf8mb4 COLLATE {COLLATION}")


def crear_engine(url: str | URL, **kwargs) -> Engine:
    """Engine con SSL (si DB_SSL_CA está definido) y la collation fijada.

    Lo usan la app, Alembic (alembic/env.py) y los tests (tests/conftest.py).
    """
    engine = create_engine(url, connect_args=settings.connect_args, **kwargs)
    event.listen(engine, "connect", _fijar_collation)
    return engine


engine = crear_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False)

# Nombres predecibles para índices y restricciones: Alembic los necesita para
# poder borrarlos en los downgrade (con nombre None, drop_constraint falla).
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def get_db():
    """Abre una sesión por petición y la cierra siempre."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
