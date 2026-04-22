"""Clientes REST livianos para Gemini generation y embeddings.

Se usan para mantener la demo publica sobre el free tier de Gemini sin agregar
otro SDK al runtime. La implementacion sigue la API REST oficial de
``generateContent`` y ``embedContent``.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GENERATION_MODEL = "gemini-2.5-flash-lite"
DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"
DEFAULT_TIMEOUT_SECONDS = 30


class GeminiAPIError(RuntimeError):
    """Error devuelto por Gemini API."""


@dataclass(frozen=True)
class GeminiEmbeddingResponse:
    embeddings: list[list[float]]


class GeminiStructuredClient:
    """Cliente de Gemini con structured output basado en JSON Schema."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = DEFAULT_GEMINI_BASE_URL,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key or os.getenv("GEMINI_API_KEY") or ""
        if not self._api_key:
            raise RuntimeError("Falta GEMINI_API_KEY para llamar Gemini API.")
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def generate_json(
        self,
        *,
        model: str,
        system_prompt: str,
        messages: Sequence[Mapping[str, str]],
        json_schema: dict[str, Any],
        temperature: float,
        max_tokens: int,
    ) -> str:
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [_to_gemini_message(message) for message in messages],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
                "responseJsonSchema": json_schema,
            },
        }
        data = self._post_json(f"/models/{model}:generateContent", payload)
        try:
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as err:
            raise GeminiAPIError(f"Respuesta inesperada de Gemini generation: {data}") from err
        text_parts = [part.get("text", "") for part in parts if isinstance(part, dict)]
        if not text_parts:
            raise GeminiAPIError(f"Gemini no devolvio texto JSON en candidates: {data}")
        return "".join(text_parts)

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url=f"{self._base_url}{path}",
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self._api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                return cast(dict[str, Any], json.loads(response.read().decode("utf-8")))
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", errors="replace")
            raise GeminiAPIError(_extract_error_message(detail, err.code)) from err
        except urllib.error.URLError as err:
            raise GeminiAPIError(f"No pude alcanzar Gemini API: {err}") from err


class GeminiEmbeddingClient:
    """Cliente compatible con ``EmbeddingClient`` usando `embedContent`."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = DEFAULT_GEMINI_BASE_URL,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key or os.getenv("GEMINI_API_KEY") or ""
        if not self._api_key:
            raise RuntimeError("Falta GEMINI_API_KEY para generar embeddings con Gemini.")
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def embed(
        self,
        texts: list[str],
        model: str | None = None,
        input_type: str | None = None,
    ) -> GeminiEmbeddingResponse:
        model_name = model or DEFAULT_EMBEDDING_MODEL
        embeddings: list[list[float]] = []
        for text in texts:
            formatted = _format_embedding_text(text=text, input_type=input_type)
            payload = {
                "model": f"models/{model_name}",
                "content": {
                    "parts": [{"text": formatted}],
                },
            }
            data = self._post_json(f"/models/{model_name}:embedContent", payload)
            try:
                values = data["embedding"]["values"]
            except (KeyError, TypeError) as err:
                raise GeminiAPIError(f"Respuesta inesperada de Gemini embeddings: {data}") from err
            embeddings.append([float(value) for value in values])
        return GeminiEmbeddingResponse(embeddings=embeddings)

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url=f"{self._base_url}{path}",
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self._api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                return cast(dict[str, Any], json.loads(response.read().decode("utf-8")))
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", errors="replace")
            raise GeminiAPIError(_extract_error_message(detail, err.code)) from err
        except urllib.error.URLError as err:
            raise GeminiAPIError(f"No pude alcanzar Gemini embeddings API: {err}") from err


def _to_gemini_message(message: Mapping[str, str]) -> dict[str, Any]:
    role = message.get("role", "user")
    gemini_role = "model" if role == "assistant" else role
    return {
        "role": gemini_role,
        "parts": [{"text": message.get("content", "")}],
    }


def _format_embedding_text(*, text: str, input_type: str | None) -> str:
    normalized = text.strip()
    if input_type == "query":
        return f"task: search result | query: {normalized}"
    if input_type == "document":
        return f"title: none | text: {normalized}"
    return normalized


def _extract_error_message(detail: str, status_code: int) -> str:
    try:
        payload = json.loads(detail)
    except json.JSONDecodeError:
        return f"Gemini API error {status_code}: {detail}"
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        status = error.get("status")
        if message and status:
            return f"Gemini API error {status_code} ({status}): {message}"
        if message:
            return f"Gemini API error {status_code}: {message}"
    return f"Gemini API error {status_code}: {detail}"
