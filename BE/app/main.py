"""SLA Monitoring API.

Upload endpoint: takes a CSV of health checks, validates it, cleans it and
returns a report on what was found. Nothing is written to the database yet.
"""

import csv
import io
import os
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from functools import lru_cache
from typing import Literal

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import psycopg
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import engine  # loads .env
from app.models import (
    Agent,
    Region,
    Service,
    ServiceStatusLog,
    TestOutage,
    TestOutageIncident,
    UploadedFile,
)

app = FastAPI(title="SLA Monitoring API")

# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

ALLOWED_EXTENSIONS = (".csv",)

# The 30-day sample file is 1.1 MB. The cap is generous for this data and
# still under the few-MB request body limit serverless platforms impose.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

REQUIRED_COLUMNS = (
    "service_id",
    "service_name",
    "timestamp",
    "status_code",
    "latency",
    "latency_unit",
    "agent",
    "region",
)

# One check per service every 15 minutes.
CHECK_INTERVAL_SECONDS = 900
CHECKS_PER_DAY = 86_400 // CHECK_INTERVAL_SECONDS

LATENCY_MULTIPLIERS = {"ms": Decimal(1), "s": Decimal(1000)}

# A code has to fit the smallint column and be a plausible three-digit status.
# The seeded 999 is kept (as 'invalid'); 999999 is not a status code at all.
MIN_STATUS_CODE = 100
MAX_STATUS_CODE = 999

# The largest value the Numeric(12, 3) column holds: about 11.5 days, far past
# anything a health check could report.
MAX_LATENCY_MS = Decimal("999999999.999")

# Decision D1: a failure seen by any agent is real evidence, and a valid
# reading beats an invalid code. Lower sorts first, so it wins the slot.
OUTCOME_PRIORITY = {"down": 0, "up": 1, "invalid": 2}

MAX_REPORTED_REJECTIONS = 20

# Decision D5: where one incident ends. A healthy check ends it, so every
# stored incident is an unbroken run of failures and its range never covers a
# check that passed. A flickering outage is therefore recorded as several
# incidents: the seeded svc-reports outage on 2025-05-13 recovers for one slot
# at 16:30, 17:00 and 17:45, and is stored as four rows rather than one.
#
# Raising this bridges that many healthy slots and merges those rows back into
# one. At 2 (30 minutes) each of the 8 seeded outages reads as a single
# incident, which is how dataset_incident_log.json describes them. Either way
# the downtime is the same: only failed checks are ever counted. Stored
# incidents are read back assuming 0, though (see INCIDENT_LENGTH), so raising
# it means storing each incident's failed-check count as well.
MAX_HEALTHY_GAP_SLOTS = 0

# The SLA from the brief: monthly availability below 99.9% earns a credit.
SLA_TARGET_PCT = 99.9

MAX_LOG_PAGE_SIZE = 200

LogSort = Literal["checked_at", "service", "status", "outcome", "latency", "agent", "region"]

MAX_STATS_PAGE_SIZE = 100
ServiceSort = Literal["service", "availability", "sla", "downtime", "incidents", "longest", "p95", "gaps"]
IncidentSort = Literal["service", "started", "ended", "checks", "downtime"]

# Comma-separated; the dashboard's dev server (FE/vite.config.ts) by default.
CORS_ORIGINS = [
    origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:5174").split(",") if origin.strip()
]

# Every raw upload is kept at s3://S3_BUCKET/<file_id>/<file name>. Credentials
# come from the usual AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY variables.
S3_BUCKET = os.getenv("S3_BUCKET", "")
AWS_REGION = os.getenv("AWS_REGION") or None


class ErrorCode(str, Enum):
    """Reasons a file is rejected outright, with no report to return."""

    NO_FILE = "NO_FILE"
    UNSUPPORTED_FILE_TYPE = "UNSUPPORTED_FILE_TYPE"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    EMPTY_FILE = "EMPTY_FILE"
    UNREADABLE_ENCODING = "UNREADABLE_ENCODING"
    MISSING_HEADER = "MISSING_HEADER"
    MISSING_COLUMNS = "MISSING_COLUMNS"
    DUPLICATE_COLUMNS = "DUPLICATE_COLUMNS"
    NO_DATA_ROWS = "NO_DATA_ROWS"
    NO_VALID_ROWS = "NO_VALID_ROWS"


