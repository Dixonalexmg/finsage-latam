"""Wrapper comun sobre Gemini con structured output Pydantic obligatorio.

Toda llamada a un LLM en FinSage pasa por ``BaseAgent.call`` para garantizar que
la salida valida contra un schema Pydantic, sin depender del SDK de un
proveedor concreto en la capa de agentes.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Protocol, TypedDict, TypeVar, cast

from pydantic import BaseModel, ValidationError

from src.llm.gemini import GeminiStructuredClient

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class ChatMessage(TypedDict):
    """Mensaje minimo soportado por la capa de agentes."""

    role: Literal["user", "assistant", "model"]
    content: str


class StructuredOutputError(RuntimeError):
    """El modelo no devolvio una salida estructurada valida para el schema solicitado."""


class StructuredLLMClient(Protocol):
    """Contrato minimo para clientes LLM con soporte de structured output."""

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
        """Devuelve una cadena JSON que debe validar contra ``json_schema``."""


class BaseAgent:
    """Base para agentes que llaman a un proveedor LLM con salida estructurada."""

    DEFAULT_MAX_TOKENS = 1024
    MAX_TEMPERATURE: float = 0.3
    """Tope para agentes de produccion (CLAUDE.md). ``ProfileAnalyst`` lo eleva."""

    def __init__(
        self,
        *,
        model: str,
        system_prompt: str,
        client: StructuredLLMClient | None = None,
        temperature: float = 0.0,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        agent_name: str | None = None,
    ) -> None:
        if not 0.0 <= temperature <= self.MAX_TEMPERATURE:
            raise ValueError(
                f"temperature={temperature} fuera del rango [0, {self.MAX_TEMPERATURE}] "
                f"permitido para {type(self).__name__}"
            )
        self.model = model
        self.system_prompt = system_prompt
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.agent_name = agent_name or type(self).__name__
        self.client = client

    def call(
        self,
        messages: list[ChatMessage],
        response_model: type[T],
        *,
        max_tokens: int | None = None,
    ) -> T:
        """Invoca al modelo y devuelve una instancia validada de ``response_model``."""
        schema = self._build_response_schema(response_model)
        client = self.client or GeminiStructuredClient()
        raw_json = client.generate_json(
            model=self.model,
            system_prompt=self.system_prompt,
            messages=cast(Sequence[Mapping[str, str]], messages),
            json_schema=schema,
            temperature=self.temperature,
            max_tokens=max_tokens or self.max_tokens,
        )
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError as err:
            logger.warning("%s devolvio JSON invalido: %s", self.agent_name, raw_json)
            detail = f"{self.agent_name}: el modelo no devolvio JSON valido"
            if _looks_like_truncated_json(raw_json, err):
                detail += " (salida truncada; reduje el payload pero conviene revisar max_tokens)"
            raise StructuredOutputError(
                detail
            ) from err

        try:
            return response_model.model_validate(payload)
        except ValidationError as err:
            logger.warning(
                "structured output validation failed for %s: %s",
                self.agent_name,
                err.errors(),
            )
            raise StructuredOutputError(
                f"{self.agent_name}: salida no valida contra {response_model.__name__}"
            ) from err

    @staticmethod
    def _build_response_schema(response_model: type[BaseModel]) -> dict[str, Any]:
        raw_schema = response_model.model_json_schema()
        defs = raw_schema.pop("$defs", {})
        return _normalize_gemini_schema(raw_schema, defs)


_ALLOWED_SCHEMA_KEYS = {
    "type",
    "title",
    "description",
    "properties",
    "required",
    "additionalProperties",
    "enum",
    "format",
    "minimum",
    "maximum",
    "items",
    "prefixItems",
    "minItems",
    "maxItems",
    "propertyOrdering",
}


def _normalize_gemini_schema(schema: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    normalized = _resolve_schema_refs(schema, defs)
    return cast(dict[str, Any], _strip_schema_to_gemini_subset(normalized))


def _resolve_schema_refs(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, list):
        return [_resolve_schema_refs(item, defs) for item in node]
    if not isinstance(node, dict):
        return node

    if "$ref" in node:
        ref = node["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/$defs/"):
            raise ValueError(f"Referencia JSON Schema no soportada: {ref!r}")
        def_name = ref.removeprefix("#/$defs/")
        resolved = defs.get(def_name)
        if resolved is None:
            raise ValueError(f"No encontre la definicion {def_name!r} en $defs")
        merged = {**_resolve_schema_refs(resolved, defs), **{k: v for k, v in node.items() if k != "$ref"}}
        return _resolve_schema_refs(merged, defs)

    if "anyOf" in node:
        variants = [_resolve_schema_refs(item, defs) for item in node["anyOf"]]
        non_null = [variant for variant in variants if variant.get("type") != "null"]
        null_variants = [variant for variant in variants if variant.get("type") == "null"]
        if len(non_null) == 1 and len(null_variants) == 1:
            merged = dict(non_null[0])
            base_type = merged.get("type")
            if isinstance(base_type, list):
                merged["type"] = [*base_type, "null"]
            elif base_type is not None:
                merged["type"] = [base_type, "null"]
            else:
                merged["type"] = ["null"]
            return _resolve_schema_refs(merged, defs)
        return {"type": "string", "description": node.get("description", "Union schema simplificado.")}

    return {key: _resolve_schema_refs(value, defs) for key, value in node.items() if key != "$defs"}


def _strip_schema_to_gemini_subset(node: Any) -> Any:
    if isinstance(node, list):
        return [_strip_schema_to_gemini_subset(item) for item in node]
    if not isinstance(node, dict):
        return node

    cleaned: dict[str, Any] = {}
    for key, value in node.items():
        if key not in _ALLOWED_SCHEMA_KEYS:
            continue
        if key == "properties" and isinstance(value, dict):
            cleaned[key] = {
                property_name: _strip_schema_to_gemini_subset(property_schema)
                for property_name, property_schema in value.items()
            }
            continue
        cleaned[key] = _strip_schema_to_gemini_subset(value)

    if cleaned.get("type") == "object":
        properties = cleaned.get("properties")
        if isinstance(properties, dict):
            cleaned["propertyOrdering"] = list(properties.keys())
    return cleaned


def _looks_like_truncated_json(raw_json: str, err: json.JSONDecodeError) -> bool:
    stripped = raw_json.rstrip()
    if not stripped:
        return False
    if "Unterminated" in err.msg:
        return True
    if err.pos >= max(0, len(raw_json) - 40):
        return True
    return stripped[-1] not in {"}", "]"}
