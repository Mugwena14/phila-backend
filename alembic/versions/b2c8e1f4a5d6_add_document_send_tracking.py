"""add document send tracking

Adds three nullable timestamp columns to patient_documents and creates the
document_send_log audit table. Every send attempt (success or failure) is
logged for POPIA compliance and debugging.

Revision ID: b2c8e1f4a5d6
Revises: 71116fd50a5f
Create Date: 2026-06-24

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b2c8e1f4a5d6"
down_revision: Union[str, Sequence[str], None] = "71116fd50a5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Send tracking - nullable so existing rows backfill cleanly
    op.add_column("patient_documents", sa.Column("sent_via_whatsapp_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("patient_documents", sa.Column("sent_via_email_at",    sa.DateTime(timezone=True), nullable=True))
    op.add_column("patient_documents", sa.Column("recalled_at",          sa.DateTime(timezone=True), nullable=True))

    # Audit log - every send attempt logged regardless of outcome
    op.create_table(
        "document_send_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("patient_documents.id"), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("recipient", sa.String(length=256), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("success", sa.Boolean, nullable=False),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("initiated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_document_send_log_document_id", "document_send_log", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_document_send_log_document_id", table_name="document_send_log")
    op.drop_table("document_send_log")
    op.drop_column("patient_documents", "recalled_at")
    op.drop_column("patient_documents", "sent_via_email_at")
    op.drop_column("patient_documents", "sent_via_whatsapp_at")
