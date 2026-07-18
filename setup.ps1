<#
.SYNOPSIS
    GramVault setup script for Windows.
.DESCRIPTION
    Creates a Python virtual environment, installs the backend (editable,
    with dev extras), and installs + builds the frontend. Safe to re-run.
#>

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Write-Info($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[OK] $msg" -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "[!]  $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host "[X]  $msg" -ForegroundColor Red }

# --- Node is often installed at C:\Program Files\nodejs but not always on
#     PATH in every shell profile on this machine. Add it opportunistically
#     before checking for node/npm. ---
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    $nodeDefaultDir = "C:\Program Files\nodejs"
    if (Test-Path (Join-Path $nodeDefaultDir "node.exe")) {
        $env:PATH = "$env:PATH;$nodeDefaultDir"
    }
}

# --- find a usable python (3.11+) ---
$PythonCmd = $null
$PythonArgs = @()

if (Get-Command python -ErrorAction SilentlyContinue) {
    $verOut = & python -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($verOut) {
        $parts = $verOut.Trim().Split(".")
        if ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 11) {
            $PythonCmd = "python"
            $PythonArgs = @()
        }
    }
}

if (-not $PythonCmd -and (Get-Command py -ErrorAction SilentlyContinue)) {
    foreach ($ver in @("3.13", "3.12", "3.11")) {
        $verOut = & py "-$ver" -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($verOut) {
            $PythonCmd = "py"
            $PythonArgs = @("-$ver")
            break
        }
    }
}

if (-not $PythonCmd) {
    Write-Fail "Python 3.11+ not found on PATH."
    Write-Host "  Install it from https://www.python.org/downloads/ (check 'Add python.exe to PATH' during install) and re-run this script."
    exit 1
}
$pyVersionString = (& $PythonCmd @PythonArgs --version)
Write-Ok "Found $pyVersionString ($PythonCmd $($PythonArgs -join ' '))"

# --- check node (18+) ---
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Fail "Node.js not found on PATH."
    Write-Host "  Install Node 18+ from https://nodejs.org/ and re-run this script."
    Write-Host "  (Node is often installed at C:\Program Files\nodejs - make sure that folder is on PATH.)"
    exit 1
}
$nodeVersionRaw = (node --version).TrimStart("v")
$nodeMajor = [int]($nodeVersionRaw.Split(".")[0])
if ($nodeMajor -lt 18) {
    Write-Fail "Node $(node --version) found, but GramVault needs Node 18+."
    exit 1
}
Write-Ok "Found Node $(node --version)"

# --- check ffmpeg (needed at runtime, not for setup itself) ---
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    Write-Ok "Found ffmpeg"
} else {
    Write-Warn2 "ffmpeg not found on PATH. Video/reel keyframe extraction won't work until it's installed."
    Write-Host "  Install it from https://ffmpeg.org/download.html and add it to PATH before running 'gramvault serve'."
}

# --- check ollama (needed at runtime, not for setup itself) ---
if (Get-Command ollama -ErrorAction SilentlyContinue) {
    Write-Ok "Found Ollama"
} else {
    Write-Warn2 "Ollama not found on PATH. The AI pipeline and chat features won't work until it's installed."
    Write-Host "  Install it from https://ollama.com/download before running 'gramvault serve'."
}

# --- create local config from example (config.yaml is gitignored: it may hold personal paths) ---
if (-not (Test-Path "config.yaml")) {
    Copy-Item "config.example.yaml" "config.yaml"
    Write-Ok "Created config.yaml from config.example.yaml."
} else {
    Write-Ok "config.yaml already exists - leaving it untouched."
}

# --- python venv + backend install ---
Write-Info "Creating virtual environment (.venv)..."
& $PythonCmd @PythonArgs -m venv .venv
if ($LASTEXITCODE -ne 0) { Write-Fail "Failed to create virtual environment."; exit 1 }
Write-Ok "Virtual environment ready."

Write-Info "Installing backend (editable, with dev extras)..."
& .\.venv\Scripts\python.exe -m pip install --upgrade pip | Out-Null
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) { Write-Fail "pip install failed."; exit 1 }
Write-Ok "Backend installed."

# --- frontend install + build ---
Write-Info "Installing frontend dependencies..."
Push-Location frontend
try {
    npm install
    if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
    Write-Ok "Frontend dependencies installed."

    Write-Info "Building frontend..."
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
    Write-Ok "Frontend built."
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Pull the Ollama models GramVault uses by default:"
Write-Host "       ollama pull llama3.1:8b"
Write-Host "       ollama pull llava:7b"
Write-Host "       ollama pull nomic-embed-text"
Write-Host "  2. Activate the virtual environment:"
Write-Host "       .venv\Scripts\Activate.ps1"
Write-Host "     (If PowerShell blocks the activation script, run:"
Write-Host "      Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass)"
Write-Host "  3. Start the server:"
Write-Host "       gramvault serve"
Write-Host "  4. Visit http://localhost:8000 and import your Instagram export"
Write-Host "     (or try tests/fixtures/sample_export.zip as a demo)."
Write-Host ""
