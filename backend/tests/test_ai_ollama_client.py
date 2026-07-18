"""Tests for `gramvault.ai.ollama_client`: the thin async HTTP wrapper
around a local Ollama server.

All Ollama HTTP calls are mocked with `respx` — no real Ollama server or
model is ever contacted.
"""

from __future__ import annotations

import base64
from pathlib import Path

import httpx
import pytest
import respx
from httpx import Response

from gramvault.ai.ollama_client import (
    DEFAULT_CAPTION_PROMPT,
    ModelNotPulledError,
    OllamaError,
    OllamaNotRunningError,
    caption_image,
    chat_completion,
    check_health,
    embed,
    ensure_model_pulled,
    ensure_running,
    generate_caption,
    is_model_pulled,
    stream_chat,
)
from gramvault.config import Config

HOST = "http://localhost:11434"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class TestExceptionMessages:
    def test_ollama_not_running_error_is_friendly_and_carries_host(self) -> None:
        exc = OllamaNotRunningError(HOST, cause=RuntimeError("boom"))
        assert isinstance(exc, OllamaError)
        assert HOST in str(exc)
        assert "ollama serve" in str(exc)
        assert exc.host == HOST

    def test_model_not_pulled_error_is_friendly_and_carries_model(self) -> None:
        exc = ModelNotPulledError("llava:7b")
        assert isinstance(exc, OllamaError)
        assert "llava:7b" in str(exc)
        assert "ollama pull llava:7b" in str(exc)
        assert exc.model == "llava:7b"


class TestCheckHealth:
    @pytest.mark.anyio
    @respx.mock
    async def test_true_when_ollama_responds_ok(self, tmp_config: Config) -> None:
        respx.get(f"{HOST}/api/tags").mock(return_value=Response(200, json={"models": []}))

        assert await check_health(tmp_config) is True

    @pytest.mark.anyio
    @respx.mock
    async def test_false_when_ollama_unreachable(self, tmp_config: Config) -> None:
        respx.get(f"{HOST}/api/tags").mock(side_effect=httpx.ConnectError("refused"))

        assert await check_health(tmp_config) is False

    @pytest.mark.anyio
    @respx.mock
    async def test_false_on_non_200_status(self, tmp_config: Config) -> None:
        respx.get(f"{HOST}/api/tags").mock(return_value=Response(500))

        assert await check_health(tmp_config) is False

    @pytest.mark.anyio
    @respx.mock
    async def test_does_not_raise_when_down(self, tmp_config: Config) -> None:
        respx.get(f"{HOST}/api/tags").mock(side_effect=httpx.ConnectError("refused"))
        # check_health() never raises, unlike ensure_running().
        result = await check_health(tmp_config)
        assert result is False


class TestEnsureRunning:
    @pytest.mark.anyio
    @respx.mock
    async def test_returns_none_when_up(self, tmp_config: Config) -> None:
        respx.get(f"{HOST}/api/tags").mock(return_value=Response(200, json={"models": []}))
        assert await ensure_running(tmp_config) is None

    @pytest.mark.anyio
    @respx.mock
    async def test_raises_friendly_error_when_down(self, tmp_config: Config) -> None:
        respx.get(f"{HOST}/api/tags").mock(side_effect=httpx.ConnectError("refused"))

        with pytest.raises(OllamaNotRunningError) as exc_info:
            await ensure_running(tmp_config)
        assert HOST in str(exc_info.value)


class TestIsModelPulled:
    @pytest.mark.anyio
    @respx.mock
    async def test_true_on_exact_match(self, tmp_config: Config) -> None:
        respx.get(f"{HOST}/api/tags").mock(
            return_value=Response(200, json={"models": [{"name": "llava:7b"}]})
        )
        assert await is_model_pulled("llava:7b", tmp_config) is True

    @pytest.mark.anyio
    @respx.mock
    async def test_true_on_bare_name_match(self, tmp_config: Config) -> None:
        # "llama3.1" (config-style bare name) should match a pulled
        # "llama3.1:8b" tag.
        respx.get(f"{HOST}/api/tags").mock(
            return_value=Response(200, json={"models": [{"name": "llama3.1:8b"}]})
        )
        assert await is_model_pulled("llama3.1", tmp_config) is True

    @pytest.mark.anyio
    @respx.mock
    async def test_false_when_not_pulled(self, tmp_config: Config) -> None:
        respx.get(f"{HOST}/api/tags").mock(
            return_value=Response(200, json={"models": [{"name": "mistral:7b"}]})
        )
        assert await is_model_pulled("llava:7b", tmp_config) is False

    @pytest.mark.anyio
    @respx.mock
    async def test_raises_not_running_on_connect_error(self, tmp_config: Config) -> None:
        respx.get(f"{HOST}/api/tags").mock(side_effect=httpx.ConnectError("refused"))

        with pytest.raises(OllamaNotRunningError):
            await is_model_pulled("llava:7b", tmp_config)


