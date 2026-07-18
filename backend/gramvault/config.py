"""Configuration loading for GramVault.

Design:
  - All configuration lives in a YAML file (default: `config.yaml` in the
    current working directory, overridable via the `GRAMVAULT_CONFIG_PATH`
    environment variable).
  - The YAML is validated into a single `Config` object via pydantic v2.
  - Individual values can be overridden with environment variables using a
    `GRAMVAULT_<SECTION>__<FIELD>` naming scheme (double underscore between
    section and field), e.g. `GRAMVAULT_SERVER__PORT=9000`.
  - Nothing else in the codebase should read `os.environ` for paths/model
    names directly, and nothing should hardcode a path — always go through
    `get_config()`.

Usage:
    from gramvault.config import get_config

    config = get_config()
    db_path = config.resolved_db_path
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

# Environment variable that points at the YAML config file to load.
CONFIG_PATH_ENV_VAR = "GRAMVAULT_CONFIG_PATH"
# Prefix + delimiter scheme for per-field environment variable overrides.
ENV_PREFIX = "GRAMVAULT_"
ENV_NESTING_DELIMITER = "__"

# Default location, relative to the current working directory. GramVault is
# meant to be run from the repo root (or wherever `config.yaml` lives) —
# this is a discovery convention, not a hardcoded data path.
DEFAULT_CONFIG_FILENAME = "config.yaml"


class PathsConfig(BaseModel):
    """Filesystem locations. All relative paths are resolved against the
    current working directory at access time (see `Config.resolved_*`)."""

    library_dir: str = "./library"
    db_path: str = "./data/gramvault.db"
    chroma_dir: str = "./data/chroma"
    obsidian_vault_dir: str | None = None


class ModelsConfig(BaseModel):
    """Ollama model names, referenced by name only. Nothing in this
    scaffold calls these models — see Agents A3 (AI pipeline) and A4
    (chat) for the actual Ollama client calls."""

    chat_model: str = "llama3.1:8b"
    vision_model: str = "llava:7b"
    embedding_model: str = "nomic-embed-text"


class OllamaConfig(BaseModel):
    host: str = "http://localhost:11434"


class ChunkingConfig(BaseModel):
    chunk_size: int = 512
    chunk_overlap: int = 64


class VideoConfig(BaseModel):
    """ffmpeg keyframe extraction settings."""

    keyframe_interval_seconds: int = 5
    max_keyframes: int = 6


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000


class ExportConfig(BaseModel):
    """Obsidian export behavior (Agent A6). Additive section — safe
    defaults so existing config.yaml files without an `export:` block
    keep working unchanged."""

    # Subfolder created inside `paths.obsidian_vault_dir` when a request
    # doesn't specify one explicitly.
    default_vault_subfolder: str = "GramVault"
    # "copy": copy media files into the vault subfolder and embed them
    # with Obsidian `![[...]]` syntax. "link": leave media where it is
    # and link out to the original path instead.
    media_mode: Literal["copy", "link"] = "copy"


class Config(BaseModel):
    paths: PathsConfig = Field(default_factory=PathsConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    video: VideoConfig = Field(default_factory=VideoConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)

    # --- convenience resolved paths (absolute, based on cwd) ---

    @property
    def resolved_library_dir(self) -> Path:
        return Path(self.paths.library_dir).expanduser().resolve()

    @property
    def resolved_db_path(self) -> Path:
        return Path(self.paths.db_path).expanduser().resolve()

    @property
    def resolved_chroma_dir(self) -> Path:
        return Path(self.paths.chroma_dir).expanduser().resolve()

    @property
    def resolved_obsidian_vault_dir(self) -> Path | None:
        if self.paths.obsidian_vault_dir is None:
            return None
        return Path(self.paths.obsidian_vault_dir).expanduser().resolve()


def _find_config_path() -> Path | None:
    """Locate the YAML config file to load, or None to use pure defaults."""
    env_path = os.environ.get(CONFIG_PATH_ENV_VAR)
    if env_path:
        return Path(env_path)

    cwd_candidate = Path.cwd() / DEFAULT_CONFIG_FILENAME
    if cwd_candidate.exists():
        return cwd_candidate

    return None


def _set_nested(data: dict[str, Any], dotted_keys: list[str], value: str) -> None:
    """Set `value` into nested dict `data` following `dotted_keys`, coercing
    the value to int/float/bool where it obviously parses as one (YAML-ish
    coercion), otherwise leaving it as a string for pydantic to validate."""
    cursor = data
    for key in dotted_keys[:-1]:
        cursor = cursor.setdefault(key, {})
    leaf_key = dotted_keys[-1]

    parsed: Any = value
    lowered = value.lower()
    if lowered in ("true", "false"):
        parsed = lowered == "true"
    elif lowered in ("null", "none", "~"):
        parsed = None
    else:
        try:
            parsed = int(value)
        except ValueError:
            try:
                parsed = float(value)
            except ValueError:
                parsed = value

    cursor[leaf_key] = parsed


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """Apply GRAMVAULT_<SECTION>__<FIELD> environment variable overrides
    on top of the loaded YAML data (mutates and returns `data`)."""
    for env_key, env_value in os.environ.items():
        if not env_key.startswith(ENV_PREFIX):
            continue
        # Skip the config-path variable itself.
        if env_key == CONFIG_PATH_ENV_VAR:
            continue
        remainder = env_key[len(ENV_PREFIX) :]
        if not remainder:
            continue
        parts = [p.lower() for p in remainder.split(ENV_NESTING_DELIMITER) if p]
        if not parts:
            continue
        _set_nested(data, parts, env_value)
    return data


def load_config(path: Path | None = None) -> Config:
    """Load configuration from YAML (if present) + environment overrides.

    This does NOT cache — use `get_config()` for the cached singleton.
    Passing an explicit `path` is mainly useful for tests.
    """
    config_path = path if path is not None else _find_config_path()

    raw: dict[str, Any] = {}
    if config_path is not None and config_path.exists():
        with config_path.open("r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
            if loaded:
                raw = loaded

    raw = _apply_env_overrides(raw)
    return Config.model_validate(raw)


@lru_cache(maxsize=1)
def get_config() -> Config:
    """Return the process-wide cached Config singleton."""
    return load_config()


def reload_config() -> Config:
    """Clear the cached singleton and reload it. Mainly for tests / after
    a config file is edited at runtime."""
    get_config.cache_clear()
    return get_config()


def get_config_path() -> Path:
    """Resolve which config.yaml file writes should target — same discovery
    rule as `load_config()`. Kept as a separate accessor (rather than
    stashing this on `Config` itself) so `Config` stays a plain,
    serializable data model with no filesystem provenance baked in, and so
    it's overridable per-app the same way `get_config_dependency` is (see
    `gramvault.api.deps.get_config_path_dependency`) — tests must never
    resolve this to the real project's config.yaml."""
    return _find_config_path() or (Path.cwd() / DEFAULT_CONFIG_FILENAME)


