# Backend (FastAPI)

API for the SLA Monitoring Dashboard. Reads cleaned check data from Neon PostgreSQL.

## Requirements

- Python 3.12+

## Setup

```bash
cd BE
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set `DATABASE_URL`: the Neon connection string, pooled endpoint, `sslmode=require` (the libpq spelling, not `ssl=require`). Set `CORS_ORIGINS` to the dashboard's origin(s), comma-separated; it defaults to the Vite dev server, `http://localhost:5173`.

## Database

Design: [database.md](database.md).

Tables are defined as SQLAlchemy 2.0 models; schema changes are applied with Alembic migrations. The app never creates or changes tables on start.

| File | Purpose |
|---|---|
| `app/database.py` | Engine (from `DATABASE_URL` in `.env`, using the psycopg 3 driver) and the declarative `Base` |
| `app/models.py` | The 7 tables, with their keys, constraints and indexes |
| `alembic.ini`, `alembic/env.py` | Alembic config; the database URL comes from `.env` |
| `alembic/versions/` | Migrations, applied in order |

All commands run from `BE`, with the venv active.

Apply all pending migrations (creates the tables on a new database):

```bash
alembic upgrade head
```

Show the current revision, or the migration history:

```bash
alembic current
alembic history
```

After changing `app/models.py`, generate a migration, review it, then apply it:

```bash
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```

Undo the last migration:

```bash
alembic downgrade -1
```

**Database created before Alembic was added:** if the 7 tables already exist but `alembic_version` does not, `alembic upgrade head` fails because the tables exist. Either drop the 7 tables and run `alembic upgrade head` (deletes their data), or keep them and mark them as already migrated:

```bash
alembic stamp head
```

## Run

From `BE`, with the venv active:

```bash
uvicorn app.main:app --reload --port 8001
```

Drop `--reload` when running anywhere other than a development machine.

Interactive docs: http://127.0.0.1:8001/docs

| Endpoint | Returns |
|---|---|
| `GET /health` | `200 {"status": "ok"}` when the API is up |
| `GET /health/db` | `200 {"status": "ok"}` when the database answers `SELECT 1`; `503` otherwise |

## Dependencies

| Package | Purpose |
|---|---|
| `fastapi` | Web framework |
| `uvicorn[standard]` | ASGI server |
| `psycopg[binary]` | PostgreSQL driver (psycopg 3) |
| `python-dotenv` | Loads `.env` |
| `sqlalchemy` | ORM and table definitions |
| `alembic` | Database migrations |
