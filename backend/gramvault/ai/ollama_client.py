"""Thin async HTTP wrapper around a local Ollama server.

SCAFFOLD NOTICE (from Agent A4, chat/search):
    This file did not exist yet when A4 started building `gramvault.chat.*`
    (which needs `embed()` for retrieval and a text chat-completion function
    for the RAG response). Per the multi-agent task split, A3 (AI pipeline)
    owns this file for real — the functions below are a minimal, working
    implementation so A4 wasn't blocked. A3: please review, replace/extend
    `generate_caption()` (vision captioning — not used by A4) with the real
    faster-whisper/llava-aware implementation, and treat `embed()`,
    `chat_completion()`, `stream_chat()`, and the exception classes as the
    stable contract other agents (A4) depend on — coordinate before
    changing their signatures.

Everything here talks to `config.ollama.host` (default
http://localhost:11434) using Ollama's native HTTP API:
    GET  /api/tags       -> list locally-pulled models (health + pull check)
    POST /api/embeddings -> {model, prompt} -> {embedding: [...]}
    POST /api/chat       -> {model, messages, stream} -> chat completion
    POST /api/generate   -> {model, prompt, images} -> vision/text generation
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx

from gramvault.config import Config, get_config


class OllamaError(Exception):
    """Base class for all Ollama-related failures."""


class OllamaNotRunningError(OllamaError):
    """Raised when the Ollama server can't be reached at all."""

    def __init__(self, host: str, cause: Exception | None = None) -> None:
        super().__init__(
            f"Could not reach Ollama at {host}. Is `ollama serve` running? "
            f"(underlying error: {cause})"
        )
        self.host = host
        self.cause = cause


class ModelNotPulledError(OllamaError):
    """Raised when a required model isn't in `ollama list` yet."""

    def __init__(self, model: str) -> None:
        super().__init__(
            f"Model '{model}' is not pulled. Run `ollama pull {model}` and try again."
        )
        self.model = model


def _client(config: Config, timeout: float = 30.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=config.ollama.host, timeout=timeout)


async def check_health(config: Config | None = None) -> bool:
    """Return True if the Ollama server responds at all. Does not raise —
    use `ensure_running()` if you want the friendly exception instead."""
    config = config or get_config()
    try:
        async with _client(config, timeout=5.0) as client:
            resp = await client.get("/api/tags")
            return resp.status_code == 200
    except httpx.HTTPError:
        return False


async def ensure_running(config: Config | None = None) -> None:
    """Raise `OllamaNotRunningError` if the server can't be reached."""
    config = config or get_config()
    try:
        async with _client(config, timeout=5.0) as client:
            resp = await client.get("/api/tags")
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise OllamaNotRunningError(config.ollama.host, exc) from exc


async def is_model_pulled(model: str, config: Config | None = None) -> bool:
    """Check whether `model` is present in the local Ollama model list.

    Note (Agent A3): wraps connection failures into `OllamaNotRunningError`
    (matching `ensure_running()`) rather than letting a raw `httpx.ConnectError`
    escape — otherwise callers that call `ensure_model_pulled()` without an
    `ensure_running()` first would get an unfriendly stack trace instead of
    the intended friendly exception.
    """
    config = config or get_config()
    try:
        async with _client(config, timeout=10.0) as client:
            resp = await client.get("/api/tags")
            resp.raise_for_status()
            data = resp.json()
    except httpx.ConnectError as exc:
        raise OllamaNotRunningError(config.ollama.host, exc) from exc
    names = {m.get("name") for m in data.get("models", [])}
    # Ollama tags are often "name:tag" — also accept a bare-name match so
    # "llama3.1:8b" matches a config value of "llama3.1".
    if model in names:
        return True
    return any(name.split(":")[0] == model.split(":")[0] for name in names if name)


async def ensure_model_pulled(model: str, config: Config | None = None) -> None:
    """Raise `ModelNotPulledError` if `model` isn't pulled locally."""
    if not await is_model_pulled(model, config):
        raise ModelNotPulledError(model)


async def embed(text: str, model: str | None = None, config: Config | None = None) -> list[float]:
    """Embed `text` using the configured embedding model (nomic-embed-text
    by default). Raises `OllamaNotRunningError`/`ModelNotPulledError` on
    failure, wrapping the raw httpx error otherwise."""
    config = config or get_config()
    model = model or config.models.embedding_model
    try:
        async with _client(config) as client:
            resp = await client.post("/api/embeddings", json={"model": model, "prompt": text})
            resp.raise_for_status()
            data = resp.json()
    except httpx.ConnectError as exc:
        raise OllamaNotRunningError(config.ollama.host, exc) from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise ModelNotPulledError(model) from exc
        raise
    return data["embedding"]


