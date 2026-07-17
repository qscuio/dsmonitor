$ErrorActionPreference = "Stop"

$python = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$iconPath = (Resolve-Path (Join-Path $projectRoot "assets\dsmonitor.ico")).Path

if (-not (Test-Path $python)) {
    throw "Virtual environment not found at $python"
}

Push-Location $projectRoot
try {
    & $python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name dsmonitor `
        --icon $iconPath `
        --add-data "$iconPath;assets" `
        --paths src `
        run_tftp_monitor.py

    $distDir = Resolve-Path (Join-Path $projectRoot "dist")
    $legacyOneDir = Join-Path $distDir "dsmonitor"
    if (Test-Path -LiteralPath $legacyOneDir) {
        $resolvedLegacyOneDir = Resolve-Path $legacyOneDir
        if (-not $resolvedLegacyOneDir.Path.StartsWith($distDir.Path + [System.IO.Path]::DirectorySeparatorChar)) {
            throw "Refusing to remove unexpected path: $($resolvedLegacyOneDir.Path)"
        }
        Remove-Item -LiteralPath $resolvedLegacyOneDir.Path -Recurse -Force
    }
}
finally {
    Pop-Location
}
