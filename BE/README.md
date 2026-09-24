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

Copy `.env.example` to `.env` and set `DATABASE_URL`: the Neon connection string, pooled endpoint, `sslmode=require` (the libpq spelling, not `ssl=require`). Set `CORS_ORIGINS` to the dashboard's origin(s), comma-separated; it defaults to the dashboard's dev server, `http://localhost:5174`.

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
| `POST /uploads` | Checks the CSV (type, ≤ 4 MB, header), stores it in S3, returns `202 {file_id, status: "processing"}`; `422` if the file is rejected |
| `GET /uploads/{file_id}` | The upload's status: `processing`, `done` or `failed` (with `error_message`) |
| `GET /files` | Uploads that finished (`done`), newest first |
| `GET /files/{file_id}/stats` | Headline figures for one file |
| `GET /files/{file_id}/stats/services` | Per-service figures, sorted and paged |
| `GET /files/{file_id}/stats/incidents` | Incidents, sorted and paged |
| `GET /files/{file_id}/logs` | The stored checks, filtered by date (one day or a range), service and outcome; sorted and paged |

## Deploy to AWS Lambda

The live API runs as an AWS Lambda with a public **Function URL**. The same
`app` that uvicorn runs locally is wrapped by [Mangum](https://mangum.fastapiexpert.com/)
(`handler = Mangum(app)` at the end of `app/main.py`), so there is no second
copy of the code.

Why Lambda: it's free at this volume (1 million requests a month), it doesn't
sleep the way free web hosts do (a cold start is about 1–2 s, not 30–60 s),
and it sits in the same AWS account and region as the S3 bucket and the
upload processor ([AWS/README.md](../AWS/README.md)).

**Limit:** a Lambda request can be at most 6 MB, and the Function URL sends
the uploaded file base64-encoded (a third bigger). That is why
`MAX_UPLOAD_BYTES` is 4 MB. The largest sample file is 1.2 MB.

### Before you start

| You need | Notes |
|---|---|
| AWS CLI v2, logged in | `aws sts get-caller-identity` should print your account |
| Python 3.12 + pip | Only used to download the Linux packages |
| The S3 bucket and region | The bucket the upload processor is triggered by. It must be the same region as the function |
| The Neon connection string | The `DATABASE_URL` value in `BE/.env` (pooled host, `sslmode=require`) |
| The tables exist | Run `alembic upgrade head` once from your machine; the Lambda never changes tables |

The commands are for **PowerShell**, run from this `BE` folder. Set these once
per terminal and change the values to yours:

```powershell
$REGION   = "us-east-1"                  # same region as the bucket
$BUCKET   = "sla-monitor-store"          # S3_BUCKET from BE/.env
$FUNCTION = "sla-api"
$ROLE     = "sla-api-role"
$ACCOUNT  = aws sts get-caller-identity --query Account --output text
```

### 1. Create the IAM role the API runs as

The role lets the function write logs and put uploaded files in the bucket.
It can't read or delete them.

```powershell
@'
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "lambda.amazonaws.com" },
    "Action": "sts:AssumeRole"
  }]
}
'@ | Set-Content -Encoding ascii trust-policy.json

aws iam create-role --role-name $ROLE --assume-role-policy-document file://trust-policy.json

aws iam attach-role-policy --role-name $ROLE `
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

@"
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "s3:PutObject",
    "Resource": "arn:aws:s3:::$BUCKET/*"
  }]
}
"@ | Set-Content -Encoding ascii s3-write-policy.json

aws iam put-role-policy --role-name $ROLE --policy-name write-uploads `
  --policy-document file://s3-write-policy.json

Remove-Item trust-policy.json, s3-write-policy.json
```

### 2. Build the deployment package

Lambda runs on Linux, so `psycopg` and `pydantic-core` must be the Linux
builds, not the Windows ones `pip install` gives you by default. Only what the
API needs at run time is packaged: `uvicorn` and `alembic` are for local use,
and `boto3` is already part of the Lambda runtime. Keep the versions in step
with `requirements.txt`.

```powershell
Remove-Item -Recurse -Force build, api.zip -ErrorAction SilentlyContinue

pip install --target build --platform manylinux2014_x86_64 --implementation cp `
  --python-version 3.12 --only-binary=:all: `
  fastapi==0.141.1 "psycopg[binary]==3.3.6" python-dotenv==1.2.3 `
  sqlalchemy==2.0.54 python-multipart==0.0.20 mangum==0.19.0

Copy-Item -Recurse app build\app
python -c "import shutil; shutil.make_archive('api', 'zip', 'build')"
```

After this, `build\` should hold `app\` (with `__init__.py`, `main.py`,
`database.py`, `models.py`) next to the package folders: `fastapi`,
`starlette`, `pydantic`, `pydantic_core`, `mangum`, `sqlalchemy`, `greenlet`,
`psycopg`, `psycopg_binary`, `multipart`, `python_multipart`, `dotenv`,
`anyio`, plus `typing_extensions.py`.

`api.zip` should have the `app` folder and the packages at its **top level**,
not inside a `build` folder. `build/` and `api.zip` must not be committed.

The zip is made with Python, not `Compress-Archive`: Windows PowerShell's
`Compress-Archive` can write paths as `app\main.py`, which Linux reads as one
file named that, not `main.py` inside `app`. The Lambda then fails with
`No module named 'app'`.

### 3. Create the function

The settings go in a small JSON file, because the `=`, `?` and `&` in the
connection string break when passed inline. `AWS_REGION` is set by Lambda
itself, and the S3 credentials come from the role, so neither goes here.

```powershell
@'
{
  "Variables": {
    "DATABASE_URL": "postgresql://...paste from BE/.env...",
    "S3_BUCKET": "sla-monitor-store",
    "CORS_ORIGINS": "http://localhost:5174"
  }
}
'@ | Set-Content -Encoding ascii env.json

