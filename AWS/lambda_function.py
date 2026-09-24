"""SLA upload processor (AWS Lambda).

The API (BE/app/main.py, POST /uploads) only does the quick checks: the file is
a non-empty UTF-8 CSV whose header has every required column. It then records
the upload in uploaded_files with status 'processing' and puts the raw file at

    s3://<bucket>/<file_id>/<file name>

That S3 write triggers this function, which does the heavy part:

    1. download the CSV and split it into rows
    2. clean every row (timestamps to UTC, latency to ms, status -> outcome)
    3. keep one check per service per 15-minute slot
    4. upsert regions, agents and services
    5. COPY the checks into service_status_logs
    6. detect outages into test_outages / test_outage_incidents
    7. mark the upload 'done', or 'failed' with the reason

Steps 4-7 run in one transaction, so a half-saved file never reaches the
dashboard, which only lists uploads with status 'done'.

Environment variables:
    DATABASE_URL   the Neon connection string (postgresql://...?sslmode=require)
"""

import csv
import io
import os
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import unquote_plus

import boto3
import psycopg

DATABASE_URL = os.environ["DATABASE_URL"]

_s3 = boto3.client("s3")

# --------------------------------------------------------------------------
# Rules (kept in step with BE/app/main.py)
# --------------------------------------------------------------------------

# One check per service every 15 minutes.
CHECK_INTERVAL_SECONDS = 900
SLOT = timedelta(seconds=CHECK_INTERVAL_SECONDS)
SLOT_MINUTES = CHECK_INTERVAL_SECONDS // 60

LATENCY_MULTIPLIERS = {"ms": Decimal(1), "s": Decimal(1000)}

# A code has to fit the smallint column and be a plausible three-digit status.
MIN_STATUS_CODE = 100
MAX_STATUS_CODE = 999

# The largest value the Numeric(12, 3) column holds.
MAX_LATENCY_MS = Decimal("999999999.999")

# Decision D1: a failure seen by any agent is real evidence, and a valid
# reading beats an invalid code. Lower sorts first, so it wins the slot.
OUTCOME_PRIORITY = {"down": 0, "up": 1, "invalid": 2}

# Decision D5: a healthy check ends an incident. See MAX_HEALTHY_GAP_SLOTS in
# main.py before changing this; stored incidents are read back assuming 0.
MAX_HEALTHY_GAP_SLOTS = 0

# How many rejected rows are written to the log as examples.
MAX_LOGGED_REJECTIONS = 20

# Neon can take a few seconds to wake a suspended compute; fail rather than
# hang until the Lambda's own timeout.
CONNECT_TIMEOUT_SECONDS = 10


class RowError(ValueError):
    """A single row that cannot be cleaned. Skipped and counted, not fatal."""


class FileError(Exception):
    """A file that turns out to hold nothing worth saving."""


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def handler(event, context):
    """Process every CSV the S3 event names."""
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        # Keys arrive URL-encoded: a space in the file name comes through as '+'.
        key = unquote_plus(record["s3"]["object"]["key"])
        process_object(bucket, key)
    return {"ok": True}


