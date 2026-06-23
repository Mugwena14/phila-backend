Write-Host "Phila Backend - fill in the empty favorites migration with real DDL" -ForegroundColor Cyan

Set-Content "alembic/versions/71116fd50a5f_add_favorite_doctors_table.py" @'
"""add favorite_doctors table

Revision ID: 71116fd50a5f
Revises: f053b774bc85
Create Date: 2026-06-23 14:16:02.425778

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "71116fd50a5f"
down_revision: Union[str, Sequence[str], None] = "f053b774bc85"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "favorite_doctors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("doctor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("doctors.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.UniqueConstraint("patient_id", "doctor_id", name="uq_patient_doctor_favorite"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("favorite_doctors")
'@
Write-Host "  Updated migration - upgrade() now actually creates the table, downgrade() drops it, stray model definition removed" -ForegroundColor Green

git add .
git commit -m "Fix empty favorites migration - autogenerate produced pass/pass, fill in op.create_table with real DDL"
Write-Host "Committed locally - do NOT push yet, prod needs the version row reset first" -ForegroundColor Yellow