class UploadRejected(Exception):
    """An uploaded file that cannot be processed at all."""

    def __init__(self, code: ErrorCode, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class RowError(ValueError):
    """A single row that cannot be cleaned. Skipped and counted, not fatal."""


# --------------------------------------------------------------------------
# Response models
# --------------------------------------------------------------------------


class Coverage(BaseModel):
    start: datetime
    end: datetime
    days: int
    services: list[str]
    expected_checks: int
    missing_checks: int


class DuplicateReport(BaseModel):
    total: int
    exact_rows: int
    same_slot_rows: int
    conflicting_slots: int


class IssueReport(BaseModel):
    epoch_timestamps: int = 0
    offset_timestamps: int = 0
    naive_timestamps: int = 0
    latency_converted_from_seconds: int = 0
    blank_latency: int = 0
    negative_latency: int = 0
    invalid_status_codes: int = 0


class RejectedRowReport(BaseModel):
    line: int
    reason: str


class UploadReport(BaseModel):
    """What the upload screen shows once a file has been accepted."""

    file_id: int
    file_name: str
    status: Literal["clean", "accepted_with_warnings"]
    rows_received: int
    clean_checks: int
    rows_removed: int
    coverage: Coverage
    duplicates: DuplicateReport
    issues: IssueReport
    ragged_rows: int
    rejected_rows: list[RejectedRowReport]
    warnings: list[str]


class OutageIncident(BaseModel):
    """One detected incident, on one service, within one day."""

    service_id: str
    day_index: int
    checkpoint_start: int
    checkpoint_end: int
    started_at: datetime
    ended_at: datetime
    down_checks: int
    downtime_minutes: int


class OutageReport(BaseModel):
    """What the outage scan found for one uploaded file."""

    file_id: int
    file_name: str
    already_processed: bool
    start_date: date
    days: int
    incidents_found: int
    services_affected: list[str]
    total_downtime_minutes: int
    incidents: list[OutageIncident]


class FileSummary(BaseModel):
    """One upload, as the dashboard's file picker lists it."""

    file_id: int
    file_name: str
    uploaded_at: datetime
    rows_received: int
    stored_checks: int
    scanned: bool


class ServiceStats(BaseModel):
    service_id: str
    service_name: str
    up_checks: int
    down_checks: int
    invalid_checks: int
    missing_checks: int
    # None when the service has no valid check to judge it by.
    availability_pct: float | None
    meets_sla: bool | None
    downtime_minutes: int
    allowed_downtime_minutes: float
    incidents: int
    longest_incident_minutes: int
    avg_latency_ms: float | None
    p95_latency_ms: float | None


class DashboardStats(BaseModel):
    """The headline figures for one file. The two tables page through their
    rows separately; services stay here too, as the log filter lists them."""

    file_id: int
    file_name: str
    scanned: bool
    sla_target_pct: float
    coverage_start: datetime
    coverage_end: datetime
    days: int
    availability_pct: float | None
    services_breaching: int
    total_downtime_minutes: int
    incident_count: int
    longest_incident_minutes: int
    services: list[ServiceStats]


class ServicePage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[ServiceStats]


class IncidentPage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[OutageIncident]


class LogRow(BaseModel):
    checked_at: datetime
    service_id: str
    status_code: int
    outcome: str
    latency_ms: float | None
    agent_id: str
    region_id: str


class LogPage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[LogRow]


# --------------------------------------------------------------------------
# Working types
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RawRow:
    """One data line, keyed by normalized column name."""

    line: int
    values: dict[str, str]
    raw: tuple[str, ...]


@dataclass(frozen=True)
class CleanCheck:
    line: int
    service_id: str
    service_name: str
    checked_at: datetime
    status_code: int
    outcome: str
    latency_ms: Decimal | None
    agent_id: str
    region_id: str


@dataclass
class CleanResult:
    checks: list[CleanCheck]
    rows_received: int
    issues: Counter
    duplicates: DuplicateReport
    rejected: list[RejectedRowReport]


# --------------------------------------------------------------------------
# File and structure checks, before any row is looked at
# --------------------------------------------------------------------------


def validate_file(file_name: str, content: bytes) -> str:
    """Check the file itself and return its decoded text."""
    if not file_name:
        raise UploadRejected(ErrorCode.NO_FILE, "No file was uploaded.")

    if not file_name.lower().endswith(ALLOWED_EXTENSIONS):
        raise UploadRejected(
            ErrorCode.UNSUPPORTED_FILE_TYPE,
            f"Only {', '.join(ALLOWED_EXTENSIONS)} files are accepted.",
            {"file_name": file_name},
        )

    if len(content) > MAX_UPLOAD_BYTES:
        raise UploadRejected(
            ErrorCode.FILE_TOO_LARGE,
            f"The file is larger than the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
            {"size_bytes": len(content), "limit_bytes": MAX_UPLOAD_BYTES},
        )

    # Whitespace only counts as empty: a file of blank lines has no data either.
    if not content.strip():
        raise UploadRejected(ErrorCode.EMPTY_FILE, "The file is empty.")

    try:
        # utf-8-sig drops the byte-order mark Excel writes when it exports CSV.
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UploadRejected(
            ErrorCode.UNREADABLE_ENCODING,
            "The file is not valid UTF-8 text. Export it as CSV UTF-8 and try again.",
        ) from exc


def validate_structure(csv_text: str) -> tuple[list[RawRow], int]:
    """Check the header and split the body into rows.

    Returns the rows, and how many lines were skipped for having the wrong
    number of fields.
    """
    reader = csv.reader(io.StringIO(csv_text, newline=""))

    try:
        raw_header = next(reader)
    except StopIteration as exc:
        raise UploadRejected(ErrorCode.MISSING_HEADER, "The file has no header row.") from exc

    header = [column.strip().lower() for column in raw_header]

    repeated = sorted({column for column in header if header.count(column) > 1})
    if repeated:
        raise UploadRejected(
            ErrorCode.DUPLICATE_COLUMNS,
            f"The header repeats these columns: {', '.join(repeated)}.",
            {"duplicated": repeated},
        )

    missing = [column for column in REQUIRED_COLUMNS if column not in header]
    if missing:
        raise UploadRejected(
            ErrorCode.MISSING_COLUMNS,
            f"The file is missing these columns: {', '.join(missing)}.",
            {"missing": missing, "found": header, "required": list(REQUIRED_COLUMNS)},
        )

    # Extra columns are ignored rather than rejected: they cost nothing, and a
    # file carrying one spare column is still perfectly usable.
    width = len(raw_header)
    rows: list[RawRow] = []
    ragged = 0

    for values in reader:
        if not any(value.strip() for value in values):
            continue
        if len(values) != width:
            ragged += 1
            continue
        rows.append(RawRow(line=reader.line_num, values=dict(zip(header, values)), raw=tuple(values)))

    if not rows:
        raise UploadRejected(
            ErrorCode.NO_DATA_ROWS,
            "The file has a header but no data rows.",
            {"ragged_rows": ragged},
        )

    return rows, ragged


# --------------------------------------------------------------------------
# Cleaning, then duplicates
#
# Duplicates cannot be found on the raw text. The sample files write one and
# the same check three different ways -- 2025-05-13T12:45:00Z, the Unix epoch
# 1746938700, and an IST +05:30 offset -- so two lines describing a single
# check look nothing alike as strings. Timestamps are converted to UTC first,
# and only then are rows grouped by service and time slot.
# --------------------------------------------------------------------------


def _parse_timestamp(value: str, issues: Counter) -> datetime:
    raw = value.strip()
    if not raw:
        raise RowError("timestamp is blank")

    if raw.lstrip("-").isdigit():
        issues["epoch_timestamps"] += 1
        try:
            return datetime.fromtimestamp(int(raw), tz=UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise RowError(f"timestamp {raw!r} is not a usable epoch value") from exc

    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise RowError(f"timestamp {raw!r} is not a readable date") from exc

    if parsed.tzinfo is None:
        issues["naive_timestamps"] += 1
        return parsed.replace(tzinfo=UTC)

    # An offset is converted, never stripped: dropping +05:30 shifts the check
    # by 5.5 hours and can move it to the wrong day, or the wrong month.
    if parsed.utcoffset():
        issues["offset_timestamps"] += 1
    return parsed.astimezone(UTC)


def _parse_latency(value: str, unit: str, issues: Counter) -> Decimal | None:
    raw = value.strip()
    if not raw:
        issues["blank_latency"] += 1
        return None

    key = unit.strip().lower()
    if key not in LATENCY_MULTIPLIERS:
        raise RowError(f"unknown latency unit {unit.strip()!r}")

    try:
        amount = Decimal(raw)
    except InvalidOperation as exc:
        raise RowError(f"latency {raw!r} is not a number") from exc

    if not amount.is_finite():
        raise RowError(f"latency {raw!r} is not a number")

    # A negative latency is impossible, so it is treated as no reading at all.
    if amount < 0:
        issues["negative_latency"] += 1
        return None

    latency_ms = amount * LATENCY_MULTIPLIERS[key]
    # Caught here rather than at insert time, so one absurd value costs its own
    # row instead of the whole upload.
    if latency_ms > MAX_LATENCY_MS:
        raise RowError(f"latency {raw!r} {key} is larger than any real check")

    if key == "s":
        issues["latency_converted_from_seconds"] += 1
    return latency_ms


def _outcome(status_code: int, issues: Counter) -> str:
    if 200 <= status_code < 400:
        return "up"
    if 500 <= status_code < 600:
        return "down"
    # 1xx, 4xx and the seeded 999 prove neither up nor down (decision D2).
    issues["invalid_status_codes"] += 1
    return "invalid"


def _clean_row(row: RawRow, issues: Counter) -> CleanCheck:
    service_id = row.values["service_id"].strip()
    if not service_id:
        raise RowError("service_id is blank")

    agent_id = row.values["agent"].strip()
    if not agent_id:
        raise RowError("agent is blank")

    checked_at = _parse_timestamp(row.values["timestamp"], issues)
    # Once in UTC every timestamp lands on a 15-minute boundary. One that does
    # not means the row was read wrongly, so it is not trusted.
    if checked_at.microsecond or int(checked_at.timestamp()) % CHECK_INTERVAL_SECONDS:
        raise RowError(f"{checked_at.isoformat()} is not on the 15-minute grid")

    raw_code = row.values["status_code"].strip()
    try:
        status_code = int(raw_code)
    except ValueError as exc:
        raise RowError(f"status_code {raw_code!r} is not a number") from exc

    # Same reason as the latency ceiling: a value the column cannot hold is
    # this row's problem, not the file's.
    if not MIN_STATUS_CODE <= status_code <= MAX_STATUS_CODE:
        raise RowError(f"status_code {status_code} is outside {MIN_STATUS_CODE}-{MAX_STATUS_CODE}")

    return CleanCheck(
        line=row.line,
        service_id=service_id,
        service_name=row.values["service_name"].strip(),
        checked_at=checked_at,
        status_code=status_code,
        outcome=_outcome(status_code, issues),
        latency_ms=_parse_latency(row.values["latency"], row.values["latency_unit"], issues),
        agent_id=agent_id,
        region_id=row.values["region"].strip(),
    )


def clean_rows(rows: list[RawRow]) -> CleanResult:
    """Clean every row, then keep one check per service per time slot."""
    issues: Counter = Counter()
    rejected: list[RejectedRowReport] = []
    cleaned: list[CleanCheck] = []
    seen_raw: set[tuple[str, ...]] = set()
    exact_rows = 0

    for row in rows:
        try:
            check = _clean_row(row, issues)
        except RowError as exc:
            if len(rejected) < MAX_REPORTED_REJECTIONS:
                rejected.append(RejectedRowReport(line=row.line, reason=str(exc)))
            continue

        # Byte-for-byte repeats of an earlier line: noise, not a second opinion.
        if row.raw in seen_raw:
            exact_rows += 1
        else:
            seen_raw.add(row.raw)
        cleaned.append(check)

    if not cleaned:
        raise UploadRejected(
            ErrorCode.NO_VALID_ROWS,
            "No row in the file could be read as a health check.",
            {"rows_received": len(rows), "examples": [r.reason for r in rejected[:5]]},
        )

    slots: dict[tuple[str, datetime], list[CleanCheck]] = {}
    for check in cleaned:
        slots.setdefault((check.service_id, check.checked_at), []).append(check)

    checks: list[CleanCheck] = []
    conflicting = 0
    for group in slots.values():
        if len(group) > 1 and len({c.status_code for c in group}) > 1:
            conflicting += 1
        # Ties break on line order, so the first such row in the file wins.
        checks.append(min(group, key=lambda c: (OUTCOME_PRIORITY[c.outcome], c.line)))

    checks.sort(key=lambda c: (c.checked_at, c.service_id))

    total = len(cleaned) - len(checks)
    return CleanResult(
        checks=checks,
        rows_received=len(rows),
        issues=issues,
        duplicates=DuplicateReport(
            total=total,
            exact_rows=exact_rows,
            same_slot_rows=total - exact_rows,
            conflicting_slots=conflicting,
        ),
        rejected=rejected,
    )


# --------------------------------------------------------------------------
# Saving
#
# The master rows come from the file itself rather than from a fixed seed, so
# a service or agent nobody has seen before still uploads cleanly. A check is
# only ever inserted once its service, agent and region exist, because
# service_status_logs has foreign keys to all three.
# --------------------------------------------------------------------------

CHECK_COLUMNS = "file_id, service_id, checked_at, status_code, outcome, latency_ms, agent_id"

# COPY cannot skip conflicting rows, so the checks land in a temp table first
# and move across in one statement that can. The temp table disappears when the
# transaction ends, whether it commits or rolls back.
CREATE_STAGING = """
CREATE TEMP TABLE staged_checks (
    file_id bigint,
    service_id text,
    checked_at timestamptz,
    status_code smallint,
    outcome text,
    latency_ms numeric(12, 3),
    agent_id text
) ON COMMIT DROP
"""

COPY_CHECKS = f"COPY staged_checks ({CHECK_COLUMNS}) FROM STDIN"

# A slot already held for this file keeps the check that got there first; the
# later one is skipped and the rest of the file still saves.
INSERT_CHECKS = f"""
INSERT INTO service_status_logs ({CHECK_COLUMNS})
SELECT {CHECK_COLUMNS} FROM staged_checks
ON CONFLICT (file_id, service_id, checked_at) DO NOTHING
"""


def _sync_master_rows(session: Session, checks: list[CleanCheck], warnings: list[str]) -> None:
    regions = sorted({check.region_id for check in checks})
    session.execute(pg_insert(Region).values([{"region_id": r} for r in regions]).on_conflict_do_nothing())

    # One region per agent, one name per service. Both are assumptions the
    # database depends on, so a file that breaks them is worth saying so about.
    agent_regions: dict[str, set[str]] = {}
    service_names: dict[str, set[str]] = {}
    for check in checks:
        agent_regions.setdefault(check.agent_id, set()).add(check.region_id)
        service_names.setdefault(check.service_id, set()).add(check.service_name)

    for agent_id, seen in sorted(agent_regions.items()):
        if len(seen) > 1:
            warnings.append(f"{agent_id} reported from more than one region: {', '.join(sorted(seen))}.")

    for service_id, seen in sorted(service_names.items()):
        if len(seen) > 1:
            warnings.append(f"{service_id} appeared under more than one name: {', '.join(sorted(seen))}.")

    session.execute(
        pg_insert(Agent)
        .values([{"agent_id": a, "region_id": sorted(r)[0]} for a, r in sorted(agent_regions.items())])
        .on_conflict_do_nothing()
    )

    stored_names = dict(
        session.execute(
            select(Service.service_id, Service.service_name).where(Service.service_id.in_(service_names))
        ).all()
    )
    for service_id, seen in sorted(service_names.items()):
        stored = stored_names.get(service_id)
        if stored and stored not in seen:
            warnings.append(
                f"{service_id} is stored as {stored!r} but this file calls it "
                f"{sorted(seen)[0]!r}; the stored name was kept."
            )

    session.execute(
        pg_insert(Service)
        .values([{"service_id": s, "service_name": sorted(n)[0]} for s, n in sorted(service_names.items())])
        .on_conflict_do_nothing()
    )


@lru_cache(maxsize=1)
def _s3():
    return boto3.client("s3", region_name=AWS_REGION)


def store_raw_file(file_id: int, file_name: str, content: bytes) -> None:
    """Keep the file exactly as uploaded, in a folder named after its file_id.

    S3 has no real folders: the "<file_id>/" key prefix is what shows as one."""
    if not S3_BUCKET:
        raise RuntimeError("S3_BUCKET is not set.")
    _s3().put_object(
        Bucket=S3_BUCKET,
        Key=f"{file_id}/{os.path.basename(file_name)}",
        Body=content,
        ContentType="text/csv",
    )


def save_upload(file_name: str, content: bytes, result: CleanResult, warnings: list[str]) -> int:
    """Store the raw file in S3, save the clean checks and return the new file_id."""
    with Session(engine) as session:
        _sync_master_rows(session, result.checks, warnings)
        session.commit()

        # Committed on its own, before the checks: if the insert below fails,
        # this row survives to record that the upload was attempted.
        uploaded = UploadedFile(file_name=file_name, status="processing", rows_received=0)
        session.add(uploaded)
        session.commit()
        file_id = uploaded.file_id

        try:
            # First, so the raw file of an upload that fails below is still
            # there to look at. No S3 copy, no upload: the file is marked failed.
            store_raw_file(file_id, file_name, content)

            # COPY rather than an INSERT per check: a round trip to the
            # database costs ~2 seconds, so 14,400 inserts take a minute while
            # one COPY of the same rows takes under two.
            session.execute(text(CREATE_STAGING))
            raw_connection = session.connection().connection
            with raw_connection.cursor() as cursor, cursor.copy(COPY_CHECKS) as copy:
                for check in result.checks:
                    copy.write_row(
                        (
                            file_id,
                            check.service_id,
                            check.checked_at,
                            check.status_code,
                            check.outcome,
                            check.latency_ms,
                            check.agent_id,
                        )
                    )

            stored = session.execute(text(INSERT_CHECKS)).rowcount
            skipped = len(result.checks) - stored
            if skipped:
                # Deduplication should have made this impossible, so it means a
                # bug rather than messy data. Saying so beats saving quietly.
                warnings.append(f"{skipped} check(s) landed on a slot this file had already filled, and were skipped.")

            # Scanned in the same transaction as the checks, so a file the
            # dashboard lists always has its incidents stored: the dashboard
            # reads them from here and never works them out per request.
            _record_outages(session, file_id)

            uploaded.rows_received = result.rows_received
            uploaded.status = "done"
            session.commit()
        except Exception as exc:
            # Roll the checks back, then record why on the row from step one.
            # A half-saved file must never reach the dashboard, which only
            # lists uploads with status 'done'.
            session.rollback()
            uploaded.status = "failed"
            uploaded.error_message = str(exc)[:1000]
            session.commit()
            raise

    return file_id


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


def build_report(
    file_id: int, file_name: str, result: CleanResult, ragged: int, extra_warnings: list[str]
) -> UploadReport:
    start = result.checks[0].checked_at
    end = result.checks[-1].checked_at
    services = sorted({check.service_id for check in result.checks})
    days = (end.date() - start.date()).days + 1
    expected = len(services) * CHECKS_PER_DAY * days

    coverage = Coverage(
        start=start,
        end=end,
        days=days,
        services=services,
        expected_checks=expected,
        # Never negative: an extra check would mean a slot was counted twice,
        # which deduplication has already ruled out.
        missing_checks=max(0, expected - len(result.checks)),
    )

    warnings: list[str] = list(extra_warnings)
    if ragged:
        warnings.append(f"{ragged} line(s) had the wrong number of fields and were skipped.")
    if result.rejected:
        warnings.append(f"{len(result.rejected)} row(s) could not be read and were skipped.")
    if coverage.missing_checks:
        warnings.append(
            f"{coverage.missing_checks} expected check(s) are missing "
            f"({len(services)} services x {CHECKS_PER_DAY} slots x {days} days)."
        )
    if result.duplicates.conflicting_slots:
        warnings.append(
            f"{result.duplicates.conflicting_slots} time slot(s) had agents reporting "
            "different status codes; the more serious valid result was kept."
        )

    return UploadReport(
        file_id=file_id,
        file_name=file_name,
        status="accepted_with_warnings" if warnings else "clean",
        rows_received=result.rows_received,
        clean_checks=len(result.checks),
        rows_removed=result.rows_received - len(result.checks),
        coverage=coverage,
        duplicates=result.duplicates,
        issues=IssueReport(**result.issues),
        ragged_rows=ragged,
        rejected_rows=result.rejected,
        warnings=warnings,
    )


# --------------------------------------------------------------------------
# Outages
#
# An incident is a run of down checks on one service, taken from
# service_status_logs. Nothing else feeds it: the checks in the database are
# the only input, so the result always matches what was actually stored.
#
# test_outage_incidents describes an incident as a day and a range of
# check-points within that day, so a run crossing midnight is split at the day
# boundary and stored as one row per day.
# --------------------------------------------------------------------------

SLOT = timedelta(seconds=CHECK_INTERVAL_SECONDS)
SLOT_MINUTES = CHECK_INTERVAL_SECONDS // 60

# Ordered by service first, so one service's failures form one continuous run
# regardless of what the other services were doing at the time.
LOAD_DOWN_CHECKS = (
    select(ServiceStatusLog.service_id, ServiceStatusLog.checked_at)
    .where(ServiceStatusLog.outcome == "down")
    .order_by(ServiceStatusLog.service_id, ServiceStatusLog.checked_at)
)

LOAD_COVERAGE = select(func.min(ServiceStatusLog.checked_at), func.max(ServiceStatusLog.checked_at))


def _checkpoint(moment: datetime) -> int:
    """Which of the day's 96 check-points a time falls on."""
    return (moment.hour * 60 + moment.minute) // SLOT_MINUTES


def _moment(start_date: date, day_index: int, checkpoint: int) -> datetime:
    """The UTC time a stored day and check-point refer to."""
    return datetime.combine(start_date, datetime.min.time(), UTC) + timedelta(
        days=day_index, minutes=checkpoint * SLOT_MINUTES
    )


def detect_incidents(down_checks: list[tuple[str, datetime]], start_date: date) -> list[OutageIncident]:
    """Group down checks into incidents, merging runs across short healthy gaps."""
    runs: list[list[tuple[str, datetime]]] = []

    for check in down_checks:
        service_id, checked_at = check
        if runs:
            last_service, last_at = runs[-1][-1]
            # The run continues while the healthy stretch since the last
            # failure is within tolerance. A missing slot counts as healthy:
            # no data is not a failure (decision D3).
            if last_service == service_id and checked_at - last_at <= SLOT * (MAX_HEALTHY_GAP_SLOTS + 1):
                runs[-1].append(check)
                continue
        runs.append([check])

    incidents: list[OutageIncident] = []
    for run in runs:
        days: dict[int, list[datetime]] = {}
        for _, checked_at in run:
            days.setdefault((checked_at.date() - start_date).days, []).append(checked_at)

        for day_index, moments in sorted(days.items()):
            incidents.append(
                OutageIncident(
                    service_id=run[0][0],
                    day_index=day_index,
                    checkpoint_start=_checkpoint(moments[0]),
                    checkpoint_end=_checkpoint(moments[-1]),
                    started_at=moments[0],
                    ended_at=moments[-1],
                    down_checks=len(moments),
                    # Downtime counts the failed checks, not the span: the
                    # healthy checks inside a flickering outage were really up.
                    downtime_minutes=len(moments) * SLOT_MINUTES,
                )
            )

    incidents.sort(key=lambda incident: (incident.started_at, incident.service_id))
    return incidents


# A stored row keeps only its range. While a healthy check ends every incident
# (MAX_HEALTHY_GAP_SLOTS = 0), each slot in that range failed, so the range's
# length is the number of failed checks and the checks never need reading
# again. Raising the gap would break this: the count would then have to be
# stored, or recounted from service_status_logs.
INCIDENT_LENGTH = TestOutageIncident.checkpoint_end - TestOutageIncident.checkpoint_start + 1


def _as_incident(row: TestOutageIncident, start_date: date) -> OutageIncident:
    down_checks = row.checkpoint_end - row.checkpoint_start + 1
    return OutageIncident(
        service_id=row.service_id,
        day_index=row.day_index,
        checkpoint_start=row.checkpoint_start,
        checkpoint_end=row.checkpoint_end,
        started_at=_moment(start_date, row.day_index, row.checkpoint_start),
        ended_at=_moment(start_date, row.day_index, row.checkpoint_end),
        down_checks=down_checks,
        downtime_minutes=down_checks * SLOT_MINUTES,
    )


def _stored_incidents(session: Session, outage: TestOutage) -> list[OutageIncident]:
    """Read back a scan done earlier."""
    rows = session.execute(
        select(TestOutageIncident)
        .where(TestOutageIncident.file_id == outage.file_id)
        .order_by(TestOutageIncident.day_index, TestOutageIncident.checkpoint_start, TestOutageIncident.service_id)
    ).scalars()
    return [_as_incident(row, outage.start_date) for row in rows]


def _record_outages(session: Session, file_id: int) -> tuple[date, int, list[OutageIncident]]:
    """Detect a file's incidents from its stored checks and add them to the
    session. Nothing is committed here: the caller decides the transaction."""
    first, last = session.execute(LOAD_COVERAGE.where(ServiceStatusLog.file_id == file_id)).one()
    if first is None:
        raise HTTPException(status_code=409, detail=f"File {file_id} has no stored checks to scan.")

    start_date = first.date()
    days = (last.date() - start_date).days + 1
    down_checks = [
        (row.service_id, row.checked_at)
        for row in session.execute(LOAD_DOWN_CHECKS.where(ServiceStatusLog.file_id == file_id))
    ]
    incidents = detect_incidents(down_checks, start_date)

    # The parent row first: test_outage_incidents points at it.
    session.add(TestOutage(file_id=file_id, days=days, start_date=start_date))
    session.add_all(
        TestOutageIncident(
            file_id=file_id,
            service_id=incident.service_id,
            day_index=incident.day_index,
            checkpoint_start=incident.checkpoint_start,
            checkpoint_end=incident.checkpoint_end,
        )
        for incident in incidents
    )
    return start_date, days, incidents


def _outage_report(
    file_id: int,
    file_name: str,
    already_processed: bool,
    start_date: date,
    days: int,
    incidents: list[OutageIncident],
) -> OutageReport:
    return OutageReport(
        file_id=file_id,
        file_name=file_name,
        already_processed=already_processed,
        start_date=start_date,
        days=days,
        incidents_found=len(incidents),
        services_affected=sorted({incident.service_id for incident in incidents}),
        total_downtime_minutes=sum(incident.downtime_minutes for incident in incidents),
        incidents=incidents,
    )


def scan_outages(file_id: int) -> OutageReport:
    """Detect this file's incidents and store them, unless it is already done.
    Every upload is scanned as it saves, so this mostly reads a scan back; it
    still scans an upload saved before that was the case."""
    with Session(engine) as session:
        uploaded = session.get(UploadedFile, file_id)
        if uploaded is None:
            raise HTTPException(status_code=404, detail=f"No uploaded file with id {file_id}.")
        if uploaded.status != "done":
            raise HTTPException(
                status_code=409,
                detail=f"File {file_id} is {uploaded.status}; only a file that saved completely can be scanned.",
            )
        file_name = uploaded.file_name

        # A file already scanned is read back rather than written a second
        # time. Keyed by file_id, not name: two uploads that share a name are
        # separate datasets and each gets its own scan.
        stored = session.get(TestOutage, file_id)
        if stored is not None:
            return _outage_report(
                file_id, file_name, True, stored.start_date, stored.days, _stored_incidents(session, stored)
            )

        start_date, days, incidents = _record_outages(session, file_id)
        # One transaction, so the tables never hold a half-finished scan.
        session.commit()

    return _outage_report(file_id, file_name, False, start_date, days, incidents)


# --------------------------------------------------------------------------
# Dashboard reads
#
# Availability is up / (up + down). Invalid codes and missing slots are left
# out rather than counted as failures (decision D3: no data is not a
# failure), and reported beside it so the reader can see how much the number
# rests on.
# --------------------------------------------------------------------------


def _stored_file(session: Session, file_id: int) -> UploadedFile:
    """An upload the dashboard can show: one that saved completely."""
    uploaded = session.get(UploadedFile, file_id)
    if uploaded is None or uploaded.status != "done":
        raise HTTPException(status_code=404, detail=f"No completed upload with id {file_id}.")
    return uploaded


def _pct(part: int, whole: int) -> float | None:
    return round(part * 100 / whole, 4) if whole else None


def list_files() -> list[FileSummary]:
    checks = (
        select(ServiceStatusLog.file_id, func.count().label("stored_checks"))
        .group_by(ServiceStatusLog.file_id)
        .subquery()
    )
    query = (
        select(
            UploadedFile,
            func.coalesce(checks.c.stored_checks, 0),
            TestOutage.file_id.is_not(None),
        )
        .outerjoin(checks, checks.c.file_id == UploadedFile.file_id)
        .outerjoin(TestOutage, TestOutage.file_id == UploadedFile.file_id)
        .where(UploadedFile.status == "done")
        .order_by(UploadedFile.uploaded_at.desc())
    )
    with Session(engine) as session:
        return [
            FileSummary(
                file_id=uploaded.file_id,
                file_name=uploaded.file_name,
                uploaded_at=uploaded.uploaded_at,
                rows_received=uploaded.rows_received,
                stored_checks=stored_checks,
                scanned=scanned,
            )
            for uploaded, stored_checks, scanned in session.execute(query)
        ]


@lru_cache(maxsize=32)
def _check_totals(file_id: int) -> tuple[datetime, datetime, tuple]:
    """A file's coverage and per-service counts and latencies: the one read
    over all of its checks. A file's checks never change once it has saved
    (a new upload gets a new file_id), so it is read once per file and kept,
    rather than again for the stats and again for the service table. Callers
    check the upload still exists first, so a deleted file is never served."""
    with Session(engine) as session:
        first, last = session.execute(LOAD_COVERAGE.where(ServiceStatusLog.file_id == file_id)).one()
        if first is None:
            raise HTTPException(status_code=404, detail=f"File {file_id} has no stored checks.")

        per_service = session.execute(
            select(
                ServiceStatusLog.service_id,
                Service.service_name,
                func.count().filter(ServiceStatusLog.outcome == "up"),
                func.count().filter(ServiceStatusLog.outcome == "down"),
                func.count().filter(ServiceStatusLog.outcome == "invalid"),
                func.avg(ServiceStatusLog.latency_ms),
                func.percentile_cont(0.95).within_group(ServiceStatusLog.latency_ms),
            )
            .join(Service, Service.service_id == ServiceStatusLog.service_id)
            .where(ServiceStatusLog.file_id == file_id)
            .group_by(ServiceStatusLog.service_id, Service.service_name)
            .order_by(ServiceStatusLog.service_id)
        ).all()

    return first, last, tuple(tuple(row) for row in per_service)


def _file_stats(file_id: int) -> DashboardStats:
    """The headline figures for one file. Incidents come only from the stored
    scan; a file with none stored shows none, rather than being scanned here."""
    with Session(engine) as session:
        uploaded = _stored_file(session, file_id)
        scanned = session.get(TestOutage, file_id) is not None

        # Count and longest incident per service, from the small incident
        # table rather than from the checks.
        incident_totals = {
            service_id: (count, longest * SLOT_MINUTES)
            for service_id, count, longest in session.execute(
                select(TestOutageIncident.service_id, func.count(), func.max(INCIDENT_LENGTH))
                .where(TestOutageIncident.file_id == file_id)
                .group_by(TestOutageIncident.service_id)
            )
        }

    first, last, per_service = _check_totals(file_id)
    days = (last.date() - first.date()).days + 1

    services: list[ServiceStats] = []
    for service_id, service_name, up, down, invalid, avg_latency, p95_latency in per_service:
        incident_count, longest = incident_totals.get(service_id, (0, 0))
        availability = _pct(up, up + down)
        services.append(
            ServiceStats(
                service_id=service_id,
                service_name=service_name,
                up_checks=up,
                down_checks=down,
                invalid_checks=invalid,
                missing_checks=max(0, CHECKS_PER_DAY * days - (up + down + invalid)),
                availability_pct=availability,
                meets_sla=None if availability is None else availability >= SLA_TARGET_PCT,
                downtime_minutes=down * SLOT_MINUTES,
                # The downtime the SLA tolerates over the minutes actually observed.
                allowed_downtime_minutes=round((up + down) * SLOT_MINUTES * (100 - SLA_TARGET_PCT) / 100, 2),
                incidents=incident_count,
                longest_incident_minutes=longest,
                avg_latency_ms=None if avg_latency is None else round(float(avg_latency), 1),
                p95_latency_ms=None if p95_latency is None else round(float(p95_latency), 1),
            )
        )

    total_up = sum(service.up_checks for service in services)
    total_down = sum(service.down_checks for service in services)
    return DashboardStats(
        file_id=file_id,
        file_name=uploaded.file_name,
        scanned=scanned,
        sla_target_pct=SLA_TARGET_PCT,
        coverage_start=first,
        coverage_end=last,
        days=days,
        availability_pct=_pct(total_up, total_up + total_down),
        services_breaching=sum(service.meets_sla is False for service in services),
        total_downtime_minutes=total_down * SLOT_MINUTES,
        incident_count=sum(count for count, _ in incident_totals.values()),
        longest_incident_minutes=max((longest for _, longest in incident_totals.values()), default=0),
        services=services,
    )


def dashboard_stats(file_id: int) -> DashboardStats:
    return _file_stats(file_id)


def _sorted_page(items: list, value, tiebreak, order: str, page: int, page_size: int) -> tuple[int, list]:
    """One page of rows sorted by `value`. Empty values stay last whichever
    way it runs, and `tiebreak` keeps equal rows in a fixed order, so a row
    never shows up on two pages."""
    present = sorted((item for item in items if value(item) is not None), key=tiebreak)
    empty = sorted((item for item in items if value(item) is None), key=tiebreak)
    # Python's sort is stable, also in reverse, so ties keep the tiebreak order.
    present.sort(key=value, reverse=order == "desc")
    ordered = present + empty
    start = (page - 1) * page_size
    return len(ordered), ordered[start : start + page_size]


# Breached (0) sorts before met (1); a service with no valid check has no verdict.
SERVICE_SORT_VALUES = {
    "service": lambda service: service.service_name,
    "availability": lambda service: service.availability_pct,
    "sla": lambda service: None if service.meets_sla is None else int(service.meets_sla),
    "downtime": lambda service: service.downtime_minutes,
    "incidents": lambda service: service.incidents,
    "longest": lambda service: service.longest_incident_minutes,
    "p95": lambda service: service.p95_latency_ms,
    "gaps": lambda service: service.invalid_checks + service.missing_checks,
}

# Day then check-point is start time; the range's length is both the failed
# checks and the downtime.
INCIDENT_SORT_COLUMNS = {
    "service": (TestOutageIncident.service_id,),
    "started": (TestOutageIncident.day_index, TestOutageIncident.checkpoint_start),
    "ended": (TestOutageIncident.day_index, TestOutageIncident.checkpoint_end),
    "checks": (INCIDENT_LENGTH,),
    "downtime": (INCIDENT_LENGTH,),
}


def service_page(file_id: int, sort: ServiceSort, order: str, page: int, page_size: int) -> ServicePage:
    # One row per service, so the handful of them is sorted here.
    summary = _file_stats(file_id)
    total, items = _sorted_page(
        summary.services, SERVICE_SORT_VALUES[sort], lambda service: service.service_id, order, page, page_size
    )
    return ServicePage(total=total, page=page, page_size=page_size, items=items)


def incident_page(file_id: int, sort: IncidentSort, order: str, page: int, page_size: int) -> IncidentPage:
    """One page of the stored incidents, sorted and paged by the database."""
    condition = TestOutageIncident.file_id == file_id
    with Session(engine) as session:
        _stored_file(session, file_id)
        outage = session.get(TestOutage, file_id)
        if outage is None:
            return IncidentPage(total=0, page=page, page_size=page_size, items=[])

        total = session.execute(select(func.count()).select_from(TestOutageIncident).where(condition)).scalar_one()
        columns = INCIDENT_SORT_COLUMNS[sort]
        rows = session.execute(
            select(TestOutageIncident)
            .where(condition)
            # Start time then service break ties, so a row never shows up on
            # two pages.
            .order_by(
                *(column.desc() if order == "desc" else column.asc() for column in columns),
                TestOutageIncident.day_index,
                TestOutageIncident.checkpoint_start,
                TestOutageIncident.service_id,
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).scalars()
        items = [_as_incident(row, outage.start_date) for row in rows]

    return IncidentPage(total=total, page=page, page_size=page_size, items=items)


LOG_SORT_COLUMNS = {
    "checked_at": ServiceStatusLog.checked_at,
    "service": ServiceStatusLog.service_id,
    "status": ServiceStatusLog.status_code,
    "outcome": ServiceStatusLog.outcome,
    "latency": ServiceStatusLog.latency_ms,
    "agent": ServiceStatusLog.agent_id,
    "region": Agent.region_id,
}


def read_logs(
    file_id: int,
    date_from: date | None,
    date_to: date | None,
    service_id: str | None,
    outcome: str | None,
    page: int,
    page_size: int,
    sort: LogSort = "checked_at",
    order: Literal["asc", "desc"] = "asc",
) -> LogPage:
    """One page of stored checks. Dates are whole UTC days, both inclusive."""
    if date_from and date_to and date_to < date_from:
        raise HTTPException(status_code=422, detail="date_to is before date_from.")

    conditions = [ServiceStatusLog.file_id == file_id]
    if date_from:
        conditions.append(ServiceStatusLog.checked_at >= datetime.combine(date_from, datetime.min.time(), UTC))
    if date_to:
        next_day = datetime.combine(date_to + timedelta(days=1), datetime.min.time(), UTC)
        conditions.append(ServiceStatusLog.checked_at < next_day)
    if service_id:
        conditions.append(ServiceStatusLog.service_id == service_id)
    if outcome:
        conditions.append(ServiceStatusLog.outcome == outcome)
    sort_column = LOG_SORT_COLUMNS[sort]

    with Session(engine) as session:
        _stored_file(session, file_id)
        total = session.execute(select(func.count()).select_from(ServiceStatusLog).where(*conditions)).scalar_one()
        rows = session.execute(
            select(ServiceStatusLog, Agent.region_id)
            .join(Agent, Agent.agent_id == ServiceStatusLog.agent_id)
            .where(*conditions)
            # Empty values last either way; time then service break ties, so a
            # row never shows up on two pages.
            .order_by(
                sort_column.desc().nulls_last() if order == "desc" else sort_column.asc().nulls_last(),
                ServiceStatusLog.checked_at,
                ServiceStatusLog.service_id,
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()

    return LogPage(
        total=total,
        page=page,
        page_size=page_size,
        items=[
            LogRow(
                checked_at=log.checked_at,
                service_id=log.service_id,
                status_code=log.status_code,
                outcome=log.outcome,
                latency_ms=None if log.latency_ms is None else float(log.latency_ms),
                agent_id=log.agent_id,
                region_id=region_id,
            )
            for log, region_id in rows
        ],
    )

# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/db")
def health_db() -> dict[str, str]:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    return {"status": "ok"}


@app.post("/uploads", response_model=UploadReport)
async def create_upload(file: UploadFile = File(...)) -> UploadReport:
    """Validate an uploaded CSV, clean it, save it, and report what was found."""
    file_name = file.filename or ""
    content = await file.read()

    try:
        csv_text = validate_file(file_name, content)
        rows, ragged = validate_structure(csv_text)
        result = clean_rows(rows)
    except UploadRejected as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code.value, "message": exc.message, "details": exc.details},
        ) from exc

    warnings: list[str] = []
    try:
        file_id = save_upload(file_name, content, result, warnings)
    except (SQLAlchemyError, psycopg.Error) as exc:
        # COPY reports failures as psycopg errors, not SQLAlchemy ones.
        raise HTTPException(status_code=503, detail="The file was read but could not be saved.") from exc
    except (BotoCoreError, ClientError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="The file was read but could not be stored in S3.") from exc

    return build_report(file_id, file_name, result, ragged, warnings)


@app.post("/outage", response_model=OutageReport)
def create_outages(file_id: int = Query(..., ge=1, description="An uploaded file's id")) -> OutageReport:
    """Detect the outages in an uploaded file's stored checks and save them."""
    try:
        return scan_outages(file_id)
    except (SQLAlchemyError, psycopg.Error) as exc:
        raise HTTPException(status_code=503, detail="The outages could not be read or saved.") from exc


@app.get("/files", response_model=list[FileSummary])
def get_files() -> list[FileSummary]:
    """Uploads that saved completely, newest first."""
    try:
        return list_files()
    except (SQLAlchemyError, psycopg.Error) as exc:
        raise HTTPException(status_code=503, detail="The uploads could not be read.") from exc


@app.get("/files/{file_id}/stats", response_model=DashboardStats)
def get_stats(file_id: int) -> DashboardStats:
    """SLA, downtime, incident and latency figures per service for one file."""
    try:
        return dashboard_stats(file_id)
    except (SQLAlchemyError, psycopg.Error) as exc:
        raise HTTPException(status_code=503, detail="The stats could not be read.") from exc


@app.get("/files/{file_id}/stats/services", response_model=ServicePage)
def get_service_stats(
    file_id: int,
    sort: ServiceSort = "availability",
    order: Literal["asc", "desc"] = "asc",
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=MAX_STATS_PAGE_SIZE),
) -> ServicePage:
    """The per-service figures, sorted and paged. Worst availability first by default."""
    try:
        return service_page(file_id, sort, order, page, page_size)
    except (SQLAlchemyError, psycopg.Error) as exc:
        raise HTTPException(status_code=503, detail="The service stats could not be read.") from exc


@app.get("/files/{file_id}/stats/incidents", response_model=IncidentPage)
def get_incidents(
    file_id: int,
    sort: IncidentSort = "downtime",
    order: Literal["asc", "desc"] = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=MAX_STATS_PAGE_SIZE),
) -> IncidentPage:
    """The file's incidents, sorted and paged. Longest first by default."""
    try:
        return incident_page(file_id, sort, order, page, page_size)
    except (SQLAlchemyError, psycopg.Error) as exc:
        raise HTTPException(status_code=503, detail="The incidents could not be read.") from exc


@app.get("/files/{file_id}/logs", response_model=LogPage)
def get_logs(
    file_id: int,
    date_from: date | None = Query(None, description="First UTC day, inclusive"),
    date_to: date | None = Query(None, description="Last UTC day, inclusive; equal to date_from for a single day"),
    service_id: str | None = None,
    outcome: Literal["up", "down", "invalid"] | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=MAX_LOG_PAGE_SIZE),
    sort: LogSort = "checked_at",
    order: Literal["asc", "desc"] = "asc",
) -> LogPage:
    """The stored checks behind the stats, filtered, sorted and paged."""
    try:
        return read_logs(file_id, date_from, date_to, service_id, outcome, page, page_size, sort, order)
    except (SQLAlchemyError, psycopg.Error) as exc:
        raise HTTPException(status_code=503, detail="The logs could not be read.") from exc
