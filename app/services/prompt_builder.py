from pydantic import BaseModel, Field


class ProductSeoOutput(BaseModel):
    """Schéma de sortie structurée attendue pour l'enrichissement SEO."""
    """Schéma de sortie structurée pour l'enrichissement SEO."""

    seo_title: str = Field(
        description="Titre SEO optimisé pour les moteurs de recherche"
        description="Titre optimisé pour le référencement naturel (SEO), accrocheur et vendeur"
    )

    meta_description: str = Field(
        description="Meta description SEO de 150 à 160 caractères"
    )

    short_description: str = Field(
        description="Description courte destinée aux aperçus et catalogues e-commerce"
        description="Description courte résumant les atouts clés du produit pour le e-commerce"
    )

    long_description: str = Field(
        description="Description détaillée, persuasive et optimisée SEO"
        description="Description détaillée, fluide, persuasive et structurée du produit"
    )

    benefits: list[str] = Field(
        description="Liste des principaux avantages du produit"
    )

    specifications: list[str] = Field(
        description="Liste des caractéristiques ou spécifications du produit"
    )

    usage_tips: list[str] = Field(
        description="Conseils d'utilisation du produit"
    )

    seo_tags: list[str] = Field(
        description="Liste de mots-clés SEO pertinents"
        description="Liste de mots-clés et tags SEO e-commerce pertinents"
    )


class PromptBuilder:
    """Constructeur de prompts spécialisé pour l'enrichissement SEO e-commerce."""

    @staticmethod
    def build_system_instruction() -> str:
        return (
            "Tu es un expert e-commerce, SEO et copywriting. "
            "Tu rédiges des fiches produits professionnelles destinées à être publiées "
            "sur des marketplaces et des boutiques en ligne. "
            "Le contenu doit être naturel, vendeur, clair et optimisé pour le référencement naturel. "
            "Tu ne dois jamais inventer des caractéristiques techniques précises lorsqu'elles ne sont pas connues. "
            "Tu dois produire uniquement un JSON valide conforme au schéma demandé."
            "Tu es un expert en e-commerce et en rédaction SEO. "
            "Ton rôle est de générer des fiches produits attrayantes, professionnelles, "
            "optimisées pour le référencement naturel (SEO), en français. "
            "Tu dois respecter strictement le schéma JSON demandé sans inventer d'allégations fausses."
        )

    @staticmethod
    def build_prompt(product_name: str) -> str:
        return f"""
Produit : {product_name}

Génère une fiche produit e-commerce professionnelle en français.

Retourne les champs suivants :

- seo_title
- meta_description
- short_description
- long_description
- benefits
- specifications
- usage_tips
- seo_tags

Consignes :

- Le titre SEO doit être accrocheur.
- La meta description doit faire entre 150 et 160 caractères.
- La description courte doit résumer rapidement les avantages du produit.
- La description longue doit être détaillée, persuasive et orientée vente.
- benefits doit contenir 3 à 7 avantages.
- specifications doit contenir 3 à 10 caractéristiques générales.
- usage_tips doit contenir 2 à 5 conseils utiles.
- seo_tags doit contenir 5 à 15 mots-clés SEO.
- Ne jamais inventer des données techniques précises lorsqu'elles ne sont pas connues.
- Adapter le contenu au commerce en ligne.
"""
        return (
            f"Produit : {product_name}\n\n"
            "Génère la fiche SEO e-commerce complète comprenant exactement les champs suivants :\n"
            "- seo_title : titre SEO percutant\n"
            "- short_description : courte description vendeuse\n"
            "- long_description : description détaillée, fluide et engageante\n"
            "- seo_tags : liste de 5 à 10 mots-clés SEO pertinents"
        )