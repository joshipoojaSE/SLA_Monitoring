# Upload processor (AWS Lambda)

`lambda_function.py` does the heavy part of a CSV upload. It cleans the rows,
saves the checks and detects the outages. The API only does the quick checks
and hands the file over through S3.

## How it fits together

```
Browser ──POST /uploads──▶ API (BE/app/main.py)
                             1. file present, .csv, ≤ 10 MB, not empty, UTF-8
                             2. header has every required column, ≥ 1 data line
                                └─ fails → 422, nothing stored
                             3. INSERT uploaded_files (status = 'processing') → file_id
                             4. PUT s3://<bucket>/<file_id>/<file name>
                             5. 202 {file_id, status: 'processing'}
                                          │
                                          ▼  S3 "ObjectCreated" event (*.csv)
                           Lambda (AWS/lambda_function.py)
                             6. download the CSV, split into rows
                             7. clean each row, keep one check per service per slot
                             8. upsert regions / agents / services
                             9. COPY checks into service_status_logs
                            10. detect incidents → test_outages, test_outage_incidents
                            11. uploaded_files.status = 'done'
                                (or 'failed' + error_message; everything from 8 on is rolled back)

Browser ──GET /uploads/{file_id}──▶ poll until status is 'done' or 'failed'
```

A few points to know:

- **No new tables.** The Lambda writes to the same Neon tables the API always used.
- **The Lambda only processes each upload once.** It works only on an upload whose status is still `processing`. If S3 sends the same event twice, the second one is skipped.
- **Where to see results.** Row-level details (rejected rows, duplicates, warnings) go to the Lambda's CloudWatch log. They are no longer returned to the browser.
- **The S3 key carries the file_id.** The API writes to `<file_id>/<file name>`. The Lambda reads the id from the folder name and ignores any key not under a numeric folder.

## Before you start

| You need | Notes |
|---|---|
| AWS CLI v2, logged in | `aws sts get-caller-identity` should print your account |
| Python 3.12 + pip on your machine | Only used to download the Linux packages |
| The S3 bucket the API already uses | The `S3_BUCKET` value in `BE/.env` |
| The Neon connection string | The `DATABASE_URL` value in `BE/.env` |

The commands below are for **PowerShell**, run from this `AWS` folder. Set these
once per terminal and change the values to yours:

```powershell
$REGION   = "us-east-2"                  # same region as the bucket
$BUCKET   = "your-bucket-name"           # S3_BUCKET from BE/.env
$FUNCTION = "sla-upload-processor"
$ROLE     = "sla-upload-processor-role"
$ACCOUNT  = aws sts get-caller-identity --query Account --output text
```

## First-time setup

### 1. Create the IAM role the Lambda runs as

The role lets the Lambda write logs and read the uploaded files from the bucket.

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
    "Action": "s3:GetObject",
    "Resource": "arn:aws:s3:::$BUCKET/*"
  }]
}
"@ | Set-Content -Encoding ascii s3-read-policy.json

aws iam put-role-policy --role-name $ROLE --policy-name read-uploads `
  --policy-document file://s3-read-policy.json
```

### 2. Build the deployment package

Lambda runs on Linux, so `psycopg` has to be the Linux build. Don't use the
Windows one that `pip install` gives you by default. `boto3` is already part of
the Lambda runtime, so it isn't included.

```powershell
Remove-Item -Recurse -Force build, function.zip -ErrorAction SilentlyContinue

pip install --target build --platform manylinux2014_x86_64 --implementation cp `
  --python-version 3.12 --only-binary=:all: "psycopg[binary]==3.3.6"

Copy-Item lambda_function.py build\
Compress-Archive -Path build\* -DestinationPath function.zip
```

`function.zip` should have `lambda_function.py` and the `psycopg` folders at
its **top level**, not inside a `build` folder.

### 3. Create the function

The connection string goes in a small JSON file. Passing it inline breaks on
the `=`, `?` and `&` characters in it.

```powershell
@'
{ "Variables": { "DATABASE_URL": "postgresql://...paste from BE/.env..." } }
'@ | Set-Content -Encoding ascii env.json

