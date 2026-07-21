param(
    [Parameter(Mandatory = $false)]
    [string]$SourceId = "bf5febf414be4b6786b0fb8e33d7dc77",

    [Parameter(Mandatory = $false)]
    [string]$ApiBase = "http://localhost:8100",

    [Parameter(Mandatory = $false)]
    [string]$UserName = "EDY",

    [Parameter(Mandatory = $false)]
    [int]$EmbeddingDimensions = 1024,

    [Parameter(Mandatory = $false)]
    [ValidateRange(1, 20)]
    [int]$ProbeAttempts = 6,

    [switch]$SkipBackup,
    [switch]$SkipEmbeddingProbe
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
            # Keep waiting while the container starts.
        }
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $deadline)

    throw "API did not become ready within $TimeoutSeconds seconds."
}

function Login-ChemClaw {
    $body = @{ name = $UserName } | ConvertTo-Json
    $auth = Invoke-RestMethod `
        -Method Post `
        -Uri "$ApiBase/api/v1/auth/login" `
        -ContentType "application/json; charset=utf-8" `
        -Body ([Text.Encoding]::UTF8.GetBytes($body))

    if ([string]::IsNullOrWhiteSpace([string]$auth.access_token)) {
        throw "Login succeeded without an access token."
    }
    return [string]$auth.access_token
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "docker command was not found."
}

Write-Host "Starting ChemClaw services..."
docker compose up -d api
if ($LASTEXITCODE -ne 0) {
    throw "docker compose up failed."
}
Wait-ApiReady

$token = Login-ChemClaw
$headers = @{ Authorization = "Bearer $token" }

