"""SLA Monitoring API.

Upload endpoint: checks a CSV of health checks and its header, then stores it
in S3. The S3 write triggers AWS/lambda_function.py, which cleans the rows and
saves the checks and outages. The other endpoints read what it saved.
"""

import csv
import io
import logging
import os
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from functools import lru_cache
from typing import Literal

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mangum import Mangum
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import engine  # loads .env
from app.models import (
    Agent,
    Service,
    ServiceStatusLog,
    TestOutage,
    TestOutageIncident,
    UploadedFile,
)

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

ALLOWED_EXTENSIONS = (".csv",)

# The 30-day sample file is 1.1 MB. On AWS Lambda a request can be at most
# 6 MB, and the Function URL sends an uploaded file base64-encoded (a third
# bigger), so 4 MB is the largest file that always fits.
MAX_UPLOAD_BYTES = 4 * 1024 * 1024

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

# Every raw upload is kept at s3://S3_BUCKET/<file_id>/<file name>. Locally,
# credentials come from the usual AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
# variables; on Lambda, from the function's role.
S3_BUCKET = os.getenv("S3_BUCKET", "")
AWS_REGION = os.getenv("AWS_REGION") or None


class ErrorCode(StrEnum):
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


class UploadRejected(Exception):
    """An uploaded file that cannot be processed at all."""

    def __init__(self, code: ErrorCode, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


# --------------------------------------------------------------------------
# Response models
# --------------------------------------------------------------------------


class UploadAccepted(BaseModel):
    """What the API returns once the file is safely in S3. The Lambda in
    AWS/lambda_function.py cleans and saves it from there."""

    file_id: int
    file_name: str
    status: Literal["processing"]


class UploadStatus(BaseModel):
    """Where one upload has got to: 'processing' until the Lambda finishes."""

    file_id: int
    file_name: str
    uploaded_at: datetime
    status: Literal["processing", "done", "failed"]
    error_message: str | None
    rows_received: int


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


class FileSummary(BaseModel):
    """One upload, as the dashboard's file picker lists it."""

    file_id: int
    file_name: str
    uploaded_at: datetime
    rows_received: int


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


def validate_header(csv_text: str) -> None:
    """Check the header, and that at least one line follows it. The rows
    themselves are the Lambda's job."""
    reader = csv.reader(io.StringIO(csv_text, newline=""))

    try:
        raw_header = next(reader)
    except StopIteration as exc:
        raise UploadRejected(ErrorCode.MISSING_HEADER, "The file has no header row.") from exc

    header = [column.strip().lower() for column in raw_header]

    repeated = sorted(column for column, count in Counter(header).items() if count > 1)
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

    # Extra columns are ignored rather than rejected: they cost nothing.
    if not any(any(value.strip() for value in values) for values in reader):
        raise UploadRejected(ErrorCode.NO_DATA_ROWS, "The file has a header but no data rows.")


# --------------------------------------------------------------------------
# Handing the file to the Lambda
#
# The upload is recorded as 'processing' and its raw file put in S3. That S3
# write triggers AWS/lambda_function.py, which cleans the rows, saves the
# checks and outages, and sets the status to 'done' or 'failed'.
# --------------------------------------------------------------------------


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


def register_upload(file_name: str, content: bytes) -> int:
    """Record the upload, store the raw file in S3 and return the new file_id."""
    with Session(engine) as session:
        # Committed before the S3 write: the Lambda that write triggers looks
        # this row up, so it has to exist by then.
        uploaded = UploadedFile(file_name=file_name, status="processing", rows_received=0)
        session.add(uploaded)
        session.commit()
        file_id = uploaded.file_id

        try:
            store_raw_file(file_id, file_name, content)
        except Exception as exc:
            uploaded.status = "failed"
            uploaded.error_message = str(exc)[:1000]
            session.commit()
            raise

    return file_id


def upload_status(file_id: int) -> UploadStatus:
    with Session(engine) as session:
        uploaded = session.get(UploadedFile, file_id)
        if uploaded is None:
            raise HTTPException(status_code=404, detail=f"No uploaded file with id {file_id}.")
        return UploadStatus(
            file_id=uploaded.file_id,
            file_name=uploaded.file_name,
            uploaded_at=uploaded.uploaded_at,
            status=uploaded.status,
            error_message=uploaded.error_message,
            rows_received=uploaded.rows_received,
        )


# --------------------------------------------------------------------------
# Outages
#
# The Lambda (AWS/lambda_function.py) is the only writer of test_outages and
# test_outage_incidents: it detects the incidents as it saves each file. The
# API only reads them back.
#
# test_outage_incidents describes an incident as a day and a range of
# check-points within that day, so a run crossing midnight is stored as one
# row per day.
# --------------------------------------------------------------------------

SLOT_MINUTES = CHECK_INTERVAL_SECONDS // 60


def _moment(start_date: date, day_index: int, checkpoint: int) -> datetime:
    """The UTC time a stored day and check-point refer to."""
    return datetime.combine(start_date, datetime.min.time(), UTC) + timedelta(
        days=day_index, minutes=checkpoint * SLOT_MINUTES
    )


# A stored row keeps only its range. The Lambda ends an incident at the first
# healthy check (MAX_HEALTHY_GAP_SLOTS = 0 in AWS/lambda_function.py, decision
# D5), so every slot in the range failed and its length is the failed-check
# count. Raising that gap would mean storing the count instead.
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
    query = select(UploadedFile).where(UploadedFile.status == "done").order_by(UploadedFile.uploaded_at.desc())
    with Session(engine) as session:
        return [
            FileSummary(
                file_id=uploaded.file_id,
                file_name=uploaded.file_name,
                uploaded_at=uploaded.uploaded_at,
                rows_received=uploaded.rows_received,
            )
            for uploaded in session.scalars(query)
        ]


@lru_cache(maxsize=32)
def _check_totals(file_id: int) -> tuple[datetime, datetime, tuple]:
    """A file's coverage and per-service counts and latencies: the one read
    over all of its checks. A file's checks never change once it has saved
    (a new upload gets a new file_id), so it is read once per file and kept,
    rather than again for the stats and again for the service table. Callers
    check the upload still exists first, so a deleted file is never served."""
    with Session(engine) as session:
        first, last = session.execute(
            select(func.min(ServiceStatusLog.checked_at), func.max(ServiceStatusLog.checked_at)).where(
                ServiceStatusLog.file_id == file_id
            )
        ).one()
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


def file_stats(file_id: int) -> DashboardStats:
    """The headline figures for one file. Incidents come from the scan the
    Lambda stored with the checks."""
    with Session(engine) as session:
        uploaded = _stored_file(session, file_id)

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
    summary = file_stats(file_id)
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


app = FastAPI(title="SLA Monitoring API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.exception_handler(SQLAlchemyError)
async def database_unavailable(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    """Any database failure is the same to a caller: try again later."""
    logger.error("Database error on %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse(status_code=503, content={"detail": "The database is unavailable. Try again shortly."})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/db")
def health_db() -> dict[str, str]:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.post("/uploads", response_model=UploadAccepted, status_code=202)
async def create_upload(file: UploadFile = File(...)) -> UploadAccepted:
    """Check the file and its header, store it in S3, and hand it to the Lambda."""
    file_name = file.filename or ""
    content = await file.read()

    try:
        validate_header(validate_file(file_name, content))
    except UploadRejected as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": exc.code.value, "message": exc.message, "details": exc.details},
        ) from exc

    try:
        file_id = register_upload(file_name, content)
    except (BotoCoreError, ClientError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="The file could not be stored in S3.") from exc

    return UploadAccepted(file_id=file_id, file_name=file_name, status="processing")


@app.get("/uploads/{file_id}", response_model=UploadStatus)
def get_upload(file_id: int) -> UploadStatus:
    """Poll this after POST /uploads until status is 'done' or 'failed'."""
    return upload_status(file_id)


@app.get("/files", response_model=list[FileSummary])
def get_files() -> list[FileSummary]:
    """Uploads that saved completely, newest first."""
    return list_files()


@app.get("/files/{file_id}/stats", response_model=DashboardStats)
def get_stats(file_id: int) -> DashboardStats:
    """SLA, downtime, incident and latency figures per service for one file."""
    return file_stats(file_id)


@app.get("/files/{file_id}/stats/services", response_model=ServicePage)
def get_service_stats(
    file_id: int,
    sort: ServiceSort = "availability",
    order: Literal["asc", "desc"] = "asc",
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=MAX_STATS_PAGE_SIZE),
) -> ServicePage:
    """The per-service figures, sorted and paged. Worst availability first by default."""
    return service_page(file_id, sort, order, page, page_size)


@app.get("/files/{file_id}/stats/incidents", response_model=IncidentPage)
def get_incidents(
    file_id: int,
    sort: IncidentSort = "downtime",
    order: Literal["asc", "desc"] = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=MAX_STATS_PAGE_SIZE),
) -> IncidentPage:
    """The file's incidents, sorted and paged. Longest first by default."""
    return incident_page(file_id, sort, order, page, page_size)


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
    return read_logs(file_id, date_from, date_to, service_id, outcome, page, page_size, sort, order)


# The AWS Lambda entry point (handler "app.main.handler"): turns a Function URL
# request into an ASGI call to the same app uvicorn runs locally.
handler = Mangum(app, lifespan="off")
