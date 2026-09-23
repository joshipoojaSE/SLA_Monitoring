"""ORM models. See database.md."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# Master tables


class Region(Base):
    __tablename__ = "regions"

    region_id: Mapped[str] = mapped_column(Text, primary_key=True)


class Service(Base):
    __tablename__ = "services"

    service_id: Mapped[str] = mapped_column(Text, primary_key=True)
    service_name: Mapped[str] = mapped_column(Text, unique=True)


class Agent(Base):
    __tablename__ = "agents"

    agent_id: Mapped[str] = mapped_column(Text, primary_key=True)
    region_id: Mapped[str] = mapped_column(ForeignKey("regions.region_id"))


# Transaction tables


class UploadedFile(Base):
    __tablename__ = "uploaded_files"
    __table_args__ = (
        CheckConstraint("status IN ('processing', 'done', 'failed')", name="ck_uploaded_files_status"),
        CheckConstraint("status = 'failed' OR error_message IS NULL", name="ck_uploaded_files_error_message"),
        CheckConstraint("rows_received >= 0", name="ck_uploaded_files_rows_received"),
    )

    file_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    file_name: Mapped[str] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(Text, server_default="processing")
    error_message: Mapped[str | None] = mapped_column(Text)
    rows_received: Mapped[int] = mapped_column(Integer, server_default="0")


class ServiceStatusLog(Base):
    __tablename__ = "service_status_logs"
    __table_args__ = (
        # Also serves the (file_id, service_id, checked_at) index.
        UniqueConstraint("file_id", "service_id", "checked_at", name="uq_service_status_logs_slot"),
        # 15-minute grid; epoch is timezone-independent.
        CheckConstraint("mod(extract(epoch FROM checked_at), 900) = 0", name="ck_service_status_logs_grid"),
        CheckConstraint("outcome IN ('up', 'down', 'invalid')", name="ck_service_status_logs_outcome"),
        CheckConstraint("latency_ms >= 0", name="ck_service_status_logs_latency"),
        Index("ix_service_status_logs_file_checked_at", "file_id", "checked_at"),
    )

    status_log_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("uploaded_files.file_id", ondelete="CASCADE"))
    service_id: Mapped[str] = mapped_column(ForeignKey("services.service_id"))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status_code: Mapped[int] = mapped_column(SmallInteger)
    outcome: Mapped[str] = mapped_column(Text)
    latency_ms: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.agent_id"))


# Reference tables (verification only)


class TestOutage(Base):
    __tablename__ = "test_outages"
    __table_args__ = (CheckConstraint("days > 0", name="ck_test_outages_days"),)

    file_name: Mapped[str] = mapped_column(Text, primary_key=True)
    days: Mapped[int] = mapped_column(Integer)
    start_date: Mapped[date] = mapped_column(Date)


class TestOutageIncident(Base):
    __tablename__ = "test_outage_incidents"
    __table_args__ = (
        UniqueConstraint(
            "file_name", "service_id", "day_index", "checkpoint_start", name="uq_test_outage_incidents_outage"
        ),
        CheckConstraint("day_index >= 0", name="ck_test_outage_incidents_day_index"),
        CheckConstraint("checkpoint_start BETWEEN 0 AND 95", name="ck_test_outage_incidents_start"),
        CheckConstraint("checkpoint_end BETWEEN 0 AND 95", name="ck_test_outage_incidents_end"),
        CheckConstraint("checkpoint_end >= checkpoint_start", name="ck_test_outage_incidents_range"),
    )

    incident_id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    file_name: Mapped[str] = mapped_column(ForeignKey("test_outages.file_name"))
    service_id: Mapped[str] = mapped_column(ForeignKey("services.service_id"))
    day_index: Mapped[int] = mapped_column(Integer)
    checkpoint_start: Mapped[int] = mapped_column(Integer)
    checkpoint_end: Mapped[int] = mapped_column(Integer)