def process_object(bucket: str, key: str) -> None:
    folder, _, _ = key.partition("/")
    if not folder.isdigit():
        print(f"Skipping {key}: not under a <file_id>/ folder.")
        return
    file_id = int(folder)

    with psycopg.connect(DATABASE_URL, connect_timeout=CONNECT_TIMEOUT_SECONDS) as conn:
        # S3 can deliver the same event more than once, even to two runs at
        # the same time. The row lock, held until the final commit, makes a
        # second run wait for the first and then see 'done' or 'failed', which
        # are final, so it skips instead of saving the file twice.
        row = conn.execute("SELECT status FROM uploaded_files WHERE file_id = %s FOR UPDATE", (file_id,)).fetchone()
        if row is None:
            print(f"Skipping {key}: no uploaded_files row {file_id}.")
            return
        if row[0] != "processing":
            print(f"Skipping {key}: file {file_id} is already {row[0]}.")
            return

        try:
            body = _s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            rows, ragged = read_rows(body.decode("utf-8-sig"))
            checks, rows_received, stats = clean_rows(rows)
            warnings = save(conn, file_id, checks)
            conn.execute(
                "UPDATE uploaded_files SET status = 'done', rows_received = %s WHERE file_id = %s",
                (rows_received, file_id),
            )
            conn.commit()
        except FileError as exc:
            # Bad data, not a fault: retrying would fail the same way.
            conn.rollback()
            _mark_failed(conn, file_id, str(exc))
            print(f"File {file_id} failed: {exc}")
            return
        except Exception as exc:
            conn.rollback()
            _mark_failed(conn, file_id, f"{type(exc).__name__}: {exc}")
            # Raised so the invocation shows as an error in CloudWatch. A retry
            # finds the file 'failed' and skips it.
            raise

    print(
        f"File {file_id} done: {rows_received} rows, {len(checks)} checks saved, "
        f"{ragged} ragged line(s), {stats['rejected']} rejected row(s), "
        f"{stats['duplicates']} duplicate(s), {stats['conflicting_slots']} conflicting slot(s)."
    )
    if stats["issues"]:
        print(
            "  cleaned: "
            + ", ".join(f"{count} {name.replace('_', ' ')}" for name, count in sorted(stats["issues"].items()))
        )
    for example in stats["rejected_examples"]:
        print(f"  rejected: {example}")
    for warning in warnings:
        print(f"  warning: {warning}")


def _mark_failed(conn: psycopg.Connection, file_id: int, message: str) -> None:
    # Only a file still processing: never overwrite another run's 'done'.
    conn.execute(
        "UPDATE uploaded_files SET status = 'failed', error_message = %s "
        "WHERE file_id = %s AND status = 'processing'",
        (message[:1000], file_id),
    )
    conn.commit()


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def read_rows(csv_text: str) -> tuple[list[tuple[int, dict[str, str]]], int]:
    """Split the body into (line, values by column) rows.

    The API has already checked the header, so it is only normalized here.
    Returns the rows, and how many lines had the wrong number of fields."""
    reader = csv.reader(io.StringIO(csv_text, newline=""))
    raw_header = next(reader)
    header = [column.strip().lower() for column in raw_header]
    width = len(raw_header)

    rows = []
    ragged = 0
    for values in reader:
        if not any(value.strip() for value in values):
            continue
        if len(values) != width:
            ragged += 1
            continue
        rows.append((reader.line_num, dict(zip(header, values))))

    if not rows:
        raise FileError("The file has a header but no data rows.")
    return rows, ragged


# --------------------------------------------------------------------------
# Cleaning, then duplicates
#
# One check is written three ways in the sample files (ISO Z, Unix epoch and a
# +05:30 offset), so duplicates are only found after timestamps are in UTC.
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

    # An offset is converted, never stripped.
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


def _clean_row(line: int, values: dict[str, str], issues: Counter) -> dict:
    service_id = values["service_id"].strip()
    if not service_id:
        raise RowError("service_id is blank")

    agent_id = values["agent"].strip()
    if not agent_id:
        raise RowError("agent is blank")

    checked_at = _parse_timestamp(values["timestamp"], issues)
    # In UTC every real check lands on a 15-minute boundary.
    if checked_at.microsecond or int(checked_at.timestamp()) % CHECK_INTERVAL_SECONDS:
        raise RowError(f"{checked_at.isoformat()} is not on the 15-minute grid")

    raw_code = values["status_code"].strip()
    try:
        status_code = int(raw_code)
    except ValueError as exc:
        raise RowError(f"status_code {raw_code!r} is not a number") from exc

    if not MIN_STATUS_CODE <= status_code <= MAX_STATUS_CODE:
        raise RowError(f"status_code {status_code} is outside {MIN_STATUS_CODE}-{MAX_STATUS_CODE}")

    return {
        "line": line,
        "service_id": service_id,
        "service_name": values["service_name"].strip(),
        "checked_at": checked_at,
        "status_code": status_code,
        "outcome": _outcome(status_code, issues),
        "latency_ms": _parse_latency(values["latency"], values["latency_unit"], issues),
        "agent_id": agent_id,
        "region_id": values["region"].strip(),
    }