Write-Host "Setting embedding dimensions to $EmbeddingDimensions..."
$configBody = @{ embedding_dimensions = $EmbeddingDimensions } | ConvertTo-Json
$configResult = Invoke-RestMethod `
    -Method Put `
    -Uri "$ApiBase/api/v1/system/model-config" `
    -Headers $headers `
    -ContentType "application/json; charset=utf-8" `
    -Body ([Text.Encoding]::UTF8.GetBytes($configBody))

if ([int]$configResult.config.embedding_dimensions -ne $EmbeddingDimensions) {
    throw "Embedding dimensions were not saved correctly."
}
Write-Host "Embedding dimensions saved: $($configResult.config.embedding_dimensions)" -ForegroundColor Green

if (-not $SkipEmbeddingProbe) {
    Write-Host "Testing the configured embedding endpoint (up to $ProbeAttempts attempts)..."
    $probeCode = @'
import json
import os
import socket
import sqlite3
import time
import httpx

connection = sqlite3.connect("/data/sag.db")
row = connection.execute(
    "SELECT value_json FROM settings WHERE scope = 'global' AND key = 'model_config'"
).fetchone()
connection.close()

if not row:
    raise SystemExit("model_config was not found")

config = json.loads(row[0] or "{}")
base_url = str(config.get("embedding_base_url") or "").rstrip("/")
api_key = str(config.get("embedding_api_key") or "")
model = str(config.get("embedding_model") or "")
expected = int(config.get("embedding_dimensions") or 0)
attempts = max(1, int(os.environ.get("PROBE_ATTEMPTS", "6")))

if not base_url or not api_key or not model:
    raise SystemExit("embedding configuration is incomplete")

host = base_url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
last_error = None

for attempt in range(1, attempts + 1):
    try:
        transport = httpx.HTTPTransport(retries=2)
        timeout = httpx.Timeout(90.0, connect=30.0)
        with httpx.Client(
            transport=transport,
            timeout=timeout,
            follow_redirects=True,
            trust_env=True,
        ) as client:
            response = client.post(
                base_url + "/embeddings",
                headers={"Authorization": "Bearer " + api_key},
                json={"model": model, "input": ["ChemClaw embedding health check"]},
            )

        if response.status_code == 429 or response.status_code >= 500:
            raise RuntimeError(
                f"temporary HTTP {response.status_code}: {response.text[:500]}"
            )

        if 400 <= response.status_code < 500:
            print(json.dumps({
                "ok": False,
                "attempt": attempt,
                "http_status": response.status_code,
                "response": response.text[:1000],
            }, ensure_ascii=True))
            raise SystemExit(3)

        response.raise_for_status()
        payload = response.json()
        vector = payload["data"][0]["embedding"]
        actual = len(vector)

        print(json.dumps({
            "ok": actual == expected,
            "attempt": attempt,
            "model": model,
            "expected_dimensions": expected,
            "actual_dimensions": actual,
        }, ensure_ascii=True))

        if actual != expected:
            raise SystemExit(2)
        raise SystemExit(0)
    except SystemExit:
        raise
    except Exception as error:
        last_error = f"{type(error).__name__}: {error}"
        if attempt < attempts:
            wait_seconds = min(20, 2 ** attempt)
            print(
                f"probe attempt {attempt}/{attempts} failed: {last_error}; "
                f"retrying in {wait_seconds}s",
                flush=True,
            )
            time.sleep(wait_seconds)

try:
    resolved = sorted({item[4][0] for item in socket.getaddrinfo(host, 443)})
except Exception as error:
    resolved = [f"DNS error: {type(error).__name__}: {error}"]

print(json.dumps({
    "ok": False,
    "attempts": attempts,
    "base_url": base_url,
    "host": host,
    "resolved_addresses": resolved,
    "proxy_environment": {
        key: value
        for key, value in os.environ.items()
        if key.lower() in {
            "http_proxy", "https_proxy", "all_proxy", "no_proxy"
        }
    },
    "last_error": last_error,
}, ensure_ascii=True))
raise SystemExit(1)
'@

    $probeOutput = $probeCode |
        docker compose exec -T `
          -e "PROBE_ATTEMPTS=$ProbeAttempts" `
          api python -

    if ($LASTEXITCODE -ne 0) {
        throw "Embedding endpoint test failed after $ProbeAttempts attempts.`n$($probeOutput -join "`n")"
    }

    $probeJson = $probeOutput |
        Where-Object { $_ -match '^\s*\{' } |
        Select-Object -Last 1

    if ([string]::IsNullOrWhiteSpace([string]$probeJson)) {
        throw "Embedding endpoint test returned no JSON result.`n$($probeOutput -join "`n")"
    }

    $probe = $probeJson | ConvertFrom-Json
    $probe | Format-List
}

$source = Invoke-RestMethod `
    -Method Get `
    -Uri "$ApiBase/api/v1/sources/$SourceId" `
    -Headers $headers

if (
    [int]$source.document_count -ne 36 -or
    [int]$source.chunk_count -ne 41314 -or
    [int]$source.event_count -ne 41314
) {
    throw "Source counts are not the validated migration baseline (36 / 41314 / 41314)."
}

if (-not $SkipBackup) {
    Write-Host "Creating a backup before vector indexing..."
    npm run db:backup
    if ($LASTEXITCODE -ne 0) {
        throw "Database backup failed."
    }
    Wait-ApiReady
}

Write-Host "Stopping API before creating persistent index jobs..."
docker compose stop api
if ($LASTEXITCODE -ne 0) {
    throw "Failed to stop the API container."
}

$createJobsCode = @'
import asyncio
import json
import os

from sqlalchemy import select

from sag_api.core.config import settings
from sag_api.core.db import SessionLocal, dispose_db
from sag_api.db.models import Document, Job, Source
from sag_api.enums import JobStatus, JobType
from sag_api.sag import EngineManager
from sag_api.services.settings_service import apply_startup_overrides


async def main():
    source_id = os.environ["SOURCE_ID"]
    await apply_startup_overrides(SessionLocal)
    manager = EngineManager(settings)

    try:
        async with SessionLocal() as session:
            source = await session.get(Source, source_id)
            if source is None:
                raise RuntimeError("source not found")

            documents = list((await session.execute(
                select(Document)
                .where(Document.source_id == source_id)
                .order_by(Document.created_at, Document.id)
            )).scalars())

            if len(documents) != 36:
                raise RuntimeError(f"expected 36 documents, found {len(documents)}")

            await manager.provision(source.sag_source_config_id, source)

            from zleap.sag.db import get_session_factory
            from zleap.sag.db.models import SourceChunk, SourceEvent

            factory = get_session_factory()
            async with factory() as engine_session:
                existing_jobs = list((await session.execute(
                    select(Job).where(
                        Job.source_id == source_id,
                        Job.type == JobType.STRUCTURED_INDEX,
                        Job.status.in_([
                            JobStatus.QUEUED,
                            JobStatus.RUNNING,
                            JobStatus.SUCCEEDED,
                        ]),
                    )
                )).scalars())

                already_planned = set()
                for job in existing_jobs:
                    for article_id in (job.payload or {}).get("article_ids") or []:
                        already_planned.add(str(article_id))

                created = []
                skipped = []
                for document in documents:
                    article_id = str(document.sag_source_id or "")
                    if not article_id:
                        raise RuntimeError(f"document {document.id} has no sag_source_id")

                    if article_id in already_planned:
                        skipped.append({
                            "filename": document.filename,
                            "article_id": article_id,
                            "reason": "existing queued/running/succeeded index job",
                        })
                        continue

                    chunk_ids = [str(value) for value in (await engine_session.execute(
                        select(SourceChunk.id)
                        .where(SourceChunk.article_id == article_id)
                        .order_by(SourceChunk.rank, SourceChunk.id)
                    )).scalars()]

                    event_ids = [str(value) for value in (await engine_session.execute(
                        select(SourceEvent.id)
                        .where(
                            SourceEvent.article_id == article_id,
                            (SourceEvent.status.is_(None)) | (SourceEvent.status != "DELETED"),
                        )
                        .order_by(SourceEvent.rank, SourceEvent.id)
                    )).scalars()]

                    if len(chunk_ids) != int(document.chunk_count or 0):
                        raise RuntimeError(
                            f"chunk count mismatch for {document.filename}: "
                            f"document={document.chunk_count}, engine={len(chunk_ids)}"
                        )
                    if len(event_ids) != int(document.event_count or 0):
                        raise RuntimeError(
                            f"event count mismatch for {document.filename}: "
                            f"document={document.event_count}, engine={len(event_ids)}"
                        )

                    job = Job(
                        type=JobType.STRUCTURED_INDEX,
                        status=JobStatus.QUEUED,
                        source_id=source_id,
                        document_id=document.id,
                        progress=0.0,
                        payload={
                            "stage": "queued",
                            "filename": document.filename,
                            "article_ids": [article_id],
                            "event_ids": event_ids,
                            "chunk_ids": chunk_ids,
                            "counts": {
                                "chunks": len(chunk_ids),
                                "events": len(event_ids),
                            },
                        },
                    )
                    session.add(job)
                    await session.flush()
                    created.append({
                        "job_id": job.id,
                        "filename": document.filename,
                        "chunks": len(chunk_ids),
                        "events": len(event_ids),
                    })

                await session.commit()

            print(json.dumps({
                "source_id": source_id,
                "documents": len(documents),
                "jobs_created": len(created),
                "jobs_skipped": len(skipped),
                "created": created,
                "skipped": skipped,
            }, ensure_ascii=True))
    finally:
        await manager.aclose_all()
        await dispose_db()


asyncio.run(main())
'@

$jobOutput = $createJobsCode |
    docker compose run --rm --no-deps -T `
      -e "SOURCE_ID=$SourceId" `
      api python -

if ($LASTEXITCODE -ne 0) {
    docker compose up -d api | Out-Null
    throw "Failed to create vector index jobs.`n$($jobOutput -join "`n")"
}

$jobPlan = ($jobOutput -join "`n") | ConvertFrom-Json
Write-Host "Index jobs created: $($jobPlan.jobs_created)"
Write-Host "Index jobs skipped: $($jobPlan.jobs_skipped)"

Write-Host "Starting API. Queued jobs will be recovered automatically..."
docker compose up -d api
if ($LASTEXITCODE -ne 0) {
    throw "Failed to restart the API container."
}
Wait-ApiReady

Write-Host ""
Write-Host "Vector indexing is now running in the persistent job queue." -ForegroundColor Green
Write-Host "It is safe to close this PowerShell window. If Windows restarts, run 'docker compose up -d'; unfinished jobs will resume."
Write-Host ""
Write-Host "Monitor with:"
Write-Host '  docker compose logs -f api'
