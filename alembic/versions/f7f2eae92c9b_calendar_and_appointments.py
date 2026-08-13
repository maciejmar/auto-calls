"""calendar config on tenants + appointments table

Revision ID: f7f2eae92c9b
Revises: 5e15c76a0f94
Create Date: 2026-08-12

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7f2eae92c9b"
down_revision: Union[str, None] = "5e15c76a0f94"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("timezone", sa.String(), nullable=False, server_default="Europe/Warsaw"))
    op.add_column(
        "tenants", sa.Column("business_hours_start", sa.String(), nullable=False, server_default="09:00")
    )
    op.add_column(
        "tenants", sa.Column("business_hours_end", sa.String(), nullable=False, server_default="17:00")
    )
    op.add_column(
        "tenants",
        sa.Column("appointment_duration_minutes", sa.Integer(), nullable=False, server_default="30"),
    )

    op.create_table(
        "appointments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("call_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("calls.id"), nullable=True),
        sa.Column("vapi_call_id", sa.String(), nullable=True),
        sa.Column("google_event_id", sa.String(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("client_name", sa.String(), nullable=True),
        sa.Column("client_phone", sa.String(), nullable=True),
        sa.Column("topic", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="confirmed"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_appointments_tenant_id", "appointments", ["tenant_id"])
    op.create_index(
        "ix_appointments_tenant_id_starts_at", "appointments", ["tenant_id", "starts_at"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_appointments_tenant_id_starts_at", table_name="appointments")
    op.drop_index("ix_appointments_tenant_id", table_name="appointments")
    op.drop_table("appointments")

    op.drop_column("tenants", "appointment_duration_minutes")
    op.drop_column("tenants", "business_hours_end")
    op.drop_column("tenants", "business_hours_start")
    op.drop_column("tenants", "timezone")
