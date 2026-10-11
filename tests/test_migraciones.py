"""Las migraciones de Alembic y los modelos de SQLAlchemy describen el mismo esquema."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app import models  # noqa: F401  (registra los modelos en Base.metadata)
from app.database import Base


def test_los_modelos_coinciden_con_las_migraciones(engine):
    # engine ya está en head (fixture de conftest.py).
    with engine.connect() as conn:
        contexto = MigrationContext.configure(conn, opts={"compare_type": True})
        diferencias = compare_metadata(contexto, Base.metadata)

    assert diferencias == []
