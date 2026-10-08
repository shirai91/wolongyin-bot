<#
.SYNOPSIS
    Build the one-folder release of AutoNgoaLong v2: dist\AutoNgoaLong\AutoNgoaLong.exe

.DESCRIPTION
    1. Creates .venv (Python 3.12+ through the "py" launcher) and installs the project with its
       dev extras when the venv or PyInstaller is missing.
    2. Runs the offline pytest suite (skip with -SkipTests).
    3. Runs PyInstaller with AutoNgoaLong.spec (profiles\ is copied next to the exe).
    4. Runs "AutoNgoaLong.exe --selftest" on the build and fails when it fails (skip with
       -SkipSelftest). The report is written to build\selftest-report.txt. The self-test is
       stopped and the build fails after -SelftestTimeoutSec seconds (default 300).

    Windows PowerShell 5.1 compatible. Exit code 0 = build and checks passed.

.EXAMPLE
    .\build.ps1
    .\build.ps1 -SkipTests
    .\build.ps1 -Python 3.13
#>
[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$SkipSelftest,
    [string]$Python = "3",
    [ValidateRange(1, 86400)][int]$SelftestTimeoutSec = 300
)

# "Continue": PyInstaller and pip log to stderr, which Windows PowerShell 5.1 would turn into
# terminating errors under "Stop". Failures are detected by exit codes and "throw" instead.
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"
$AppName = "AutoNgoaLong"
$DistDir = Join-Path $Root "dist\$AppName"
$WorkDir = Join-Path $Root "build\pyinstaller"
$Report = Join-Path $Root "build\selftest-report.txt"

function Step([string]$Text) {
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

function Invoke-Checked([string]$Exe, [string[]]$Arguments, [string]$What) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$What failed (exit code $LASTEXITCODE)"
    }
}

# --- 1. virtual environment ---------------------------------------------------------------------
if (-not (Test-Path $VenvPy)) {
    Step "Creating .venv with py -$Python"
    Invoke-Checked "py" @("-$Python", "-m", "venv", ".venv") "venv creation"
}
& $VenvPy -c "import PyInstaller, ngoalong, PySide6, cv2" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Step "Installing the project and its dev extras into .venv"
    Invoke-Checked $VenvPy @("-m", "pip", "install", "--upgrade", "pip") "pip upgrade"
    Invoke-Checked $VenvPy @("-m", "pip", "install", "-e", ".[dev]") "pip install"
}

# --- 2. tests -----------------------------------------------------------------------------------
if (-not $SkipTests) {
    Step "Running the offline test suite (pytest)"
    Invoke-Checked $VenvPy @("-m", "pytest", "-q") "pytest"
}

# --- 3. PyInstaller -----------------------------------------------------------------------------
Step "Building $AppName with PyInstaller"
# Never touch the folder while the app runs from it: files it holds open (data\history.sqlite3)
# cannot be deleted, and a half-deleted build loses the user's files.
$running = @(Get-Process -Name $AppName -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -and $_.Path.StartsWith($DistDir, [System.StringComparison]::OrdinalIgnoreCase) })
if ($running.Count -gt 0) {
    throw "$AppName.exe is running from $DistDir (pid $($running.Id -join ', ')): close it first, then build again"
}
# User files live next to the exe (ngoalong\paths.py APP_ROOT): keep them across the rebuild.
$UserItems = @("settings.toml", "settings.toml.bak", "NgoaLong_data.ini", "data", "logs")
$Keep = Join-Path $Root "build\user-files"
if (Test-Path $Keep) { Remove-Item -Recurse -Force $Keep }
if (Test-Path $DistDir) {
    New-Item -ItemType Directory -Force $Keep | Out-Null
    foreach ($item in $UserItems) {
        $src = Join-Path $DistDir $item
        if (Test-Path $src) { Copy-Item -Recurse -Force $src (Join-Path $Keep $item) }
    }
    Remove-Item -Recurse -Force $DistDir
    if (Test-Path $DistDir) { throw "could not remove the old build in $DistDir (a file is in use?)" }
}
Invoke-Checked $VenvPy @("-m", "PyInstaller", "--noconfirm", "--clean",
    "--distpath", (Join-Path $Root "dist"), "--workpath", $WorkDir, "AutoNgoaLong.spec") "PyInstaller"
if (Test-Path $Keep) {
    foreach ($item in $UserItems) {
        $src = Join-Path $Keep $item
        if (Test-Path $src) { Copy-Item -Recurse -Force $src (Join-Path $DistDir $item) }
    }
    Write-Host "Kept user files from the previous build: $((Get-ChildItem $Keep).Name -join ', ')"
}

$Exe = Join-Path $DistDir "$AppName.exe"
if (-not (Test-Path $Exe)) { throw "build output missing: $Exe" }
if (-not (Test-Path (Join-Path $DistDir "profiles\ngoalongviet\locator.yaml"))) {
    throw "profiles\ was not copied next to the exe"
}

# --- 4. self-test of the build ------------------------------------------------------------------
if (-not $SkipSelftest) {
    Step "Running $AppName.exe --selftest"
    if (Test-Path $Report) { Remove-Item -Force $Report }
    # The exe is a windowed app (no console): wait for it with a time limit (an error dialog of
    # the bootloader would otherwise block the build forever) and read the report file.
    $proc = Start-Process -FilePath $Exe -ArgumentList @("--selftest", "--report", "`"$Report`"") -PassThru
    $null = $proc.Handle  # keep the process handle so ExitCode is readable after the exit
    if (-not $proc.WaitForExit($SelftestTimeoutSec * 1000)) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        throw "self-test timed out after $SelftestTimeoutSec s (exe hung or showed an error dialog)"
    }
    $proc.WaitForExit()  # fills ExitCode
    if (Test-Path $Report) {
        Get-Content -Path $Report -Encoding UTF8 | ForEach-Object { Write-Host $_ }
    } else {
        Write-Host "(no self-test report was written)" -ForegroundColor Yellow
    }
    if ($proc.ExitCode -ne 0) {
        throw "self-test of the build failed (exit code $($proc.ExitCode))"
    }
}

$SizeMb = [math]::Round(((Get-ChildItem -Recurse -File $DistDir | Measure-Object -Sum Length).Sum / 1MB), 1)
Step "Done: $Exe ($SizeMb MB in $DistDir)"
exit 0
