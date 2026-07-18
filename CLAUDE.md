# GramVault — project memory for Claude Code

Private, local-first library for a user's saved Instagram content (photos,
videos, reels) with a local AI chatbot (RAG) and Obsidian export.
Open source (MIT), owner: aleksanderislami03-cell.

## Architecture

- `backend/gramvault/` — Python 3.11+, FastAPI, SQLite (metadata) +
  ChromaDB (vectors). Entry: `gramvault serve` (CLI in `cli.py`).
- `frontend/` — React + Vite + Tailwind, built to static files served by
  FastAPI. Pages: Gallery, ItemDetail, Chat, Import, Settings.
- AI via local Ollama only: chat `llama3.1:8b`, vision `llava:7b`,
  embeddings `nomic-embed-text`; audio via `faster-whisper`;
  keyframes via ffmpeg. Model names come from `config.yaml`, never hardcoded.
- All config flows through `config.yaml` (see `backend/gramvault/config.py`);
  env overrides use `GRAMVAULT_SECTION__KEY`.

## Hard rules — privacy (never break these)

- `config.yaml` is GITIGNORED (may contain personal paths). Never `git add -f`
  it. `config.example.yaml` is the committed template; setup scripts copy it.
- Never commit: `library/`, `data/`, `*.db`, chroma dirs, any real Instagram
  export, any real media, or absolute paths containing a username.
- Commit author must stay `Aleksander <aleksanderislami03-cell@users.noreply.github.com>`.
- No telemetry, no network calls except localhost Ollama.
- Screenshots/GIFs for README: only with the demo fixture
  (`tests/fixtures/sample_export.zip`) imported — never real user data.

## Conventions

- Python: type hints everywhere, `ruff` must pass, tests with `pytest`
  (Ollama is always mocked in tests).
- Server binds 127.0.0.1 only.
- Friendly errors for the three common failures: Ollama not running,
  model not pulled, wrong ZIP format.
- Keep scope tight: new feature ideas go to `ROADMAP.md`, not into the code.
- Windows is the dev machine: keep `setup.ps1` and `setup.sh` in sync;
  line endings governed by `.gitattributes`.

## Verify before any push

`pytest` green, `ruff check` clean, and `git ls-files` contains no
config.yaml / *.db / library files.
