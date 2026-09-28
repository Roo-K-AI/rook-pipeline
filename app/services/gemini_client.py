import json
import logging
import os
import time
from typing import Any, Type

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel

from app.core.config import settings

# Chargement automatique des variables d'environnement (.env)
load_dotenv()

logger = logging.getLogger(__name__)


class GeminiClientError(Exception):
    """Exception personnalisée pour les erreurs du client Gemini."""
    pass


class GeminiClient:
    """Client wrapper autour du SDK officiel google-genai pour ROOK."""

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str | None = None,
    ) -> None:

        if api_key is not None:
            resolved_key = api_key.strip()
        else:
            resolved_key = (
                settings.gemini_api_key
                or os.getenv("GEMINI_API_KEY")
                or ""
            ).strip()

        if not resolved_key:
            raise GeminiClientError(
                "GEMINI_API_KEY n'est pas définie dans l'environnement ni dans le fichier .env."
            )

        self.api_key = resolved_key
        self.default_model = default_model or settings.gemini_model

        self.client = genai.Client(
            api_key=self.api_key
        )

    def generate_structured(
        self,
        contents: str,
        response_schema: Type[BaseModel],
        system_instruction: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_retries: int = 3,
        base_backoff: float = 2.0,
    ) -> dict[str, Any]:
        """
        Génère du contenu structuré conforme à un schéma Pydantic.
        Gère automatiquement les erreurs temporaires Gemini (429 / 503).
        """

        target_model = model or self.default_model

        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=response_schema,
            system_instruction=system_instruction,
            temperature=temperature,
        )

        last_error: Exception | None = None

        for attempt in range(1, max_retries + 1):
            try:
                response = self.client.models.generate_content(
                    model=target_model,
                    contents=contents,
                    config=config,
                )

                if not response.text:
                    raise GeminiClientError(
                        "Réponse vide reçue de Gemini."
                    )

                return json.loads(response.text)

            except Exception as exc:
                last_error = exc
                error_message = str(exc)

                logger.warning(
                    "Tentative %d/%d échouée pour %s : %s",
                    attempt,
                    max_retries,
                    target_model,
                    error_message,
                )

                if (
                    "503" in error_message
                    or "429" in error_message
                    or "UNAVAILABLE" in error_message
                ):
                    sleep_time = base_backoff * (2 ** (attempt - 1))
                    logger.warning(
                        "Gemini temporairement indisponible. Nouvelle tentative dans %.1f secondes.",
                        sleep_time,
                    )
                    time.sleep(sleep_time)

                elif attempt < max_retries:
                    time.sleep(base_backoff)

                else:
                    break

        raise GeminiClientError(
            f"Échec de génération Gemini après {max_retries} tentatives : {last_error}"
        ) from last_error