def clean_rows(rows) -> tuple[list[dict], int, dict]:
    """Clean every row, then keep one check per service per time slot.

    Returns the checks sorted by time, the number of rows received, and
    counts for the log."""
    issues: Counter = Counter()
    rejected: list[str] = []
    cleaned: list[dict] = []

    for line, values in rows:
        try:
            cleaned.append(_clean_row(line, values, issues))
        except RowError as exc:
            rejected.append(f"line {line}: {exc}")

    if not cleaned:
        raise FileError("No row in the file could be read as a health check. Examples: " + "; ".join(rejected[:5]))

    slots: dict[tuple[str, datetime], list[dict]] = {}
    for check in cleaned:
        slots.setdefault((check["service_id"], check["checked_at"]), []).append(check)

    checks: list[dict] = []
    conflicting = 0
    for group in slots.values():
        if len(group) > 1 and len({c["status_code"] for c in group}) > 1:
            conflicting += 1
        # Ties break on line order, so the first such row in the file wins.
        checks.append(min(group, key=lambda c: (OUTCOME_PRIORITY[c["outcome"]], c["line"])))

    checks.sort(key=lambda c: (c["checked_at"], c["service_id"]))

    stats = {
        "rejected": len(rejected),
        "rejected_examples": rejected[:MAX_LOGGED_REJECTIONS],
        "duplicates": len(cleaned) - len(checks),
        "conflicting_slots": conflicting,
        "issues": dict(issues),
    }
    return checks, len(rows), stats


# --------------------------------------------------------------------------
# Saving
#
# A check is only inserted once its service, agent and region exist, because
# service_status_logs has foreign keys to all three.
# --------------------------------------------------------------------------

CHECK_COLUMNS = "file_id, service_id, checked_at, status_code, outcome, latency_ms, agent_id"

# COPY cannot skip conflicting rows, so the checks land in a temp table first
# and move across in one statement that can.
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

INSERT_CHECKS = f"""
INSERT INTO service_status_logs ({CHECK_COLUMNS})
SELECT {CHECK_COLUMNS} FROM staged_checks
ON CONFLICT (file_id, service_id, checked_at) DO NOTHING
"""


def _sync_master_rows(conn: psycopg.Connection, checks: list[dict], warnings: list[str]) -> None:
    agent_regions: dict[str, set[str]] = {}
    service_names: dict[str, set[str]] = {}
    for check in checks:
        agent_regions.setdefault(check["agent_id"], set()).add(check["region_id"])
        service_names.setdefault(check["service_id"], set()).add(check["service_name"])

    for agent_id, seen in sorted(agent_regions.items()):
        if len(seen) > 1:
            warnings.append(f"{agent_id} reported from more than one region: {', '.join(sorted(seen))}.")
    for service_id, seen in sorted(service_names.items()):
        if len(seen) > 1:
            warnings.append(f"{service_id} appeared under more than one name: {', '.join(sorted(seen))}.")

    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO regions (region_id) VALUES (%s) ON CONFLICT DO NOTHING",
            [(r,) for r in sorted({c["region_id"] for c in checks})],
        )
        cur.executemany(
            "INSERT INTO agents (agent_id, region_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            [(a, sorted(r)[0]) for a, r in sorted(agent_regions.items())],
        )

        stored_names = dict(
            cur.execute(
                "SELECT service_id, service_name FROM services WHERE service_id = ANY(%s)",
                (list(service_names),),
            ).fetchall()
        )
        for service_id, seen in sorted(service_names.items()):
            stored = stored_names.get(service_id)
            if stored and stored not in seen:
                warnings.append(
                    f"{service_id} is stored as {stored!r} but this file calls it "
                    f"{sorted(seen)[0]!r}; the stored name was kept."
                )

        cur.executemany(
            "INSERT INTO services (service_id, service_name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            [(s, sorted(n)[0]) for s, n in sorted(service_names.items())],
        )