class TestEnsureModelPulled:
    @pytest.mark.anyio
    @respx.mock
    async def test_returns_none_when_pulled(self, tmp_config: Config) -> None:
        respx.get(f"{HOST}/api/tags").mock(
            return_value=Response(200, json={"models": [{"name": "llava:7b"}]})
        )
        assert await ensure_model_pulled("llava:7b", tmp_config) is None

    @pytest.mark.anyio
    @respx.mock
    async def test_raises_friendly_error_when_missing(self, tmp_config: Config) -> None:
        respx.get(f"{HOST}/api/tags").mock(return_value=Response(200, json={"models": []}))

        with pytest.raises(ModelNotPulledError) as exc_info:
            await ensure_model_pulled("llava:7b", tmp_config)
        assert "llava:7b" in str(exc_info.value)


class TestEmbed:
    @pytest.mark.anyio
    @respx.mock
    async def test_happy_path_returns_embedding(self, tmp_config: Config) -> None:
        route = respx.post(f"{HOST}/api/embeddings").mock(
            return_value=Response(200, json={"embedding": [0.1, 0.2, 0.3]})
        )

        result = await embed("hello world", config=tmp_config)

        assert result == [0.1, 0.2, 0.3]
        sent_body = route.calls.last.request.content
        import json as _json

        payload = _json.loads(sent_body)
        assert payload["prompt"] == "hello world"
        assert payload["model"] == tmp_config.models.embedding_model

    @pytest.mark.anyio
    @respx.mock
    async def test_uses_explicit_model_override(self, tmp_config: Config) -> None:
        route = respx.post(f"{HOST}/api/embeddings").mock(
            return_value=Response(200, json={"embedding": [1.0]})
        )

        await embed("text", model="custom-embed", config=tmp_config)

        import json as _json

        payload = _json.loads(route.calls.last.request.content)
        assert payload["model"] == "custom-embed"

    @pytest.mark.anyio
    @respx.mock
    async def test_connect_error_raises_not_running(self, tmp_config: Config) -> None:
        respx.post(f"{HOST}/api/embeddings").mock(side_effect=httpx.ConnectError("refused"))

        with pytest.raises(OllamaNotRunningError):
            await embed("hello", config=tmp_config)

    @pytest.mark.anyio
    @respx.mock
    async def test_404_raises_model_not_pulled(self, tmp_config: Config) -> None:
        respx.post(f"{HOST}/api/embeddings").mock(return_value=Response(404))

        with pytest.raises(ModelNotPulledError):
            await embed("hello", config=tmp_config)

    @pytest.mark.anyio
    @respx.mock
    async def test_other_http_error_propagates(self, tmp_config: Config) -> None:
        respx.post(f"{HOST}/api/embeddings").mock(return_value=Response(500))

        with pytest.raises(httpx.HTTPStatusError):
            await embed("hello", config=tmp_config)


class TestGenerateCaption:
    @pytest.mark.anyio
    @respx.mock
    async def test_happy_path_text_only(self, tmp_config: Config) -> None:
        route = respx.post(f"{HOST}/api/generate").mock(
            return_value=Response(200, json={"response": "a caption"})
        )

        result = await generate_caption("describe this", config=tmp_config)

        assert result == "a caption"
        import json as _json

        payload = _json.loads(route.calls.last.request.content)
        assert payload["prompt"] == "describe this"
        assert "images" not in payload
        assert payload["model"] == tmp_config.models.vision_model

    @pytest.mark.anyio
    @respx.mock
    async def test_happy_path_with_image_includes_images_field(self, tmp_config: Config) -> None:
        route = respx.post(f"{HOST}/api/generate").mock(
            return_value=Response(200, json={"response": "a photo of a cat"})
        )

        result = await generate_caption("describe", image_b64="ZmFrZQ==", config=tmp_config)

        assert result == "a photo of a cat"
        import json as _json

        payload = _json.loads(route.calls.last.request.content)
        assert payload["images"] == ["ZmFrZQ=="]

    @pytest.mark.anyio
    @respx.mock
    async def test_missing_response_field_defaults_to_empty_string(
        self, tmp_config: Config
    ) -> None:
        respx.post(f"{HOST}/api/generate").mock(return_value=Response(200, json={}))

        result = await generate_caption("describe", config=tmp_config)
        assert result == ""

    @pytest.mark.anyio
    @respx.mock
    async def test_connect_error_raises_not_running(self, tmp_config: Config) -> None:
        respx.post(f"{HOST}/api/generate").mock(side_effect=httpx.ConnectError("refused"))

        with pytest.raises(OllamaNotRunningError):
            await generate_caption("describe", config=tmp_config)

    @pytest.mark.anyio
    @respx.mock
    async def test_404_raises_model_not_pulled(self, tmp_config: Config) -> None:
        respx.post(f"{HOST}/api/generate").mock(return_value=Response(404))

        with pytest.raises(ModelNotPulledError):
            await generate_caption("describe", config=tmp_config)


