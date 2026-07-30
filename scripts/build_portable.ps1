# Build portable Windows package with PyInstaller (onedir).
# Usage: powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1
# Keep this file ASCII-only so Windows PowerShell 5.1 never mis-parses UTF-8.

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Missing .venv. Run start.bat once first."
}

Write-Host "[*] Ensuring PyInstaller..."
& $Python -m pip install -q "pyinstaller>=6.0"

foreach ($dir in @("build", "dist\DiskWatch")) {
    $p = Join-Path $Root $dir
    if (Test-Path $p) { Remove-Item $p -Recurse -Force }
}

Write-Host "[*] Building (this may take several minutes)..."
& $Python -m PyInstaller --noconfirm --clean (Join-Path $Root "DiskWatch.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$Dist = Join-Path $Root "dist\DiskWatch"
if (-not (Test-Path (Join-Path $Dist "DiskWatch.exe"))) {
    throw "DiskWatch.exe not found in dist\DiskWatch"
}

# ASCII launcher name only - Chinese filenames get corrupted under PS 5.1 UTF-8 scripts.
$bat = @"
@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
start "" "DiskWatch.exe"
"@
$launcher = Join-Path $Dist "Start DiskWatch.bat"
[System.IO.File]::WriteAllText($launcher, $bat, (New-Object System.Text.UTF8Encoding $false))

$Version = "1.1.0"
$initPath = Join-Path $Root "diskwatch\__init__.py"
$init = [System.IO.File]::ReadAllText($initPath, [System.Text.Encoding]::UTF8)
if ($init -match 'VERSION\s*=\s*"([^"]+)"') { $Version = $Matches[1] }

$OutDir = Join-Path $Root "release"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$ZipName = "DiskWatch-$Version-win64-portable.zip"
$Zip = Join-Path $OutDir $ZipName
if (Test-Path $Zip) { Remove-Item $Zip -Force }

Compress-Archive -Path $Dist -DestinationPath $Zip -Force
$sizeMB = [math]::Round((Get-Item $Zip).Length / 1MB, 1)
Write-Host "OK  $Zip  ($sizeMB MB)"
Write-Host "Unzip and run DiskWatch.exe - no Python required."
