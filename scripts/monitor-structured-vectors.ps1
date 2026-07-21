param(
    [Parameter(Mandatory = $false)]
    [string]$SourceId = "bf5febf414be4b6786b0fb8e33d7dc77",

    [Parameter(Mandatory = $false)]
    [ValidateRange(5, 300)]
    [int]$IntervalSeconds = 15,

    [Parameter(Mandatory = $false)]
    [ValidateRange(1, 1000)]
    [int]$ExpectedDocuments = 36
)

$ErrorActionPreference = "Stop"

$statusCode = @'
import json
import os
import sqlite3

source_id = os.environ["SOURCE_ID"]
db = sqlite3.connect("/data/sag.db")
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

# Keep only the newest index job for each article/document. This ignores
# intentionally cancelled historical jobs once a newer retry exists.
by_article = {}
without_article = []

for row in rows:
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except Exception:
        payload = {}

    item = {
        "id": row["id"],
        "status": row["status"],
        "percent": round(float(row["progress"] or 0) * 100, 1),
        "stage": payload.get("stage", ""),
        "filename": payload.get("filename", ""),
        "chunks": (payload.get("counts") or {}).get("chunks", 0),
        "events": (payload.get("counts") or {}).get("events", 0),
        "attempts": row["attempts"],
        "error": row["error"] or "",
        "created_at": row["created_at"],
        "article_ids": [str(value) for value in (payload.get("article_ids") or [])],
    }

    if not item["article_ids"]:
        without_article.append(item)
        continue

    for article_id in item["article_ids"]:
        current = by_article.get(article_id)
        if current is None or (item["created_at"], item["id"]) > (current["created_at"], current["id"]):
            by_article[article_id] = item

selected = sorted(
    by_article.values(),
    key=lambda value: (value["filename"], value["created_at"], value["id"]),
)

print(json.dumps({
    "jobs": selected,
    "coverage": len(by_article),
    "historical_without_article": len(without_article),
}, ensure_ascii=True))

db.close()
'@

do {
    $raw = $statusCode |
        docker compose exec -T `
          -e "SOURCE_ID=$SourceId" `
          api python -

    if ($LASTEXITCODE -ne 0) {
        throw "Failed to read vector index jobs. Confirm that the api service is running."
    }

    $state = ($raw -join "`n") | ConvertFrom-Json
    $jobs = @($state.jobs)
    $active = @($jobs | Where-Object { $_.status -in @("QUEUED", "RUNNING") })
    $failed = @($jobs | Where-Object { $_.status -eq "FAILED" })
    $succeeded = @($jobs | Where-Object { $_.status -eq "SUCCEEDED" })

    Clear-Host
    Write-Host "Structured vector indexing"
    Write-Host "Coverage : $($state.coverage) / $ExpectedDocuments"
    Write-Host "Succeeded: $($succeeded.Count)"
    Write-Host "Active   : $($active.Count)"
    Write-Host "Failed   : $($failed.Count)"
    Write-Host ""

    $active |
        Select-Object status, percent, stage, chunks, filename, attempts |
        Format-Table -AutoSize

    if ($failed.Count -gt 0) {
        Write-Host ""
        Write-Host "Actionable failed jobs:" -ForegroundColor Red
        $failed |
            Select-Object filename, attempts, error |
            Format-Table -Wrap -AutoSize
        throw "$($failed.Count) current vector index job(s) failed."
    }

    if ([int]$state.coverage -lt $ExpectedDocuments -and $active.Count -eq 0) {
        throw "Only $($state.coverage) of $ExpectedDocuments documents have a current vector index job."
    }

    if ($active.Count -gt 0) {
        Start-Sleep -Seconds $IntervalSeconds
    }
} while ($active.Count -gt 0)

if ($succeeded.Count -ne $ExpectedDocuments) {
    throw "Indexing stopped with $($succeeded.Count) successful documents; expected $ExpectedDocuments."
}

Write-Host "All $ExpectedDocuments document vector index jobs have completed." -ForegroundColor Green
