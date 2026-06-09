param(
    [int]$Port = 8775
)

$ErrorActionPreference = "Stop"
$workspace = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $workspace ".venv\Scripts\python.exe"
$server = Join-Path $PSScriptRoot "server.py"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment Python was not found: $python"
}

$serverPattern = [regex]::Escape("web\sales_demo\server.py")
$existing = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq "python.exe" -and
    $_.CommandLine -match $serverPattern -and
    $_.CommandLine -match "--port\s+$Port(?:\s|$)"
}

foreach ($process in $existing) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}

Set-Location -LiteralPath $workspace
Write-Host "Sales Demo: http://127.0.0.1:$Port"
Write-Host "Press Ctrl+C to stop the service."
& $python $server --port $Port
