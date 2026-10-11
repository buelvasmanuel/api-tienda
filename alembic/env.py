from logging.config import fileConfig

from sqlalchemy import pool

from alembic import context

from app import models  # noqa: F401  (registra los modelos en Base.metadata)
from app.config import settings
from app.database import Base, crear_engine

# Objeto de configuración de Alembic (lee alembic.ini).
config = context.config

# Configura el logging según alembic.ini (los tests lo desactivan para no
# pisar la configuración de logging de pytest).
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

# Metadatos de nuestros modelos: autogenerate los compara con la base real.
target_metadata = Base.metadata

# La URL sale de app/config.py (que lee .env y las variables de entorno), así
# no hay credenciales en alembic.ini. No se pasa por config.set_main_option()
# porque un "%" en la contraseña rompería la interpolación de configparser.
DATABASE_URL = settings.database_url


def run_migrations_offline() -> None:
    """Modo offline: genera el SQL sin conectarse (alembic upgrade --sql)."""
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def _run_with_connection(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,  # detecta también cambios de tipo de columna
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Modo online: se conecta a la base y aplica las migraciones."""
    # Si quien llama ya trae una conexión (p. ej. los tests, contra tienda_test),
    # se usa esa. Desde la terminal no hay ninguna y se usa la URL de config.py.
    connection = config.attributes.get("connection")
    if connection is not None:
        _run_with_connection(connection)
        return

    connectable = crear_engine(DATABASE_URL, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        _run_with_connection(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
