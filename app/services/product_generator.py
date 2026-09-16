import logging
import re
import unicodedata
from typing import Any

from app.services.gemini_client import GeminiClient
from app.services.prompt_builder import ProductSeoOutput, PromptBuilder

logger = logging.getLogger(__name__)


def slugify(text: str) -> str:
    """Génère un slug URL propre et normalisé."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[-\s]+", "-", text).strip("-")


class ProductGenerator:
    """Service d'enrichissement et de génération de fiches produits SEO avec Gemini."""

    def __init__(
        self,
        gemini_client: GeminiClient | None = None,
        prompt_builder: PromptBuilder | None = None,
    ) -> None:
        self.gemini_client = gemini_client or GeminiClient()
        self.prompt_builder = prompt_builder or PromptBuilder()

    def generate(self, product_name: str) -> dict[str, Any]:
        """
        Génère une fiche produit SEO complète à partir d'un nom de produit.

        Entrée :
            product_name: str (ex: "Litière à chat")

        Sortie :
            dict contenant seo_title, short_description, long_description, seo_tags
        """
        cleaned_name = product_name.strip()
        if not cleaned_name:
            raise ValueError("Le nom du produit ne peut pas être vide.")

        system_instruction = self.prompt_builder.build_system_instruction()
        prompt = self.prompt_builder.build_prompt(cleaned_name)

        logger.info("Génération du contenu SEO pour le produit : %s", cleaned_name)
        logger.info("Génération du contenu SEO avec Gemini pour : %s", cleaned_name)

        result = self.gemini_client.generate_structured(
            contents=prompt,
            response_schema=ProductSeoOutput,
            system_instruction=system_instruction,
        )

        return result


# Instance singleton et fonction utilitaire directe
_generator_instance: ProductGenerator | None = None


def get_product_generator() -> ProductGenerator:
    """Retourne l'instance singleton du ProductGenerator."""
    global _generator_instance
    if _generator_instance is None:
        _generator_instance = ProductGenerator()
    return _generator_instance


def generate_product_seo(product_name: str) -> dict[str, Any]:
    """Point d'entrée direct pour générer une fiche produit SEO."""
    generator = get_product_generator()
    return generator.generate(product_name)

