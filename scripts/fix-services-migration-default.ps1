Write-Host "Phila Backend - Fix services migration default, ready to commit" -ForegroundColor Cyan

Set-Content "alembic/versions/f053b774bc85_add_services_and_custom_services_note_.py" @'
"""add services and custom_services_note to doctors

Revision ID: f053b774bc85
Revises: a1b2c3d4e5f6
Create Date: 2026-06-22 17:16:14.884233

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f053b774bc85"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "doctors",
        sa.Column(
            "services",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=True,
        ),
    )
    op.add_column("doctors", sa.Column("custom_services_note", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("doctors", "custom_services_note")
    op.drop_column("doctors", "services")
'@
Write-Host "  Updated migration with server_default so existing doctors get [] not NULL" -ForegroundColor Green

git add .
git commit -m "Add services and custom_services_note columns to doctors table - persists onboarding Services step, default-backfills existing rows to empty array"
Write-Host "Committed locally - DO NOT PUSH YET, read the next step" -ForegroundColor Yellow