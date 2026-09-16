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
        default_model: str = "gemini-3.6-flash",
        default_model: str | None = None,
    ) -> None:
        if api_key is not None:
            resolved_key = api_key.strip()
        else:
            resolved_key = (os.getenv("GEMINI_API_KEY") or "").strip()
            resolved_key = (settings.gemini_api_key or os.getenv("GEMINI_API_KEY") or "").strip()

        if not resolved_key:
            raise GeminiClientError(
                "GEMINI_API_KEY n'est pas définie dans l'environnement ni dans le fichier .env."
            )

        self.api_key = resolved_key

        self.default_model = default_model
        self.default_model = default_model or settings.gemini_model
        self.client = genai.Client(api_key=self.api_key)

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
        Génère du contenu conforme à un schéma Pydantic avec le modèle spécifié.
        Génère du contenu conforme à un schéma Pydantic avec le modèle Gemini configuré.
        Intègre une stratégie de réessai exponentiel face aux saturations temporaires (503 / 429).
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
                    raise GeminiClientError("Réponse vide reçue de Gemini.")

                return json.loads(response.text)

            except Exception as exc:
                last_error = exc
                err_msg = str(exc)
                logger.warning(
                    "Tentative %d/%d échouée pour le modèle %s : %s",
                    attempt,
                    max_retries,
                    target_model,
                    err_msg,
                )

                # Si c'est une erreur 503 (haute demande temporaire) ou 429 (rate limit), on attend
                # Gestion d'indisponibilité temporaire (503) ou limitation de débit (429)
                if "503" in err_msg or "429" in err_msg or "UNAVAILABLE" in err_msg:
                    sleep_time = base_backoff * (2 ** (attempt - 1))
                    time.sleep(sleep_time)
                elif attempt < max_retries:
                    time.sleep(base_backoff)
                else:
                    break

        raise GeminiClientError(
            f"Échec de génération Gemini après {max_retries} tentatives : {last_error}"
        ) from last_error
