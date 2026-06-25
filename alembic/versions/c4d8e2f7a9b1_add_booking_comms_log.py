"""add booking comms log

Revision ID: c4d8e2f7a9b1
Revises: b2c8e1f4a5d6
Create Date: 2026-06-25
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c4d8e2f7a9b1"
down_revision: Union[str, Sequence[str], None] = "b2c8e1f4a5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "booking_comms_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("booking_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("bookings.id"), nullable=False),
        sa.Column("comms_type", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("recipient", sa.String(length=256), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("success", sa.Boolean, nullable=False),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("initiated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_booking_comms_log_booking_id", "booking_comms_log", ["booking_id"])


def downgrade() -> None:
    op.drop_index("ix_booking_comms_log_booking_id", table_name="booking_comms_log")
    op.drop_table("booking_comms_log")