async def generate_caption(
    prompt: str,
    image_b64: str | None = None,
    model: str | None = None,
    config: Config | None = None,
) -> str:
    """Generate a text (optionally vision-grounded) caption via
    `/api/generate`. Owned by Agent A3 in practice (llava captioning) —
    this is a minimal passthrough so the interface exists."""
    config = config or get_config()
    model = model or config.models.vision_model
    payload: dict[str, Any] = {"model": model, "prompt": prompt, "stream": False}
    if image_b64 is not None:
        payload["images"] = [image_b64]
    try:
        async with _client(config, timeout=120.0) as client:
            resp = await client.post("/api/generate", json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.ConnectError as exc:
        raise OllamaNotRunningError(config.ollama.host, exc) from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise ModelNotPulledError(model) from exc
        raise
    return data.get("response", "")


# --- Added by Agent A3 (AI pipeline): image captioning ----------------------
#
# `generate_caption()` above is a generic passthrough to `/api/generate`
# (A4 added it as a minimal scaffold). `caption_image()` is the real
# vision-captioning entry point used by `gramvault.ai.pipeline` — it owns
# reading + base64-encoding the image file and supplying a sensible default
# prompt, so callers just pass a path.

DEFAULT_CAPTION_PROMPT = (
    "Describe this image in 2-3 concise, factual sentences. Focus on the "
    "main subject(s), setting, and any clearly visible text. Do not "
    "speculate about anything you can't actually see."
)


async def caption_image(
    image_path: Path,
    prompt: str = DEFAULT_CAPTION_PROMPT,
    model: str | None = None,
    config: Config | None = None,
) -> str:
    """Caption a single image file with the configured vision model
    (llava by default). Thin convenience wrapper over `generate_caption()`
    that handles reading + base64-encoding `image_path`."""
    image_bytes = Path(image_path).read_bytes()
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    return await generate_caption(prompt, image_b64=image_b64, model=model, config=config)


# --- Added by Agent A4 (chat/search): text chat completion -----------------
#
# `gramvault.chat.service` needs a non-streaming and a streaming chat
# completion function against `/api/chat` (distinct from the
# embedding/vision helpers above, which are A3's). Added additively —
# nothing above this section was modified.


async def chat_completion(
    messages: list[dict[str, str]],
    model: str | None = None,
    config: Config | None = None,
) -> str:
    """Non-streaming chat completion. `messages` is a list of
    `{"role": "system"|"user"|"assistant", "content": str}` dicts, mirroring
    Ollama's `/api/chat` request shape. Returns the full response text."""
    config = config or get_config()
    model = model or config.models.chat_model
    try:
        async with _client(config, timeout=120.0) as client:
            resp = await client.post(
                "/api/chat",
                json={"model": model, "messages": messages, "stream": False},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.ConnectError as exc:
        raise OllamaNotRunningError(config.ollama.host, exc) from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise ModelNotPulledError(model) from exc
        raise
    return data.get("message", {}).get("content", "")


async def stream_chat(
    messages: list[dict[str, str]],
    model: str | None = None,
    config: Config | None = None,
) -> AsyncIterator[str]:
    """Streaming chat completion. Yields response text tokens/fragments as
    they arrive from Ollama's newline-delimited-JSON `/api/chat` stream.

    Raises `OllamaNotRunningError`/`ModelNotPulledError` before yielding
    anything if the initial connection/request fails; once streaming has
    started, transport errors propagate as-is (the caller decides how to
    surface a mid-stream failure).
    """
    config = config or get_config()
    model = model or config.models.chat_model
    import json as _json

    client = _client(config, timeout=None)
    try:
        async with client.stream(
            "POST",
            "/api/chat",
            json={"model": model, "messages": messages, "stream": True},
        ) as resp:
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    raise ModelNotPulledError(model) from exc
                raise
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                chunk = _json.loads(line)
                content = chunk.get("message", {}).get("content", "")
                if content:
                    yield content
                if chunk.get("done"):
                    break
    except httpx.ConnectError as exc:
        raise OllamaNotRunningError(config.ollama.host, exc) from exc
    finally:
        await client.aclose()
