"""add practice images to doctors

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d5e6
Create Date: 2026-05-16 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'a1b2c3d4e5f6'
down_revision = 'c97dc8a8691f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'doctors',
        sa.Column('practice_images', postgresql.ARRAY(sa.String()), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('doctors', 'practice_images')