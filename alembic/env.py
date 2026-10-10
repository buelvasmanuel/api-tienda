from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context

from app import models  # noqa: F401  (registra los modelos en Base.metadata)
from app.config import settings
from app.database import Base

# Objeto de configuración de Alembic (lee alembic.ini).
config = context.config

# Configura el logging según alembic.ini.
if config.config_file_name is not None:
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


def run_migrations_online() -> None:
    """Modo online: se conecta a la base y aplica las migraciones."""
    connectable = create_engine(DATABASE_URL, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,  # detecta también cambios de tipo de columna
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
