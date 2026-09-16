from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class JobStatus(str, Enum):
    accepted = "accepted"
    queued = "queued"
    collecting = "collecting"
    extracting = "extracting"
    normalizing = "normalizing"
    building_context = "building_context"
    generating = "generating"
    validating = "validating"
    completed = "completed"
    needs_review = "needs_review"
    failed = "failed"
    timed_out = "timed_out"
    cancelled = "cancelled"


class RequestedBy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    tenant_id: str | None = None


class CallbackConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    event_type: str = "product.enrichment.completed"


class ProductEnrichmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["product-enrichment-request.v1"]
    job_id: str | None = None
    product_id: str | None = None
    product_name: str = Field(min_length=3, max_length=255)
    source_url: HttpUrl | None = None
    brand_hint: str | None = None
    category_hint: str | None = None
    locale: str = "fr-FR"
    target_market: str | None = None
    tone: str | None = None
    required_fields: list[str] = Field(default_factory=list)
    requested_by: RequestedBy | None = None
    callback: CallbackConfig | None = None


class Identity(BaseModel):
    name: str
    brand: str | None = None
    category: str | None = None
    sku: str | None = None


class Content(BaseModel):
    title: str
    short_description: str | None = None
    long_description: str | None = None
    bullet_points: list[str] = Field(default_factory=list)
    benefits: list[str] = Field(default_factory=list)
    usage: str | None = None
    target_audience: str | None = None


class Specifications(BaseModel):
    attributes: list[dict[str, object]] = Field(default_factory=list)
    package_contents: list[str] = Field(default_factory=list)
    compatibility: list[str] = Field(default_factory=list)


class Seo(BaseModel):
    meta_title: str
    meta_description: str | None = None
    slug: str
    keywords: list[str] = Field(default_factory=list)


class Enrichment(BaseModel):
    identity: Identity
    content: Content
    specifications: Specifications
    seo: Seo


class Validation(BaseModel):
    schema_valid: bool
    fact_status: str
    requires_human_review: bool


class Generation(BaseModel):
    provider: str
    model: str
    prompt_version: str
    generated_at: datetime


class ProductEnrichmentResult(BaseModel):
    schema_version: Literal["product-enrichment-result.v1"]
    job_id: str
    product_id: str
    status: JobStatus
    pipeline_version: str
    enrichment: Enrichment
    sources: list[dict[str, object]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    validation: Validation
    generation: Generation
    completed_at: datetime


class ProductEnrichmentAck(BaseModel):
    schema_version: Literal["product-enrichment-ack.v1"]
    job_id: str
    product_id: str
    status: JobStatus
    current_step: str
    correlation_id: str
    pipeline_version: str
    status_url: str
    accepted_at: datetime


class ProductEnrichmentStatus(ProductEnrichmentAck):
    created_at: datetime
    result: ProductEnrichmentResult | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "ready"]
