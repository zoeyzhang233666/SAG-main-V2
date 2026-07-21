param(
    [Parameter(Mandatory = $false)]
    [string]$SourceId = "bf5febf414be4b6786b0fb8e33d7dc77",

    [Parameter(Mandatory = $false)]
    [ValidateRange(3, 300)]
    [int]$IntervalSeconds = 10,

    [Parameter(Mandatory = $false)]
    [ValidateRange(1, 1000)]
    [int]$ExpectedDocuments = 36,

    [Parameter(Mandatory = $false)]
    [ValidateRange(1, 240)]
    [int]$StallWarningMinutes = 20
)

$ErrorActionPreference = "Stop"

function Convert-SizeTextToBytes {
    param([AllowNull()][string]$Text)

    if ([string]::IsNullOrWhiteSpace($Text)) {
        return [int64]0
    }

    $value = $Text.Trim()
    if ($value -notmatch '^([0-9]+(?:\.[0-9]+)?)\s*([kKmMgGtTpP]?[bB])$') {
        return [int64]0
    }

    $number = [double]::Parse(
        $Matches[1],
        [Globalization.CultureInfo]::InvariantCulture
    )

    switch ($Matches[2].ToUpperInvariant()) {
        "KB" { $multiplier = 1KB }
        "MB" { $multiplier = 1MB }
        "GB" { $multiplier = 1GB }
        "TB" { $multiplier = 1TB }
        "PB" { $multiplier = 1PB }
        default { $multiplier = 1 }
    }

    return [int64]($number * $multiplier)
}

function Format-ByteCount {
    param([int64]$Bytes)

    if ($Bytes -ge 1TB) { return ("{0:N2} TB" -f ($Bytes / 1TB)) }
    if ($Bytes -ge 1GB) { return ("{0:N2} GB" -f ($Bytes / 1GB)) }
    if ($Bytes -ge 1MB) { return ("{0:N2} MB" -f ($Bytes / 1MB)) }
    if ($Bytes -ge 1KB) { return ("{0:N2} KB" -f ($Bytes / 1KB)) }
    return "$Bytes B"
}

$statusCode = @'
import json
import os
import sqlite3
from pathlib import Path

source_id = os.environ["SOURCE_ID"]
engine_root = Path("/data/engine")

db = sqlite3.connect("/data/sag.db")
db.row_factory = sqlite3.Row

rows = db.execute(
    """
    SELECT id, status, progress, attempts, payload_json, error,
           created_at, started_at, finished_at
    FROM jobs
    WHERE source_id = ?
      AND type = 'STRUCTURED_INDEX'
    ORDER BY created_at, id
    """,
    (source_id,),
).fetchall()

by_article = {}
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
        "chunks": int((payload.get("counts") or {}).get("chunks", 0) or 0),
        "events": int((payload.get("counts") or {}).get("events", 0) or 0),
        "attempts": int(row["attempts"] or 0),
        "error": row["error"] or "",
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "article_ids": [str(value) for value in (payload.get("article_ids") or [])],
    }

    for article_id in item["article_ids"]:
        current = by_article.get(article_id)
        if current is None or (item["created_at"], item["id"]) > (
            current["created_at"], current["id"]
        ):
            by_article[article_id] = item

# One job can theoretically cover more than one article. De-duplicate by job id.
selected_by_id = {item["id"]: item for item in by_article.values()}
selected = sorted(
    selected_by_id.values(),
    key=lambda value: (value["status"], value["filename"], value["id"]),
)

document_counts = db.execute(
    """
    SELECT COUNT(*), COALESCE(SUM(chunk_count), 0), COALESCE(SUM(event_count), 0)
    FROM documents
    WHERE source_id = ?
    """,
    (source_id,),
).fetchone()

db.close()

vector_tables = []
try:
    import lancedb

    connections = {}
    for table_path in sorted(engine_root.rglob("*.lance")):
        if not table_path.is_dir():
            continue

        parent = str(table_path.parent)
        table_name = table_path.stem
        row_count = None
        count_error = ""

        try:
            connection = connections.get(parent)
            if connection is None:
                connection = lancedb.connect(parent)
                connections[parent] = connection
            row_count = int(connection.open_table(table_name).count_rows())
        except Exception as error:
            count_error = f"{type(error).__name__}: {error}"

        total_bytes = 0
        latest_mtime = 0.0
        file_count = 0
        try:
            for child in table_path.rglob("*"):
                if not child.is_file():
                    continue
                stat = child.stat()
                total_bytes += int(stat.st_size)
                latest_mtime = max(latest_mtime, float(stat.st_mtime))
                file_count += 1
        except OSError as error:
            count_error = count_error or f"OSError: {error}"

        vector_tables.append({
            "table": str(table_path.relative_to(engine_root)),
            "rows": row_count,
            "bytes": total_bytes,
            "file_count": file_count,
            "latest_mtime": latest_mtime,
            "error": count_error,
        })
