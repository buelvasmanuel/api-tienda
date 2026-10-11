"""eliminar productos y categorias

Las tablas venían de la primera actividad (la tienda) y el proyecto ya no las
usa. El downgrade las vuelve a crear vacías, con la misma estructura.

Revision ID: f9047f24dd9b
Revises: 1d65ce94be3f
Create Date: 2026-10-11 01:47:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f9047f24dd9b'
down_revision: Union[str, Sequence[str], None] = '1d65ce94be3f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # productos primero: tiene la FK hacia categorias.
    op.drop_table('productos')
    op.drop_table('categorias')


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table('categorias',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('nombre', sa.String(length=50), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_categorias')),
    sa.UniqueConstraint('nombre', name=op.f('uq_categorias_nombre'))
    )
    op.create_table('productos',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('nombre', sa.String(length=100), nullable=False),
    sa.Column('precio', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('stock', sa.Integer(), nullable=False),
    sa.Column('categoria_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['categoria_id'], ['categorias.id'], name=op.f('fk_productos_categoria_id_categorias'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