class TestCaptionImage:
    @pytest.mark.anyio
    @respx.mock
    async def test_reads_and_base64_encodes_image_file(
        self, tmp_config: Config, tmp_path: Path
    ) -> None:
        image_path = tmp_path / "photo.jpg"
        image_bytes = b"\xff\xd8\xff\xe0fake jpeg bytes"
        image_path.write_bytes(image_bytes)

        route = respx.post(f"{HOST}/api/generate").mock(
            return_value=Response(200, json={"response": "a scenic photo"})
        )

        result = await caption_image(image_path, config=tmp_config)

        assert result == "a scenic photo"
        import json as _json

        payload = _json.loads(route.calls.last.request.content)
        assert payload["images"] == [base64.b64encode(image_bytes).decode("ascii")]
        assert payload["prompt"] == DEFAULT_CAPTION_PROMPT

    @pytest.mark.anyio
    @respx.mock
    async def test_custom_prompt_is_forwarded(self, tmp_config: Config, tmp_path: Path) -> None:
        image_path = tmp_path / "photo.jpg"
        image_path.write_bytes(b"bytes")
        route = respx.post(f"{HOST}/api/generate").mock(
            return_value=Response(200, json={"response": "ok"})
        )

        await caption_image(image_path, prompt="custom prompt", config=tmp_config)

        import json as _json

        payload = _json.loads(route.calls.last.request.content)
        assert payload["prompt"] == "custom prompt"


class TestChatCompletion:
    @pytest.mark.anyio
    @respx.mock
    async def test_happy_path_returns_content(self, tmp_config: Config) -> None:
        route = respx.post(f"{HOST}/api/chat").mock(
            return_value=Response(
                200, json={"message": {"role": "assistant", "content": "hi there"}}
            )
        )

        messages = [{"role": "user", "content": "hello"}]
        result = await chat_completion(messages, config=tmp_config)

        assert result == "hi there"
        import json as _json

        payload = _json.loads(route.calls.last.request.content)
        assert payload["messages"] == messages
        assert payload["stream"] is False
        assert payload["model"] == tmp_config.models.chat_model

    @pytest.mark.anyio
    @respx.mock
    async def test_connect_error_raises_not_running(self, tmp_config: Config) -> None:
        respx.post(f"{HOST}/api/chat").mock(side_effect=httpx.ConnectError("refused"))

        with pytest.raises(OllamaNotRunningError):
            await chat_completion([{"role": "user", "content": "hi"}], config=tmp_config)

    @pytest.mark.anyio
    @respx.mock
    async def test_404_raises_model_not_pulled(self, tmp_config: Config) -> None:
        respx.post(f"{HOST}/api/chat").mock(return_value=Response(404))

        with pytest.raises(ModelNotPulledError):
            await chat_completion([{"role": "user", "content": "hi"}], config=tmp_config)


class TestStreamChat:
    @pytest.mark.anyio
    @respx.mock
    async def test_yields_content_tokens_in_order(self, tmp_config: Config) -> None:
        ndjson = "\n".join(
            [
                '{"message": {"content": "Hello "}, "done": false}',
                '{"message": {"content": "world"}, "done": false}',
                '{"message": {"content": ""}, "done": true}',
            ]
        )
        respx.post(f"{HOST}/api/chat").mock(return_value=Response(200, content=ndjson))

        chunks = [
            chunk
            async for chunk in stream_chat(
                [{"role": "user", "content": "hi"}], config=tmp_config
            )
        ]

        assert chunks == ["Hello ", "world"]

    @pytest.mark.anyio
    @respx.mock
    async def test_connect_error_raises_before_yielding_anything(
        self, tmp_config: Config
    ) -> None:
        respx.post(f"{HOST}/api/chat").mock(side_effect=httpx.ConnectError("refused"))

        with pytest.raises(OllamaNotRunningError):
            async for _ in stream_chat([{"role": "user", "content": "hi"}], config=tmp_config):
                pass

    @pytest.mark.anyio
    @respx.mock
    async def test_404_raises_model_not_pulled(self, tmp_config: Config) -> None:
        respx.post(f"{HOST}/api/chat").mock(return_value=Response(404))

        with pytest.raises(ModelNotPulledError):
            async for _ in stream_chat([{"role": "user", "content": "hi"}], config=tmp_config):
                pass
