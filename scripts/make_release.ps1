# Build a source release zip (no .venv, no pycache).
# Usage: powershell -ExecutionPolicy Bypass -File scripts\make_release.ps1

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$Version = "1.0.0"
$initPath = Join-Path $Root "diskwatch\__init__.py"
$init = [System.IO.File]::ReadAllText($initPath, [System.Text.Encoding]::UTF8)
if ($init -match 'VERSION\s*=\s*"([^"]+)"') {
    $Version = $Matches[1]
}

$OutDir = Join-Path $Root "release"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$StageName = "DiskWatch-$Version"
$Stage = Join-Path $OutDir $StageName
if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Stage | Out-Null

$include = @(
    "diskwatch",
    "docs",
    "tests",
    "scripts",
    "LICENSE",
    "README.md",
    "README.zh-CN.md",
    "CHANGELOG.md",
    "requirements.txt",
    "run.pyw",
    "run_portable.py",
    "DiskWatch.spec",
    ".gitignore",
    ".gitattributes"
)

foreach ($item in $include) {
    $src = Join-Path $Root $item
    if (-not (Test-Path $src)) {
        Write-Warning "Skip missing: $item"
        continue
    }
    $dst = Join-Path $Stage $item
    if ((Get-Item $src).PSIsContainer) {
        Copy-Item $src $dst -Recurse -Force
    } else {
        $parent = Split-Path $dst -Parent
        if ($parent -and -not (Test-Path $parent)) {
            New-Item -ItemType Directory -Force -Path $parent | Out-Null
        }
        Copy-Item $src $dst -Force
    }
}

# Copy all root .bat launchers (ASCII + Chinese names)
Get-ChildItem (Join-Path $Root "*.bat") -File | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $Stage $_.Name) -Force
}

Get-ChildItem $Stage -Recurse -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem $Stage -Recurse -File -Include "*.pyc","*.pyo" |
    Remove-Item -Force -ErrorAction SilentlyContinue

$Zip = Join-Path $OutDir "$StageName-src.zip"
if (Test-Path $Zip) { Remove-Item $Zip -Force }

Compress-Archive -Path $Stage -DestinationPath $Zip -Force

$size = [math]::Round((Get-Item $Zip).Length / 1KB, 1)
Write-Host "OK  $Zip  ($size KB)"
Write-Host "Staging folder: $Stage"
