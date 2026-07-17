"""Shared FastAPI dependencies for the api.routes_* modules."""

from __future__ import annotations

from gramvault.config import Config, get_config


def get_config_dependency() -> Config:
    """FastAPI dependency wrapper around `gramvault.config.get_config`.

    Routes should depend on this (rather than importing `get_config`
    directly) so tests can override it per-app via
    `app.dependency_overrides[get_config_dependency] = lambda: tmp_config`.
    """
    return get_config()