# The role takes a few seconds to become usable after step 1.
Start-Sleep -Seconds 10

aws lambda create-function --region $REGION `
  --function-name $FUNCTION `
  --runtime python3.12 `
  --handler app.main.handler `
  --role arn:aws:iam::${ACCOUNT}:role/$ROLE `
  --zip-file fileb://api.zip `
  --timeout 30 `
  --memory-size 512 `
  --environment file://env.json

Remove-Item env.json    # it holds the database password; don't commit it
```

- **Handler** `app.main.handler`: the `handler` object in `app/main.py`.
- **Timeout 30 s:** covers Neon waking a paused database (a few seconds) plus the slowest query.
- **No VPC:** Neon and S3 are reached over the internet, which a Lambda outside a VPC can do by default.

### 4. Give it a public URL

```powershell
aws lambda create-function-url-config --region $REGION `
  --function-name $FUNCTION --auth-type NONE

aws lambda add-permission --region $REGION `
  --function-name $FUNCTION `
  --statement-id public-url `
  --action lambda:InvokeFunctionUrl `
  --principal "*" `
  --function-url-auth-type NONE

aws lambda add-permission --region $REGION `
  --function-name $FUNCTION `
  --statement-id public-url-invoke `
  --action lambda:InvokeFunction `
  --principal "*" `
  --invoked-via-function-url
```

The first command prints `FunctionUrl`, e.g.
`https://abc123.lambda-url.us-east-1.on.aws/`. That is the API's live URL.
Set it as `VITE_API_URL` for the frontend (without the trailing `/`).

**Don't** add CORS settings to the Function URL. FastAPI already sends the
CORS headers from `CORS_ORIGINS`; setting them in both places sends each
header twice, and browsers then block the request.

### 5. Check that it works

Open these in a browser:

| URL | Expect |
|---|---|
| `<FunctionUrl>/health` | `{"status":"ok"}` |
| `<FunctionUrl>/health/db` | `{"status":"ok"}` (the first call may take a few seconds while Neon wakes) |
| `<FunctionUrl>/files` | The uploads already processed |
| `<FunctionUrl>/docs` | The interactive docs. Try `POST /uploads` with a sample CSV, then `GET /uploads/{file_id}` until `status` is `done` |

Logs: `aws logs tail /aws/lambda/$FUNCTION --region $REGION --follow`.

### Updating later

After changing code under `app/`, rebuild (step 2) and push the new zip:

```powershell
aws lambda update-function-code --region $REGION `
  --function-name $FUNCTION --zip-file fileb://api.zip
```

Once the dashboard has its live URL, add it to `CORS_ORIGINS` (comma-separated,
no trailing `/`): write `env.json` as in step 3 with **all three** variables,
then run

```powershell
aws lambda update-function-configuration --region $REGION `
  --function-name $FUNCTION --environment file://env.json
Remove-Item env.json
```

`--environment` replaces every variable, so leaving one out removes it.

### When something goes wrong

| What you see | Likely cause |
|---|---|
| `Runtime.ImportModuleError: No module named 'pydantic_core'` (or `psycopg`) | The Windows build was zipped, or the files sit inside a `build/` folder in the zip. Redo step 2 |
| `Runtime.ImportModuleError: No module named 'app'` | `app` isn't at the top of the zip, or its paths use `\`. Build the zip with the Python command in step 2, not `Compress-Archive`, then run `update-function-code` |
| `RuntimeError: DATABASE_URL is not set` | The variable is missing. Redo the `env.json` part of step 3 |
| `403 Forbidden` from the URL | The permissions in step 4 are missing |
| Browser console: `blocked by CORS policy` | The page's origin isn't in `CORS_ORIGINS`, or CORS was also set on the Function URL |
| Upload returns `503`, log says `AccessDenied` on `PutObject` | The role's `write-uploads` policy names a different bucket (step 1) |
| Upload stays `processing` | The API side worked; see [AWS/README.md](../AWS/README.md) for the upload processor |
| `413` or a request-size error on upload | The file is over the Lambda request limit; files must be ≤ 4 MB |

## Dependencies

| Package | Purpose |
|---|---|
| `fastapi` | Web framework |
| `uvicorn[standard]` | ASGI server |
| `psycopg[binary]` | PostgreSQL driver (psycopg 3) |
| `python-dotenv` | Loads `.env` |
| `sqlalchemy` | ORM and table definitions |
| `alembic` | Database migrations |
| `python-multipart` | Reads the uploaded file in `POST /uploads` |
| `boto3` | Stores uploads in S3 |
| `mangum` | Runs the app on AWS Lambda |