def save(conn: psycopg.Connection, file_id: int, checks: list[dict]) -> list[str]:
    """Save the master rows, the checks and the outages. Nothing is committed
    here: the caller commits once, with the status change."""
    warnings: list[str] = []
    _sync_master_rows(conn, checks, warnings)

    with conn.cursor() as cur:
        cur.execute(CREATE_STAGING)
        with cur.copy(f"COPY staged_checks ({CHECK_COLUMNS}) FROM STDIN") as copy:
            for c in checks:
                copy.write_row(
                    (
                        file_id,
                        c["service_id"],
                        c["checked_at"],
                        c["status_code"],
                        c["outcome"],
                        c["latency_ms"],
                        c["agent_id"],
                    )
                )
        stored = cur.execute(INSERT_CHECKS).rowcount

    skipped = len(checks) - stored
    if skipped:
        warnings.append(f"{skipped} check(s) landed on a slot this file had already filled, and were skipped.")

    _record_outages(conn, file_id, checks)
    return warnings


# --------------------------------------------------------------------------
# Outages
#
# An incident is a run of down checks on one service. test_outage_incidents
# stores a day and a range of check-points within that day, so a run crossing
# midnight is split at the day boundary.
# --------------------------------------------------------------------------


def _checkpoint(moment: datetime) -> int:
    """Which of the day's 96 check-points a time falls on."""
    return (moment.hour * 60 + moment.minute) // SLOT_MINUTES


def detect_incidents(down_checks: list[tuple[str, datetime]], start_date: date) -> list[dict]:
    """Group down checks, ordered by service then time, into incidents."""
    runs: list[list[tuple[str, datetime]]] = []
    for check in down_checks:
        service_id, checked_at = check
        if runs:
            last_service, last_at = runs[-1][-1]
            # A missing slot counts as healthy: no data is not a failure (D3).
            if last_service == service_id and checked_at - last_at <= SLOT * (MAX_HEALTHY_GAP_SLOTS + 1):
                runs[-1].append(check)
                continue
        runs.append([check])

    incidents: list[dict] = []
    for run in runs:
        days: dict[int, list[datetime]] = {}
        for _, checked_at in run:
            days.setdefault((checked_at.date() - start_date).days, []).append(checked_at)
        for day_index, moments in sorted(days.items()):
            incidents.append(
                {
                    "service_id": run[0][0],
                    "day_index": day_index,
                    "checkpoint_start": _checkpoint(moments[0]),
                    "checkpoint_end": _checkpoint(moments[-1]),
                }
            )
    return incidents


def _record_outages(conn: psycopg.Connection, file_id: int, checks: list[dict]) -> None:
    # The checks are exactly what was stored, so they are scanned in memory
    # rather than read back from service_status_logs.
    start_date = checks[0]["checked_at"].date()
    days = (checks[-1]["checked_at"].date() - start_date).days + 1
    down_checks = sorted((c["service_id"], c["checked_at"]) for c in checks if c["outcome"] == "down")
    incidents = detect_incidents(down_checks, start_date)

    with conn.cursor() as cur:
        # The parent row first: test_outage_incidents points at it.
        cur.execute(
            "INSERT INTO test_outages (file_id, days, start_date) VALUES (%s, %s, %s)",
            (file_id, days, start_date),
        )
        cur.executemany(
            "INSERT INTO test_outage_incidents (file_id, service_id, day_index, checkpoint_start, checkpoint_end) "
            "VALUES (%s, %s, %s, %s, %s)",
            [(file_id, i["service_id"], i["day_index"], i["checkpoint_start"], i["checkpoint_end"]) for i in incidents],
        )