except Exception as error:
    vector_tables.append({
        "table": "<lancedb discovery failed>",
        "rows": None,
        "bytes": 0,
        "file_count": 0,
        "latest_mtime": 0,
        "error": f"{type(error).__name__}: {error}",
    })

print(json.dumps({
    "jobs": selected,
    "coverage": len(by_article),
    "documents": int(document_counts[0] or 0),
    "chunks": int(document_counts[1] or 0),
    "events": int(document_counts[2] or 0),
    "vector_tables": vector_tables,
}, ensure_ascii=True))
'@

$previousSucceeded = -1
$previousVectorRows = -1L
$previousVectorBytes = -1L
$previousNetReceive = -1L
$previousNetSend = -1L
$lastEvidenceAt = Get-Date

while ($true) {
    $containerId = (docker compose ps -q api).Trim()
    if ([string]::IsNullOrWhiteSpace($containerId)) {
        throw "The api service is not running. Run: docker compose up -d api"
    }

    $raw = $statusCode |
        docker compose exec -T `
          -e "SOURCE_ID=$SourceId" `
          api python -

    if ($LASTEXITCODE -ne 0) {
        throw "Failed to read job and LanceDB state."
    }

    $state = ($raw -join "`n") | ConvertFrom-Json
    $jobs = @($state.jobs)
    $active = @($jobs | Where-Object { $_.status -in @("QUEUED", "RUNNING") })
    $running = @($jobs | Where-Object { $_.status -eq "RUNNING" })
    $failed = @($jobs | Where-Object { $_.status -eq "FAILED" })
    $succeeded = @($jobs | Where-Object { $_.status -eq "SUCCEEDED" })

    $tables = @($state.vector_tables)
    $vectorRows = [int64](($tables | Where-Object { $null -ne $_.rows } | Measure-Object -Property rows -Sum).Sum)
    $vectorBytes = [int64](($tables | Measure-Object -Property bytes -Sum).Sum)

    $latestVectorWrite = $null
    $latestMtime = [double](($tables | Measure-Object -Property latest_mtime -Maximum).Maximum)
    if ($latestMtime -gt 0) {
        $epoch = [DateTimeOffset]::FromUnixTimeSeconds([int64][Math]::Floor($latestMtime))
        $latestVectorWrite = $epoch.LocalDateTime
    }

    $statsRaw = docker stats --no-stream --format "{{json .}}" $containerId
    $stats = $null
    if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace(($statsRaw -join ""))) {
        $stats = ($statsRaw -join "`n") | ConvertFrom-Json
    }

    $netReceive = 0L
    $netSend = 0L
    if ($null -ne $stats -and -not [string]::IsNullOrWhiteSpace([string]$stats.NetIO)) {
        $netParts = [string]$stats.NetIO -split '\s*/\s*'
        if ($netParts.Count -ge 2) {
            $netReceive = Convert-SizeTextToBytes $netParts[0]
            $netSend = Convert-SizeTextToBytes $netParts[1]
        }
    }

    $deltaRows = if ($previousVectorRows -ge 0) { $vectorRows - $previousVectorRows } else { 0 }
    $deltaBytes = if ($previousVectorBytes -ge 0) { $vectorBytes - $previousVectorBytes } else { 0 }
    $deltaReceive = if ($previousNetReceive -ge 0) { $netReceive - $previousNetReceive } else { 0 }
    $deltaSend = if ($previousNetSend -ge 0) { $netSend - $previousNetSend } else { 0 }

    $hasEvidence = (
        $succeeded.Count -gt $previousSucceeded -or
        $deltaRows -gt 0 -or
        $deltaBytes -gt 0 -or
        $deltaReceive -gt 0 -or
        $deltaSend -gt 0
    )
    if ($hasEvidence) {
        $lastEvidenceAt = Get-Date
    }

    $since = "$($IntervalSeconds)s"
    $recentLogs = @(
        docker compose logs --since $since --tail 300 api 2>&1
    )
    $errorLines = @(
        $recentLogs | Where-Object {
            $_ -match 'ERROR|Traceback|BadRequestError|ConnectError|向量生成失败|embedding失败|任务失败'
        }
    )

    Clear-Host
    Write-Host "Structured vector indexing - live evidence"
    Write-Host "Updated  : $((Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))"
    Write-Host "Coverage : $($state.coverage) / $ExpectedDocuments"
    Write-Host "Succeeded: $($succeeded.Count)"
    Write-Host "Running  : $($running.Count)"
    Write-Host "Queued   : $(@($active | Where-Object { $_.status -eq 'QUEUED' }).Count)"
    Write-Host "Failed   : $($failed.Count)"
    Write-Host "Data     : $($state.documents) documents / $($state.chunks) chunks / $($state.events) events"
    Write-Host ""

    Write-Host "Live activity evidence"
    Write-Host "Vector rows : $vectorRows  (delta $deltaRows)"
    Write-Host "Vector store: $(Format-ByteCount $vectorBytes)  (delta $(Format-ByteCount $deltaBytes))"
    if ($null -ne $latestVectorWrite) {
        Write-Host "Last vector write: $($latestVectorWrite.ToString('yyyy-MM-dd HH:mm:ss'))"
    }
    if ($null -ne $stats) {
        Write-Host "API CPU / memory: $($stats.CPUPerc) / $($stats.MemUsage)"
        Write-Host "API network I/O : $($stats.NetIO)  (delta RX $(Format-ByteCount $deltaReceive), TX $(Format-ByteCount $deltaSend))"
    }
    Write-Host "Recent errors in last $IntervalSeconds sec: $($errorLines.Count)"
    Write-Host "Last observed activity: $([Math]::Round(((Get-Date) - $lastEvidenceAt).TotalSeconds)) sec ago"
    Write-Host ""

    if ($running.Count -gt 0) {
        Write-Host "Running jobs"
        $running |
            Select-Object status, percent, stage, chunks, filename, attempts, started_at |
            Format-Table -Wrap -AutoSize
    }

    if ($tables.Count -gt 0) {
        Write-Host "LanceDB tables"
        $tables |
            Sort-Object rows -Descending |
            Select-Object `
              table, `
              rows, `
              @{Name = "size"; Expression = { Format-ByteCount ([int64]$_.bytes) }}, `
              @{Name = "modified"; Expression = {
                  if ([double]$_.latest_mtime -gt 0) {
                      [DateTimeOffset]::FromUnixTimeSeconds(
                          [int64][Math]::Floor([double]$_.latest_mtime)
                      ).LocalDateTime.ToString('HH:mm:ss')
                  }
                  else { "" }
              }} |
            Format-Table -AutoSize
    }

    if ($errorLines.Count -gt 0) {
        Write-Host ""
        Write-Host "Recent error lines" -ForegroundColor Red
        $errorLines | Select-Object -Last 8 | ForEach-Object { Write-Host $_ -ForegroundColor Red }
    }

    if ($failed.Count -gt 0) {
        Write-Host ""
        Write-Host "Current failed jobs" -ForegroundColor Red
        $failed |
            Select-Object filename, attempts, error |
            Format-Table -Wrap -AutoSize
        throw "$($failed.Count) current vector index job(s) failed."
    }

    if ($active.Count -eq 0) {
        if ($succeeded.Count -eq $ExpectedDocuments) {
            Write-Host "All $ExpectedDocuments document vector index jobs completed." -ForegroundColor Green
            break
        }
        throw "No active jobs remain, but only $($succeeded.Count) of $ExpectedDocuments succeeded."
    }

    $stallMinutes = ((Get-Date) - $lastEvidenceAt).TotalMinutes
    if ($stallMinutes -ge $StallWarningMinutes) {
        Write-Host ""
        Write-Host "WARNING: no row, file-size, network, or completed-job change for $([Math]::Round($stallMinutes, 1)) minutes." -ForegroundColor Yellow
        Write-Host "Check recent API logs before restarting anything." -ForegroundColor Yellow
    }

    $previousSucceeded = $succeeded.Count
    $previousVectorRows = $vectorRows
    $previousVectorBytes = $vectorBytes
    $previousNetReceive = $netReceive
    $previousNetSend = $netSend

    Start-Sleep -Seconds $IntervalSeconds
}
