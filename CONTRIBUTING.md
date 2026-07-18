# Contributing to GramVault

Thanks for considering a contribution. GramVault is a small, local-first project — the bar for contributing is "it works, it's tested, and it doesn't leak data off the user's machine."

## Dev environment setup

Same prerequisites as the [Quickstart](README.md#quickstart): Python 3.11+, Node 20.19+ (22 LTS recommended), ffmpeg on `PATH`, and Ollama installed locally.

```bash
git clone https://github.com/<your-fork>/gramvault.git
cd gramvault

# macOS / Linux
./setup.sh

# Windows (PowerShell)
./setup.ps1
```

This creates a `.venv`, installs the backend in editable mode with dev extras (`pip install -e ".[dev]"`), and installs + builds the frontend. If you'd rather do it by hand:

```bash
python -m venv .venv
# macOS/Linux: source .venv/bin/activate
# Windows:     .venv\Scripts\Activate.ps1
pip install -e ".[dev]"

cd frontend
npm install
npm run build
cd ..
```

For frontend development with hot reload, run `npm run dev` inside `frontend/` (Vite dev server on port 5173) alongside `gramvault serve` — the backend's CORS config already allows the Vite dev origin.

## Running tests and linters

Backend (from the repo root, with the venv active):

```bash
pytest backend/tests
ruff check .
```

Frontend (from `frontend/`):

```bash
npm run build   # tsc -b && vite build — also acts as a type-check
npm run lint    # oxlint
```

All four are run in CI on every push and pull request (see `.github/workflows/ci.yml`). AI calls (Ollama, ffmpeg, faster-whisper) are mocked in the test suite, so you don't need a real Ollama install or GPU to run tests — you only need it for manual end-to-end testing.

## Code style

- **Python**: type hints on public functions, and the code must be clean under `ruff check .` (see `[tool.ruff]` in `pyproject.toml` for the enabled rule set). No new `ruff` warnings, no unused imports, no bare `except`.
- **TypeScript**: strict mode is on (`tsconfig.json`); avoid `any`, prefer explicit prop/return types on exported components and functions. `npm run build`'s `tsc -b` step must pass with no type errors, and `npm run lint` must be clean.
- Keep functions and modules focused — this codebase favors small, well-documented modules (see the module-level docstrings throughout `backend/gramvault/`) over large ones.
- Match existing patterns in the file/directory you're editing before introducing a new one.

## Submitting a pull request

1. Fork the repo and create a branch off `master` (`git checkout -b feature/short-description`).
2. Make your change, with tests for new behavior and updated tests for changed behavior.
3. Run the full check locally: `pytest backend/tests`, `ruff check .`, and `npm run build` + `npm run lint` in `frontend/`. All should pass before you open a PR.
4. Write a clear PR description: what changed and why, not just what. Link any relevant issue.
5. Keep PRs scoped to one change — smaller PRs are easier to review and merge.

If you're planning a larger change (a new page, a new pipeline stage, a new export format), consider opening an issue first to discuss the approach — check [ROADMAP.md](ROADMAP.md) for ideas already logged.
