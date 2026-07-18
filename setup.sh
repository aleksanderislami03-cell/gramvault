#!/usr/bin/env bash
# GramVault setup script for macOS / Linux.
#
# Creates a Python virtual environment, installs the backend (editable,
# with dev extras), and installs + builds the frontend. Safe to re-run.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
RESET='\033[0m'

info() { printf "${BOLD}==>${RESET} %s\n" "$1"; }
ok()   { printf "${GREEN}[OK]${RESET} %s\n" "$1"; }
warn() { printf "${YELLOW}[!]${RESET} %s\n" "$1"; }
fail() { printf "${RED}[X]${RESET} %s\n" "$1"; }

# --- find a usable python (3.11+) ---
PYTHON_BIN=""
for candidate in python3.13 python3.12 python3.11 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    version="$("$candidate" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "0.0")"
    major="${version%%.*}"
    minor="${version##*.}"
    if [ "$major" -eq 3 ] 2>/dev/null && [ "$minor" -ge 11 ] 2>/dev/null; then
      PYTHON_BIN="$candidate"
      break
    fi
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  fail "Python 3.11+ not found on PATH."
  echo "  Install it from https://www.python.org/downloads/ and re-run this script."
  exit 1
fi
ok "Found $("$PYTHON_BIN" --version 2>&1) ($PYTHON_BIN)"

# --- check node (20.19+, Vite 8 requirement) ---
if ! command -v node >/dev/null 2>&1; then
  fail "Node.js not found on PATH."
  echo "  Install Node 22 LTS from https://nodejs.org/ and re-run this script."
  exit 1
fi
NODE_MAJOR="$(node -e 'console.log(process.versions.node.split(".")[0])')"
if [ "$NODE_MAJOR" -lt 20 ]; then
  fail "Node $(node --version) found, but GramVault needs Node 20.19+ (22 LTS recommended)."
  exit 1
fi
ok "Found Node $(node --version)"

# --- check ffmpeg (needed at runtime, not for setup itself) ---
if command -v ffmpeg >/dev/null 2>&1; then
  ok "Found ffmpeg"
else
  warn "ffmpeg not found on PATH. Video/reel keyframe extraction won't work until it's installed."
  echo "  Install it from https://ffmpeg.org/download.html (or your package manager) before running 'gramvault serve'."
fi

# --- check ollama (needed at runtime, not for setup itself) ---
if command -v ollama >/dev/null 2>&1; then
  ok "Found Ollama"
else
  warn "Ollama not found on PATH. The AI pipeline and chat features won't work until it's installed."
  echo "  Install it from https://ollama.com/download before running 'gramvault serve'."
fi

# --- create local config from example (config.yaml is gitignored: it may hold personal paths) ---
if [ ! -f config.yaml ]; then
  cp config.example.yaml config.yaml
  ok "Created config.yaml from config.example.yaml."
else
  ok "config.yaml already exists — leaving it untouched."
fi

# --- python venv + backend install ---
info "Creating virtual environment (.venv)..."
"$PYTHON_BIN" -m venv .venv
ok "Virtual environment ready."

info "Installing backend (editable, with dev extras)..."
./.venv/bin/pip install --upgrade pip >/dev/null
./.venv/bin/pip install -e ".[dev]"
ok "Backend installed."

# --- frontend install + build ---
info "Installing frontend dependencies..."
(cd frontend && npm install)
ok "Frontend dependencies installed."

info "Building frontend..."
(cd frontend && npm run build)
ok "Frontend built."

echo
printf "${BOLD}Setup complete.${RESET}\n"
echo
echo "Next steps:"
echo "  1. Pull the Ollama models GramVault uses by default:"
echo "       ollama pull llama3.1:8b"
echo "       ollama pull llava:7b"
echo "       ollama pull nomic-embed-text"
echo "  2. Activate the virtual environment:"
echo "       source .venv/bin/activate"
echo "  3. Start the server:"
echo "       gramvault serve"
echo "  4. Visit http://localhost:8000 and import your Instagram export"
echo "     (or try tests/fixtures/sample_export.zip as a demo)."
echo
