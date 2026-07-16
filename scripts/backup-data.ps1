param(
  [string]$BackupDir = $(if ($env:CHEMCLAW_BACKUP_DIR) { $env:CHEMCLAW_BACKUP_DIR } else { "" })
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($BackupDir)) {
  $BackupDir = Join-Path $projectRoot "backups"
}
$BackupDir = [System.IO.Path]::GetFullPath($BackupDir)
$latestName = "chemclaw-data.tgz"
$timestampName = "chemclaw-data-$(Get-Date -Format 'yyyyMMdd-HHmmss').tgz"
$latestPath = Join-Path $BackupDir $latestName
$timestampPath = Join-Path $BackupDir $timestampName
$volumeName = "sag_sagdata"

New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
Push-Location $projectRoot
$runningServices = @()
try {
  $runningServices = @(docker compose ps --services --status running 2>$null)
  if ($LASTEXITCODE -ne 0) { throw "Unable to read Docker Compose service status." }

  if ($runningServices.Count -gt 0) {
    Write-Host "[1/4] Pausing application writes..."
    docker compose stop api web | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Failed to pause the application." }
  } else {
    Write-Host "[1/4] Application is not running; no pause is needed."
  }

  docker volume inspect $volumeName *> $null
  if ($LASTEXITCODE -ne 0) {
    throw "Docker volume $volumeName was not found. Run docker compose up -d first."
  }

  Write-Host "[2/4] Backing up databases, vectors, uploads, and configuration..."
  docker run --rm --mount "type=volume,src=$volumeName,dst=/data,readonly" --mount "type=bind,src=$BackupDir,dst=/backup" node:20-alpine sh -c "cd /data && tar -czf /backup/$latestName ." | Out-Host
  if ($LASTEXITCODE -ne 0) { throw "Data backup failed." }

  Write-Host "[3/4] Saving a timestamped copy..."
  Copy-Item -LiteralPath $latestPath -Destination $timestampPath -Force

  Write-Host "[4/4] Backup completed." -ForegroundColor Green
  Write-Host "Latest backup: $latestPath"
  Write-Host "Timestamped copy: $timestampPath"
  Write-Host "This archive includes relational data, vector indexes, uploads, and configuration."
}
finally {
  if ($runningServices.Count -gt 0) {
    Write-Host "Restoring the services that were running..."
    docker compose up -d $runningServices | Out-Host
  }
  Pop-Location
}
