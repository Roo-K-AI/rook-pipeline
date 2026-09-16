import logging
from datetime import UTC, datetime
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.pipeline.orchestrator import JobOrchestrator, get_job_orchestrator
from app.core.config import settings
from app.core.security import verify_service_auth
from app.schemas.enrichment import (
    Content,
    Enrichment,
    Generation,
    Identity,
    JobStatus,
    ProductEnrichmentAck,
    ProductEnrichmentRequest,
    ProductEnrichmentResult,
    ProductEnrichmentStatus,
    Seo,
    Specifications,
    Validation,
)
from app.services.job_registry import (
    IdempotencyConflictError,
    InMemoryJobRegistry,
    InvalidJobTransitionError,
    get_job_registry,
)
from app.services.product_generator import (
    ProductGenerator,
    get_product_generator,
    slugify,
)
from app.services.prompt_builder import ProductSeoOutput

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/v1/product-enrichments", tags=["product-enrichments"])


class DirectProductRequest(BaseModel):
    """Requête directe de génération SEO sans passage par la file de jobs."""
    product_name: str = Field(min_length=1, max_length=255, description="Nom du produit à enrichir")


async def execute_job_generation(
    job_id: str,
    registry: InMemoryJobRegistry,
    generator: ProductGenerator,
) -> None:
    """Tâche d'arrière-plan exécutant la génération réelle avec Gemini."""
    job = registry.get_job(job_id)
    if not job:
        logger.error("Job %s introuvable dans le registre.", job_id)
        return

    try:
        registry.update_status(
            job_id=job_id,
            status=JobStatus.generating,
            current_step="generating_content",
            progress_percent=50,
        )

        product_name = job.request_payload.get("product_name", "")
        seo_data = generator.generate(product_name)

        now = datetime.now(UTC)
        enrichment = Enrichment(
            identity=Identity(
                name=product_name,
                brand=job.request_payload.get("brand_hint"),
                category=job.request_payload.get("category_hint"),
                sku=None,
            ),
            content=Content(
                title=seo_data.get("seo_title", product_name),
                short_description=seo_data.get("short_description"),
                long_description=seo_data.get("long_description"),
                bullet_points=[],
                benefits=[],
                usage=None,
                target_audience=None,
            ),
            specifications=Specifications(
                attributes=[],
                package_contents=[],
                compatibility=[],
            ),
            seo=Seo(
                meta_title=seo_data.get("seo_title", product_name),
                meta_description=seo_data.get("short_description"),
                slug=slugify(product_name),
                keywords=seo_data.get("seo_tags", []),
            ),
        )

        result = ProductEnrichmentResult(
            schema_version="product-enrichment-result.v1",
            job_id=job.job_id,
            product_id=job.product_id,
            status=JobStatus.completed,
            pipeline_version=settings.pipeline_version,
            enrichment=enrichment,
            sources=[],
            warnings=[],
            errors=[],
            validation=Validation(
                schema_valid=True,
                fact_status="verified",
                requires_human_review=False,
            ),
            generation=Generation(
                provider="gemini",
                model=settings.gemini_model,
                prompt_version="v1.0",
                generated_at=now,
            ),
            completed_at=now,
        )

        registry.update_status(
            job_id=job_id,
            status=JobStatus.completed,
            current_step="finished",
            progress_percent=100,
            result=result,
        )
        logger.info("Job %s complété avec succès par Gemini.", job_id)

    except Exception as exc:
        logger.exception("Échec de génération pour le job %s : %s", job_id, exc)
        registry.update_status(
            job_id=job_id,
            status=JobStatus.failed,
            current_step="failed",
            progress_percent=100,
            error_code="gemini_generation_failed",
            error_message=str(exc),
            validate_transition=False,
        )


@router.post("/generate", response_model=ProductSeoOutput)
def generate_seo_direct(
    payload: DirectProductRequest,
    authorization: str | None = Header(default=None),
    generator: ProductGenerator = Depends(get_product_generator),
) -> ProductSeoOutput:
    """Génération directe et synchrone d'une fiche SEO e-commerce via Gemini."""
    verify_service_auth(authorization)
    result = generator.generate(payload.product_name)
    return ProductSeoOutput.model_validate(result)


@router.post("", response_model=ProductEnrichmentAck, status_code=status.HTTP_202_ACCEPTED)
def create_product_enrichment(
    payload: ProductEnrichmentRequest,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    authorization: str | None = Header(default=None),
    correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    registry: InMemoryJobRegistry = Depends(get_job_registry),
    orchestrator: JobOrchestrator = Depends(get_job_orchestrator),
    generator: ProductGenerator = Depends(get_product_generator),
) -> ProductEnrichmentAck:
    """Création asynchrone d'un job d'enrichissement de produit."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="service_authentication_failed",
        )
    if not correlation_id or not idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="request_validation_failed",
        )

    actual_registry = registry if isinstance(registry, InMemoryJobRegistry) else get_job_registry()
    actual_orchestrator = orchestrator if isinstance(orchestrator, JobOrchestrator) else get_job_orchestrator()
    actual_generator = generator if isinstance(generator, ProductGenerator) else get_product_generator()

    try:
        job, created = actual_registry.create_or_get_job(
            payload=payload,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="idempotency_conflict",
        ) from exc

    if created:
        background_tasks.add_task(actual_orchestrator.run_pipeline, job.job_id, actual_registry)
        background_tasks.add_task(execute_job_generation, job.job_id, actual_registry, actual_generator)

    return job.to_ack()


@router.get("/jobs/{job_id}", response_model=ProductEnrichmentStatus)
def get_product_enrichment(
    job_id: str,
    registry: InMemoryJobRegistry = Depends(get_job_registry),
) -> ProductEnrichmentStatus:
    """Consultation de l'état et du résultat d'un job."""
    actual_registry = registry if isinstance(registry, InMemoryJobRegistry) else get_job_registry()
    job = actual_registry.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job_not_found")
    return job.to_status()


@router.post("/jobs/{job_id}/retry", response_model=ProductEnrichmentAck, status_code=status.HTTP_202_ACCEPTED)
def retry_product_enrichment(
    job_id: str,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    authorization: str | None = Header(default=None),
    correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    registry: InMemoryJobRegistry = Depends(get_job_registry),
    orchestrator: JobOrchestrator = Depends(get_job_orchestrator),
    generator: ProductGenerator = Depends(get_product_generator),
) -> ProductEnrichmentAck:
    """Relance un job en échec."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="service_authentication_failed",
        )
    if not correlation_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="request_validation_failed",
        )

    actual_registry = registry if isinstance(registry, InMemoryJobRegistry) else get_job_registry()
    actual_orchestrator = orchestrator if isinstance(orchestrator, JobOrchestrator) else get_job_orchestrator()
    actual_generator = generator if isinstance(generator, ProductGenerator) else get_product_generator()

    job = actual_registry.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job_not_found")

    try:
        updated_job = actual_registry.reset_for_retry(job_id)
    except InvalidJobTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    background_tasks.add_task(actual_orchestrator.run_pipeline, updated_job.job_id, actual_registry)
    background_tasks.add_task(execute_job_generation, updated_job.job_id, actual_registry, actual_generator)
    return updated_job.to_ack()

