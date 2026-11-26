$ErrorActionPreference = 'Stop'
$installer = Get-ChildItem "$PSScriptRoot/../release" -Filter 'ChemClaw-KG-Setup-*-win-x64.exe' | Select-Object -First 1
if (!$installer) { throw 'Missing Windows installer' }
$installDir = Join-Path $env:RUNNER_TEMP 'chemclaw-kg-install'
$logFile = Join-Path $env:APPDATA 'ChemClaw Knowledge Graph/logs/main.log'
$setup = Start-Process -FilePath $installer.FullName -ArgumentList @('/S', "/D=$installDir") -Wait -PassThru
if ($setup.ExitCode -ne 0) { throw "Installer exit code $($setup.ExitCode)" }
$exe = Join-Path $installDir 'ChemClaw Knowledge Graph.exe'
if (!(Test-Path $exe)) { throw "Installed executable not found: $exe" }
# Hold the preferred API port to exercise the actual production fallback.
$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 18100)
$listener.Start()
$proc = $null
try {
  $proc = Start-Process -FilePath $exe -PassThru
  $deadline = (Get-Date).AddMinutes(4)
  $ready = $false
  while ((Get-Date) -lt $deadline) {
    $proc.Refresh()
    if ($proc.HasExited) { throw "Desktop exited: $($proc.ExitCode)" }
    if (Test-Path $logFile) {
      $log = Get-Content $logFile -Raw
      $ports = [regex]::Matches($log, 'Resolved API port (\d+)')
      if ($ports.Count -gt 0 -and $log.Contains('Desktop runtime ready') -and $log.Contains('Desktop main window loaded')) {
        $port = [int]$ports[$ports.Count - 1].Groups[1].Value
        if ($port -eq 18100) { throw 'Did not bypass occupied port' }
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$port/api/v1/system/ready" -TimeoutSec 5
        if ($response.StatusCode -eq 200) { $ready = $true; break }
      }
    }
    Start-Sleep -Seconds 2
  }
  if (!$ready) { throw 'Installed app readiness timed out' }
  Write-Host 'Installed app launched and API readiness verified with preferred port occupied.'
} finally {
  if (Test-Path $logFile) { Get-Content $logFile -Tail 150 }
  if ($proc -and !$proc.HasExited) {
    if ($proc.CloseMainWindow()) { [void]$proc.WaitForExit(15000) }
    if (!$proc.HasExited) { & taskkill.exe /PID $proc.Id /T /F | Out-Host }
  }
  $listener.Stop()
}
