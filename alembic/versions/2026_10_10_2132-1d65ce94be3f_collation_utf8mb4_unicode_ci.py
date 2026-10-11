"""collation utf8mb4_unicode_ci

Fija utf8mb4_unicode_ci en la base y en las tablas. MariaDB ya la usaba, pero
MySQL 8 (p. ej. Aiven) crea las tablas con utf8mb4_0900_ai_ci, que compara
distinto (no ignora los espacios finales). En MariaDB no cambia nada.

Revision ID: 1d65ce94be3f
Revises: 81a56be44550
Create Date: 2026-10-10 21:32:59.232548

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '1d65ce94be3f'
down_revision: Union[str, Sequence[str], None] = '81a56be44550'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


COLLATION = "utf8mb4_unicode_ci"
TABLAS = ["categorias", "productos", "estudiantes"]


def upgrade() -> None:
    """Upgrade schema."""
    # Sin nombre, ALTER DATABASE actúa sobre la base actual: las tablas que se
    # creen en el futuro heredarán esta collation.
    op.execute(f"ALTER DATABASE CHARACTER SET utf8mb4 COLLATE {COLLATION}")
    for tabla in TABLAS:
        op.execute(f"ALTER TABLE {tabla} CONVERT TO CHARACTER SET utf8mb4 COLLATE {COLLATION}")


def downgrade() -> None:
    """Downgrade schema."""
    # No se deshace: la collation anterior dependía del servidor y conservar
    # utf8mb4_unicode_ci no rompe las migraciones anteriores.
