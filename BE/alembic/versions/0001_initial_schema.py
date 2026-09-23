"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "regions",
        sa.Column("region_id", sa.Text(), primary_key=True),
    )
    op.create_table(
        "services",
        sa.Column("service_id", sa.Text(), primary_key=True),
        sa.Column("service_name", sa.Text(), nullable=False, unique=True),
    )
    op.create_table(
        "agents",
        sa.Column("agent_id", sa.Text(), primary_key=True),
        sa.Column("region_id", sa.Text(), sa.ForeignKey("regions.region_id"), nullable=False),
    )
    op.create_table(
        "uploaded_files",
        sa.Column("file_id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("status", sa.Text(), server_default="processing", nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("rows_received", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("status IN ('processing', 'done', 'failed')", name="ck_uploaded_files_status"),
        sa.CheckConstraint("status = 'failed' OR error_message IS NULL", name="ck_uploaded_files_error_message"),
        sa.CheckConstraint("rows_received >= 0", name="ck_uploaded_files_rows_received"),
    )
    op.create_table(
        "service_status_logs",
        sa.Column("status_log_id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "file_id",
            sa.BigInteger(),
            sa.ForeignKey("uploaded_files.file_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("service_id", sa.Text(), sa.ForeignKey("services.service_id"), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status_code", sa.SmallInteger(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.Numeric(12, 3)),
        sa.Column("agent_id", sa.Text(), sa.ForeignKey("agents.agent_id"), nullable=False),
        sa.UniqueConstraint("file_id", "service_id", "checked_at", name="uq_service_status_logs_slot"),
        sa.CheckConstraint("mod(extract(epoch FROM checked_at), 900) = 0", name="ck_service_status_logs_grid"),
        sa.CheckConstraint("outcome IN ('up', 'down', 'invalid')", name="ck_service_status_logs_outcome"),
        sa.CheckConstraint("latency_ms >= 0", name="ck_service_status_logs_latency"),
    )
    op.create_index("ix_service_status_logs_file_checked_at", "service_status_logs", ["file_id", "checked_at"])
    op.create_table(
        "test_outages",
        sa.Column("file_name", sa.Text(), primary_key=True),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.CheckConstraint("days > 0", name="ck_test_outages_days"),
    )
    op.create_table(
        "test_outage_incidents",
        sa.Column("incident_id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("file_name", sa.Text(), sa.ForeignKey("test_outages.file_name"), nullable=False),
        sa.Column("service_id", sa.Text(), sa.ForeignKey("services.service_id"), nullable=False),
        sa.Column("day_index", sa.Integer(), nullable=False),
        sa.Column("checkpoint_start", sa.Integer(), nullable=False),
        sa.Column("checkpoint_end", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "file_name", "service_id", "day_index", "checkpoint_start", name="uq_test_outage_incidents_outage"
        ),
        sa.CheckConstraint("day_index >= 0", name="ck_test_outage_incidents_day_index"),
        sa.CheckConstraint("checkpoint_start BETWEEN 0 AND 95", name="ck_test_outage_incidents_start"),
        sa.CheckConstraint("checkpoint_end BETWEEN 0 AND 95", name="ck_test_outage_incidents_end"),
        sa.CheckConstraint("checkpoint_end >= checkpoint_start", name="ck_test_outage_incidents_range"),
    )


def downgrade() -> None:
    op.drop_table("test_outage_incidents")
    op.drop_table("test_outages")
    op.drop_table("service_status_logs")
    op.drop_table("uploaded_files")
    op.drop_table("agents")
    op.drop_table("services")
    op.drop_table("regions")
