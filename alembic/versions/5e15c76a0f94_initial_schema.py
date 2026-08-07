"""initial schema: tenants, calls, enquiries

Revision ID: 5e15c76a0f94
Revises:
Create Date: 2026-08-07

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5e15c76a0f94"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("vapi_assistant_id", sa.String(), nullable=False),
        sa.Column("vapi_phone_number_id", sa.String(), nullable=True),
        sa.Column("calendar_provider", sa.String(), nullable=False, server_default="none"),
        sa.Column("calendar_id", sa.String(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("vapi_assistant_id", name="uq_tenants_vapi_assistant_id"),
        sa.UniqueConstraint("vapi_phone_number_id", name="uq_tenants_vapi_phone_number_id"),
    )

    op.create_table(
        "calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("vapi_call_id", sa.String(), nullable=False),
        sa.Column("caller_phone", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_reason", sa.String(), nullable=True),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("recording_url", sa.String(), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
        sa.Column("processing_status", sa.String(), nullable=False, server_default="received"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("vapi_call_id", name="uq_calls_vapi_call_id"),
    )
    op.create_index("ix_calls_tenant_id", "calls", ["tenant_id"])
    op.create_index("ix_calls_tenant_id_created_at", "calls", ["tenant_id", "created_at"])

    op.create_table(
        "enquiries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("call_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("calls.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("topic", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("appointment_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("call_id", name="uq_enquiries_call_id"),
    )
    op.create_index("ix_enquiries_tenant_id", "enquiries", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_enquiries_tenant_id", table_name="enquiries")
    op.drop_table("enquiries")
    op.drop_index("ix_calls_tenant_id_created_at", table_name="calls")
    op.drop_index("ix_calls_tenant_id", table_name="calls")
    op.drop_table("calls")
    op.drop_table("tenants")
