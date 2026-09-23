import json
import logging
from typing import Any, Type

from openai import OpenAI
from pydantic import BaseModel

from app.core.config import settings

logger = logging.getLogger(__name__)


class OpenAIClient:
    def __init__(self):
        self.client = OpenAI(
            api_key=settings.openai_api_key
        )

    def generate(self, prompt: str) -> str:
        response = self.client.responses.create(
            model=settings.openai_model,
            input=prompt,
        )

        return response.output_text

    def generate_structured(
        self,
        contents: str,
        response_schema: Type[BaseModel],
        system_instruction: str | None = None,
    ) -> dict[str, Any]:

        messages = []

        if system_instruction:
            messages.append({
                "role": "system",
                "content": system_instruction,
            })

        messages.append({
            "role": "user",
            "content": contents,
        })

        response = self.client.chat.completions.create(
            model=settings.openai_model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=0.7,
        )

        content = response.choices[0].message.content

        if not content:
            raise ValueError(
                "Réponse vide reçue d'OpenAI."
            )

        data = json.loads(content)

        validated = response_schema.model_validate(
            data
        )

        return validated.model_dump()