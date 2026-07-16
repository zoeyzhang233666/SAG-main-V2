param(
  [Parameter(Mandatory = $true)]
  [string]$BackupFile
)

$ErrorActionPreference = "Stop"
$BackupFile = [System.IO.Path]::GetFullPath($BackupFile)
if (!(Test-Path -LiteralPath $BackupFile -PathType Leaf)) {
  throw "Legacy PostgreSQL backup was not found: $BackupFile"
}
$backupDir = Split-Path -Parent $BackupFile
$backupName = Split-Path -Leaf $BackupFile

Write-Host "Inspecting the legacy PostgreSQL backup in read-only mode..."
docker run --rm --mount "type=bind,src=$backupDir,dst=/backup,readonly" pgvector/pgvector:pg16 pg_restore --list "/backup/$backupName"
if ($LASTEXITCODE -ne 0) { throw "Legacy backup inspection failed." }
