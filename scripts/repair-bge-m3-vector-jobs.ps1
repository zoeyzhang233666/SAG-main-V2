param(
    [Parameter(Mandatory = $false)]
    [string]$SourceId = "bf5febf414be4b6786b0fb8e33d7dc77",

    [Parameter(Mandatory = $false)]
    [string]$ApiBase = "http://localhost:8100",

    [Parameter(Mandatory = $false)]
    [int]$ExpectedVectorDimensions = 1024
)

$ErrorActionPreference = "Stop"

function Wait-ApiReady {
    param([int]$TimeoutSeconds = 180)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            $ready = Invoke-RestMethod -Method Get -Uri "$ApiBase/api/v1/system/ready" -TimeoutSec 10
            if ($ready.status -eq "ready" -and $ready.db -eq $true) {
                return
            }
        }
        catch {
            # Keep waiting while the API starts.
        }
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $deadline)

    throw "API did not become ready within $TimeoutSeconds seconds."
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "docker command was not found."
}

Write-Host "Stopping API so failed vector jobs cannot continue..."
docker compose stop api
if ($LASTEXITCODE -ne 0) {
    throw "Failed to stop the api service."
}

try {
    Write-Host "Clearing embedding_dimensions for BAAI/bge-m3..."

    $clearDimensionsCode = @'
import json
import sqlite3

path = "/data/sag.db"
db = sqlite3.connect(path, timeout=30)
row = db.execute(
    "SELECT value_json FROM settings WHERE scope = 'global' AND key = 'model_config'"
).fetchone()

if row is None:
    raise SystemExit("model_config was not found")

config = json.loads(row[0] or "{}")
model = str(config.get("embedding_model") or "")
base_url = str(config.get("embedding_base_url") or "")

if model != "BAAI/bge-m3":
    raise SystemExit(f"Expected BAAI/bge-m3, found {model!r}")

config["embedding_dimensions"] = None

db.execute(
    "UPDATE settings SET value_json = ? WHERE scope = 'global' AND key = 'model_config'",
    (json.dumps(config, ensure_ascii=False),),
)
db.commit()
db.close()

print(json.dumps({
    "model": model,
    "base_url": base_url,
    "embedding_dimensions": None,
}, ensure_ascii=True))
'@

    $clearResult = $clearDimensionsCode |
        docker compose run --rm --no-deps -T api python -

    if ($LASTEXITCODE -ne 0) {
        throw "Failed to clear embedding_dimensions.`n$($clearResult -join "`n")"
    }

    $clearResult | Write-Host

    Write-Host "Testing the exact OpenAI-compatible embedding call without a dimensions parameter..."

    $probeCode = @'
import json
import sqlite3

from openai import OpenAI

expected = int(__import__("os").environ["EXPECTED_DIMENSIONS"])

db = sqlite3.connect("/data/sag.db")
row = db.execute(
    "SELECT value_json FROM settings WHERE scope = 'global' AND key = 'model_config'"
).fetchone()
db.close()

if row is None:
    raise SystemExit("model_config was not found")

config = json.loads(row[0] or "{}")
base_url = str(config.get("embedding_base_url") or "").rstrip("/")
api_key = str(config.get("embedding_api_key") or "")
model = str(config.get("embedding_model") or "")
dimensions = config.get("embedding_dimensions")

if dimensions is not None:
    raise SystemExit(f"embedding_dimensions must be null for {model}, found {dimensions!r}")
if not base_url or not api_key or not model:
    raise SystemExit("embedding configuration is incomplete")

client = OpenAI(
    api_key=api_key,
    base_url=base_url,
    timeout=90.0,
    max_retries=2,
)

response = client.embeddings.create(
    model=model,
    input=["ChemClaw bge-m3 repair probe"],
)
actual = len(response.data[0].embedding)

print(json.dumps({
    "ok": actual == expected,
    "model": model,
    "request_dimensions_parameter": "omitted",
    "expected_dimensions": expected,
    "actual_dimensions": actual,
}, ensure_ascii=True))

if actual != expected:
    raise SystemExit(2)
'@

    $probeResult = $probeCode |
        docker compose run --rm --no-deps -T `
          -e "EXPECTED_DIMENSIONS=$ExpectedVectorDimensions" `
          api python -

    if ($LASTEXITCODE -ne 0) {
        throw "Embedding probe without dimensions failed.`n$($probeResult -join "`n")"
    }

    $probeResult | Write-Host

    Write-Host "Re-queuing only the newest unfinished vector job for each document..."

    $resetJobsCode = @'
import json
import os
import sqlite3

source_id = os.environ["SOURCE_ID"]
db = sqlite3.connect("/data/sag.db", timeout=30)
db.row_factory = sqlite3.Row

rows = db.execute(
    """
    SELECT id, status, progress, attempts, payload_json, error, created_at
    FROM jobs
    WHERE source_id = ?
      AND type = 'STRUCTURED_INDEX'
    ORDER BY created_at, id
    """,
    (source_id,),
).fetchall()

by_article = {}
parsed = []

for row in rows:
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except Exception:
        payload = {}

    item = {
        "id": row["id"],
        "status": row["status"],
        "payload": payload,
        "created_at": row["created_at"],
        "error": row["error"] or "",
    }
    parsed.append(item)

    for article_id in payload.get("article_ids") or []:
        article_id = str(article_id)
        current = by_article.get(article_id)
        if current is None or (item["created_at"], item["id"]) > (
            current["created_at"], current["id"]
        ):
            by_article[article_id] = item

if len(by_article) != 36:
    raise SystemExit(f"Expected current index coverage for 36 documents, found {len(by_article)}")

current_ids = {item["id"] for item in by_article.values()}
superseded_active = 0

for item in parsed:
    if item["id"] in current_ids:
        continue
    if item["status"] not in {"QUEUED", "RUNNING"}:
        continue
    db.execute(
        """
        UPDATE jobs
        SET status = 'FAILED',
            error = 'Superseded by the newest document vector-index job',
            finished_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (item["id"],),
    )
    superseded_active += 1

reset = []
succeeded = []
unexpected_failed = []

for article_id, item in by_article.items():
    status = item["status"]
    payload = dict(item["payload"])
    filename = str(payload.get("filename") or "")

    if status == "SUCCEEDED":
        succeeded.append(filename)
        continue

    if status == "FAILED":
        error_text = item["error"].lower()
        expected_error = (
            "20015" in error_text
            or "parameter is invalid" in error_text
            or "embedding" in error_text
        )
        if not expected_error:
            unexpected_failed.append({
                "filename": filename,
                "error": item["error"],
            })
            continue

    payload["stage"] = "queued"
    db.execute(
        """
        UPDATE jobs
        SET status = 'QUEUED',
            progress = 0,
            attempts = 0,
            payload_json = ?,
            error = NULL,
            started_at = NULL,
            finished_at = NULL
        WHERE id = ?
        """,
        (json.dumps(payload, ensure_ascii=False), item["id"]),
    )
    reset.append(filename)

if unexpected_failed:
    db.rollback()
    print(json.dumps({
        "ok": False,
        "unexpected_failed_jobs": unexpected_failed,
    }, ensure_ascii=True))
    raise SystemExit(3)

db.commit()
db.close()

print(json.dumps({
    "ok": True,
    "coverage": len(by_article),
    "already_succeeded": len(succeeded),
    "jobs_requeued": len(reset),
    "superseded_active_jobs_cancelled": superseded_active,
}, ensure_ascii=True))
'@

    $resetResult = $resetJobsCode |
        docker compose run --rm --no-deps -T `
          -e "SOURCE_ID=$SourceId" `
          api python -

    if ($LASTEXITCODE -ne 0) {
        throw "Failed to reset vector jobs.`n$($resetResult -join "`n")"
    }

    $resetResult | Write-Host

    Write-Host "Starting API. Re-queued jobs will resume automatically..."
    docker compose up -d api
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to start the api service."
    }

    Wait-ApiReady

    Write-Host "Repair completed. Vector jobs are running again." -ForegroundColor Green
    Write-Host "Monitor with:"
    Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\monitor-structured-vectors.ps1 -SourceId `"$SourceId`""
}
catch {
    Write-Warning "Repair did not complete: $($_.Exception.Message)"
    Write-Host "Starting API so ChemClaw remains available..."
    docker compose up -d api | Out-Null
    throw
}