# The role takes a few seconds to become usable after step 1.
Start-Sleep -Seconds 10

aws lambda create-function --region $REGION `
  --function-name $FUNCTION `
  --runtime python3.12 `
  --handler lambda_function.handler `
  --role arn:aws:iam::${ACCOUNT}:role/$ROLE `
  --zip-file fileb://function.zip `
  --timeout 300 `
  --memory-size 1024 `
  --environment file://env.json

Remove-Item env.json    # it holds the database password; don't commit it
```

- **Timeout:** the 30-day sample file (14,400 checks) finishes in seconds, so 300 s leaves plenty of room.
- **Memory:** Lambda gives more CPU with more memory, which speeds up the cleaning.
- **No VPC needed:** Neon is reached over the public internet, and a Lambda outside a VPC can do that by default.

### 4. Let S3 invoke the function

```powershell
aws lambda add-permission --region $REGION `
  --function-name $FUNCTION `
  --statement-id s3-invoke `
  --action lambda:InvokeFunction `
  --principal s3.amazonaws.com `
  --source-arn arn:aws:s3:::$BUCKET `
  --source-account $ACCOUNT
```

### 5. Add the S3 trigger

> ⚠️ `put-bucket-notification-configuration` **replaces** all of the bucket's
> existing event notifications. If the bucket already has some, first run
> `aws s3api get-bucket-notification-configuration --bucket $BUCKET` and add
> this one to them.

```powershell
@"
{
  "LambdaFunctionConfigurations": [{
    "Id": "process-csv-uploads",
    "LambdaFunctionArn": "arn:aws:lambda:${REGION}:${ACCOUNT}:function:$FUNCTION",
    "Events": ["s3:ObjectCreated:*"],
    "Filter": { "Key": { "FilterRules": [{ "Name": "suffix", "Value": ".csv" }] } }
  }]
}
"@ | Set-Content -Encoding ascii notification.json

aws s3api put-bucket-notification-configuration --bucket $BUCKET `
  --notification-configuration file://notification.json
```

Setup is done. Every CSV the API puts in the bucket now runs the Lambda.

## Updating the code later

After changing `lambda_function.py`, rebuild (step 2) and push the new zip:

```powershell
aws lambda update-function-code --region $REGION `
  --function-name $FUNCTION --zip-file fileb://function.zip
```

To change the connection string, write `env.json` as in step 3, then run
`aws lambda update-function-configuration --region $REGION --function-name $FUNCTION --environment file://env.json`.

## Checking that it works

1. Start the API (`BE/`) and upload a sample file, e.g. `monitoring_checks_9d_seed101.csv`,
   through `http://localhost:8000/docs` → `POST /uploads`. You should get
   `202 {"file_id": N, "status": "processing"}`.
2. Watch the Lambda run:

   ```powershell
   aws logs tail /aws/lambda/$FUNCTION --region $REGION --follow
   ```

   A good run ends with a line like
   `File N done: 4320 rows, 4320 checks saved, 0 ragged line(s), ...`.
3. Call `GET /uploads/N`. `status` should be `done`, and the file now shows up
   in `GET /files` and on the dashboard.

## When something goes wrong

| What you see | Likely cause |
|---|---|
| Status stays `processing`, and the log group is empty | The trigger isn't set up. Check step 5, and that the S3 key ends in `.csv` |
| `Runtime.ImportModuleError: No module named 'psycopg'` | The Windows build was zipped, or the files sit inside a `build/` folder in the zip. Redo step 2 |
| `KeyError: 'DATABASE_URL'` | The environment variable is missing. Redo the `env.json` part of step 3 |
| `AccessDenied` on `GetObject` | The role's `read-uploads` policy names a different bucket (step 1) |
| Status `failed`, `error_message` says no row could be read | The data itself is bad. The log lists example rejected rows |
| `Task timed out` | Raise `--timeout` with `update-function-configuration` |

The Lambda marks an upload `failed` before it raises an error, so a failed
upload never gets stuck at `processing`. To process a file again, upload it
again through the API. That creates a new `file_id`.
