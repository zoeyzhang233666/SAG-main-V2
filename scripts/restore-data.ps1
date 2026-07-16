param(
  [string]$BackupFile = $(if ($env:CHEMCLAW_BACKUP_FILE) { $env:CHEMCLAW_BACKUP_FILE } else { "" })
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($BackupFile)) {
  $BackupFile = Join-Path $projectRoot "backups\chemclaw-data.tgz"
}
$BackupFile = [System.IO.Path]::GetFullPath($BackupFile)
if (!(Test-Path -LiteralPath $BackupFile -PathType Leaf)) {
  throw "ChemClaw full backup was not found: $BackupFile"
}

$volumeName = "sag_sagdata"
$backupDir = Split-Path -Parent $BackupFile
$backupName = Split-Path -Leaf $BackupFile
$safetyName = "chemclaw-before-restore-$(Get-Date -Format 'yyyyMMdd-HHmmss').tgz"

Write-Host "This will replace the local ChemClaw data with:" -ForegroundColor Yellow
Write-Host $BackupFile
Write-Host "A safety snapshot will be created first: $(Join-Path $backupDir $safetyName)"
$confirm = Read-Host "Type RESTORE to continue"
if ($confirm -cne "RESTORE") {
  Write-Host "Restore cancelled."
  exit 0
}

Push-Location $projectRoot
try {
  docker volume inspect $volumeName *> $null
  if ($LASTEXITCODE -ne 0) {
    docker compose create api | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Unable to create Docker volume $volumeName." }
  }

  Write-Host "[1/5] Stopping the application..."
  docker compose stop api web | Out-Host

  Write-Host "[2/5] Creating a pre-restore safety snapshot..."
  docker run --rm --mount "type=volume,src=$volumeName,dst=/data,readonly" --mount "type=bind,src=$backupDir,dst=/backup" node:20-alpine sh -c "cd /data && tar -czf /backup/$safetyName ." | Out-Host
  if ($LASTEXITCODE -ne 0) { throw "Safety snapshot failed; existing data was not changed." }

  Write-Host "[3/5] Validating the backup archive..."
  docker run --rm --mount "type=bind,src=$backupDir,dst=/backup,readonly" node:20-alpine tar -tzf "/backup/$backupName" *> $null
  if ($LASTEXITCODE -ne 0) { throw "Backup validation failed; existing data was not changed." }

  Write-Host "[4/5] Restoring databases, vector indexes, and uploads..."
  docker run --rm --mount "type=volume,src=$volumeName,dst=/data" --mount "type=bind,src=$backupDir,dst=/backup,readonly" node:20-alpine sh -c "find /data -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + && tar -xzf /backup/$backupName -C /data" | Out-Host
  if ($LASTEXITCODE -ne 0) { throw "Data restore failed. The safety snapshot can be used for recovery." }

  Write-Host "[5/5] Starting the application..."
  docker compose up -d api web | Out-Host
  if ($LASTEXITCODE -ne 0) { throw "Data was restored, but the application failed to start. Run docker compose logs." }
  Write-Host "Restore completed. No parsing or vector regeneration is required." -ForegroundColor Green
}
finally {
  Pop-Location
}
