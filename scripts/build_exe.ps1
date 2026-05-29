$ErrorActionPreference = "Stop"

$python = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

if (-not (Test-Path $python)) {
    throw "Virtual environment not found at $python"
}

Push-Location $projectRoot
try {
    & $python -m PyInstaller `
        --noconfirm `
        --clean `
        --windowed `
        --name TftpMonitor `
        --paths src `
        run_tftp_monitor.py
}
finally {
    Pop-Location
}