def save_obsidian_vault_dir(vault_dir: str | None, config_path: Path) -> Config:
    """Persist `paths.obsidian_vault_dir` to `config_path` and refresh the
    cached singleton, so the value survives a server restart.

    Rewrites just the `obsidian_vault_dir` line in place with a targeted
    regex rather than a full YAML load/dump round-trip, because PyYAML's
    dumper silently drops the file's comments (config.yaml is meant to stay
    human-readable/hand-editable). Used by the Settings page's "Save vault
    path" action, since the export endpoints only ever read from config,
    never from a request body. Callers must pass an explicit `config_path`
    (see `get_config_path`) rather than relying on discovery here, so tests
    can point this at a tmp file instead of the real config.yaml.
    """
    # A hand-rolled double-quoted YAML scalar, not yaml.safe_dump(vault_dir)
    # — PyYAML appends a spurious "...\n" document-end marker when dumping a
    # bare top-level string, which corrupts the file once spliced inline.
    value_literal = "null" if vault_dir is None else '"' + vault_dir.replace("\\", "\\\\").replace('"', '\\"') + '"'
    new_line = f"  obsidian_vault_dir: {value_literal}"

    text = config_path.read_text(encoding="utf-8") if config_path.exists() else "paths:\n"

    # Replacement strings built from a filesystem path (esp. on Windows,
    # where "\U..."/"\1" etc. look like regex backreferences/escapes) must
    # go through a callable, never a plain string, or re.sub tries to parse
    # backslashes in the path as its own escape syntax.
    pattern = re.compile(r"^  obsidian_vault_dir:.*$", re.MULTILINE)
    if pattern.search(text):
        text = pattern.sub(lambda _: new_line, text, count=1)
    elif re.search(r"^paths:", text, re.MULTILINE):
        text = re.sub(
            r"^paths:$", lambda _: "paths:\n" + new_line, text, count=1, flags=re.MULTILINE
        )
    else:
        text = text.rstrip("\n") + "\n\npaths:\n" + new_line + "\n"

    config_path.write_text(text, encoding="utf-8")

    # Clear the global singleton on a best-effort basis so a real running
    # server picks up the change on its next get_config() call. Note this
    # does NOT re-derive from config_path — get_config() re-runs its own
    # discovery (_find_config_path()), which matches config_path exactly
    # when this is called via the real dependency (get_config_path), but
    # may diverge under a test's dependency override. The return value
    # below is always correct regardless, since it loads config_path
    # directly rather than relying on discovery.
    get_config.cache_clear()
    return load_config(config_path)
