import unittest
from unittest.mock import MagicMock

from app.services.openai_client import OpenAIClient, OpenAIClientError
from app.services.product_generator import ProductGenerator
from app.services.prompt_builder import ProductSeoOutput, PromptBuilder


class TestGeminiServices(unittest.TestCase):
    def test_prompt_builder(self) -> None:
        sys_inst = PromptBuilder.build_system_instruction()
        self.assertIn("expert", sys_inst.lower())
        self.assertIn("seo", sys_inst.lower())

        prompt = PromptBuilder.build_prompt("Litière à chat")
        self.assertIn("Litière à chat", prompt)

    def test_product_seo_output_schema(self) -> None:
        data = {
            "seo_title": "Litière Chat Absorbante",
            "short_description": "Litière de qualité supérieure.",
            "long_description": "Description détaillée et complète pour litière féline.",
            "seo_tags": ["chat", "litière", "hygiène"],
        }
        output = ProductSeoOutput.model_validate(data)
        self.assertEqual(output.seo_title, "Litière Chat Absorbante")
        self.assertEqual(len(output.seo_tags), 3)

    def test_gemini_client_missing_key(self) -> None:
        with self.assertRaises(GeminiClientError):
            GeminiClient(api_key="")

    def test_product_generator_mocked(self) -> None:
        mock_client = MagicMock()
        mock_client.generate_structured.return_value = {
            "seo_title": "Litière pour Chat Haute Performance",
            "short_description": "Une litière absorbante et propre.",
            "long_description": "Une litière idéale pour le confort de votre chat au quotidien.",
            "seo_tags": ["litiere", "chat", "hygiene"],
        }

        generator = ProductGenerator(openai_client=mock_client)
        result = generator.generate("Litière à chat")

        self.assertIn("seo_title", result)
        self.assertIn("short_description", result)
        self.assertIn("long_description", result)
        self.assertIn("seo_tags", result)
        self.assertEqual(result["seo_title"], "Litière pour Chat Haute Performance")
        self.assertEqual(len(result["seo_tags"]), 3)

    def test_product_generator_empty_name(self) -> None:
        generator = ProductGenerator(gemini_client=MagicMock())
        with self.assertRaises(ValueError):
            generator.generate("   ")


if __name__ == "__main__":
    unittest.main()

