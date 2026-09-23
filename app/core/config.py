from dotenv import load_dotenv

load_dotenv()

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    service_auth_token: str = field(
        default_factory=lambda: os.getenv(
            "ROOK_SERVICE_TOKEN",
            "rook-internal-secret-token"
        )
    )

    gemini_api_key: str = field(
        default_factory=lambda: os.getenv(
            "GEMINI_API_KEY",
            ""
        )
    )

    gemini_model: str = field(
        default_factory=lambda: os.getenv(
            "GEMINI_MODEL",
            "gemini-3.6-flash"
        )
    )

    openai_api_key: str = field(
        default_factory=lambda: os.getenv(
            "OPENAI_API_KEY",
            ""
        )
    )

    openai_model: str = field(
        default_factory=lambda: os.getenv(
            "OPENAI_MODEL",
            "gpt-5"
        )
    )

    pipeline_version: str = "product-enrichment.v1"
    default_max_attempts: int = 3

    step_delay_seconds: float = field(
        default_factory=lambda: float(
            os.getenv("PIPELINE_STEP_DELAY", "0.05")
        )
    )

    enforce_auth: bool = field(
        default_factory=lambda: os.getenv(
            "ENFORCE_SERVICE_AUTH",
            "false"
        ).lower() in ("true", "1", "yes")
    )
print("OPENAI_API_KEY =", os.getenv("OPENAI_API_KEY"))
settings = Settings()