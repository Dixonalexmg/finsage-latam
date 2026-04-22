"""Agente conversacional que extrae el perfil financiero del usuario.

``ProfileAnalyst`` es la única excepción a la regla ``temperature ≤ 0.3`` definida
en ``CLAUDE.md``: opera en modo conversacional y necesita variabilidad razonable
para repreguntar y reformular sin caer en respuestas mecánicas.
"""

from __future__ import annotations

import os
from importlib import resources

from src.agents.base import BaseAgent, ChatMessage, StructuredLLMClient
from src.models.schemas import UserProfile


def _load_system_prompt() -> str:
    return (
        resources.files("src.agents.prompts")
        .joinpath("profile_analyst.md")
        .read_text(encoding="utf-8")
    )


class ProfileAnalyst(BaseAgent):
    """Extrae ``UserProfile`` desde la conversación del usuario.

    Usa ``gemini-2.5-flash-lite`` por defecto porque la tarea es extracción/clasificación
    y no razonamiento profundo (ver ``CLAUDE.md`` §Stack).
    """

    DEFAULT_MODEL = os.getenv("FINSAGE_PROFILE_MODEL", "gemini-2.5-flash-lite")
    MAX_TEMPERATURE: float = 0.7  # excepción documentada en CLAUDE.md

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        client: StructuredLLMClient | None = None,
        temperature: float = 0.5,
    ) -> None:
        super().__init__(
            model=model,
            system_prompt=_load_system_prompt(),
            client=client,
            temperature=temperature,
            agent_name="ProfileAnalyst",
        )

    def extract_profile(self, conversation: list[ChatMessage]) -> UserProfile:
        """Extrae un ``UserProfile`` validado desde el historial conversacional."""
        return self.call(conversation, response_model=UserProfile)
