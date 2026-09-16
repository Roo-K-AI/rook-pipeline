import unittest
from datetime import UTC, datetime

from fastapi import HTTPException

from app.routers.enrichments import create_product_enrichment, get_product_enrichment
from app.schemas.enrichment import (
    Enrichment,
    Generation,
    Identity,
    Content,
    JobStatus,
    ProductEnrichmentRequest,
    ProductEnrichmentResult,
    Seo,
    Specifications,
    Validation,
)
from app.services.job_registry import (
    IdempotencyConflictError,
    InMemoryJobRegistry,
    InvalidJobTransitionError,
)


def _make_request(product_name: str = "Test Product", **kwargs) -> ProductEnrichmentRequest:
    return ProductEnrichmentRequest(
        schema_version="product-enrichment-request.v1",
        product_name=product_name,
        **kwargs,
    )


class TestInMemoryJobRegistry(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = InMemoryJobRegistry()

    def test_create_and_get_job(self) -> None:
        payload = _make_request(product_name="Croquettes pour chat")
        job, created = self.registry.create_or_get_job(
            payload=payload,
            correlation_id="corr_123",
            idempotency_key="idem_123",
        )

        self.assertTrue(created)
        self.assertEqual(job.status, JobStatus.accepted)
        self.assertEqual(job.current_step, "queued")
        self.assertEqual(job.correlation_id, "corr_123")
        self.assertEqual(job.idempotency_key, "idem_123")
        self.assertIsNotNone(job.job_id)
        self.assertIsNotNone(job.product_id)

        # Retrieval by job_id
        retrieved = self.registry.get_job(job.job_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.job_id, job.job_id)

        # Retrieval by idempotency_key
        by_idem = self.registry.get_by_idempotency_key("idem_123")
        self.assertIsNotNone(by_idem)
        self.assertEqual(by_idem.job_id, job.job_id)

    def test_idempotency_same_payload_returns_existing(self) -> None:
        payload = _make_request(product_name="Croquettes pour chat")
        job1, created1 = self.registry.create_or_get_job(
            payload=payload,
            correlation_id="corr_1",
            idempotency_key="idem_duplicate",
        )
        self.assertTrue(created1)

        job2, created2 = self.registry.create_or_get_job(
            payload=payload,
            correlation_id="corr_2",
            idempotency_key="idem_duplicate",
        )
        self.assertFalse(created2)
        self.assertEqual(job1.job_id, job2.job_id)

    def test_idempotency_conflict_different_payload(self) -> None:
        payload1 = _make_request(product_name="Produit A")
        payload2 = _make_request(product_name="Produit B")

        self.registry.create_or_get_job(
            payload=payload1,
            correlation_id="corr_1",
            idempotency_key="idem_conflict",
        )

        with self.assertRaises(IdempotencyConflictError):
            self.registry.create_or_get_job(
                payload=payload2,
                correlation_id="corr_2",
                idempotency_key="idem_conflict",
            )

    def test_lifecycle_transitions(self) -> None:
        payload = _make_request(product_name="Arbre à chat")
        job, _ = self.registry.create_or_get_job(
            payload=payload,
            correlation_id="corr_life",
            idempotency_key="idem_life",
        )

        # accepted -> queued
        self.registry.update_status(job.job_id, JobStatus.queued, current_step="queued")
        self.assertEqual(job.status, JobStatus.queued)

        # queued -> collecting
        self.registry.update_status(
            job.job_id,
            JobStatus.collecting,
            current_step="collecting_sources",
            progress_percent=20,
        )
        self.assertEqual(job.status, JobStatus.collecting)
        self.assertEqual(job.progress_percent, 20)

        # collecting -> extracting
        self.registry.update_status(job.job_id, JobStatus.extracting, current_step="extracting_facts")
        self.assertEqual(job.status, JobStatus.extracting)

        # extracting -> normalizing
        self.registry.update_status(job.job_id, JobStatus.normalizing, current_step="normalizing_facts")
        self.assertEqual(job.status, JobStatus.normalizing)

        # normalizing -> building_context
        self.registry.update_status(job.job_id, JobStatus.building_context, current_step="building_prompt")
        self.assertEqual(job.status, JobStatus.building_context)

        # building_context -> generating
        self.registry.update_status(job.job_id, JobStatus.generating, current_step="calling_model")
        self.assertEqual(job.status, JobStatus.generating)

        # generating -> validating
        self.registry.update_status(job.job_id, JobStatus.validating, current_step="validating_schema")
        self.assertEqual(job.status, JobStatus.validating)

        # validating -> completed with mock result
        mock_result = ProductEnrichmentResult(
            schema_version="product-enrichment-result.v1",
            job_id=job.job_id,
            product_id=job.product_id,
            status=JobStatus.completed,
            pipeline_version="product-enrichment.v1",
            enrichment=Enrichment(
                identity=Identity(name="Arbre à chat"),
                content=Content(title="Arbre à chat"),
                specifications=Specifications(),
                seo=Seo(meta_title="Arbre à chat", slug="arbre-a-chat"),
            ),
            validation=Validation(
                schema_valid=True,
                fact_status="verified",
                requires_human_review=False,
            ),
            generation=Generation(
                provider="mock",
                model="test-model",
                prompt_version="1.0",
                generated_at=datetime.now(UTC),
            ),
            completed_at=datetime.now(UTC),
        )
        self.registry.update_status(
            job.job_id,
            JobStatus.completed,
            current_step="finished",
            progress_percent=100,
            result=mock_result,
        )
        self.assertEqual(job.status, JobStatus.completed)
        self.assertIsNotNone(job.completed_at)
        self.assertIsNotNone(job.result)

        # Terminal state: cannot transition further
        with self.assertRaises(InvalidJobTransitionError):
            self.registry.update_status(job.job_id, JobStatus.collecting)

    def test_clear_registry(self) -> None:
        payload = _make_request(product_name="Jouet")
        job, _ = self.registry.create_or_get_job(
            payload=payload,
            correlation_id="c1",
            idempotency_key="k1",
        )
        self.assertEqual(len(self.registry.list_jobs()), 1)
        self.registry.clear()
        self.assertEqual(len(self.registry.list_jobs()), 0)
        self.assertIsNone(self.registry.get_job(job.job_id))


class TestRouterWithRegistry(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = InMemoryJobRegistry()

    def test_create_endpoint_success(self) -> None:
        payload = _make_request(product_name="Shampoing bio")
        ack = create_product_enrichment(
            payload=payload,
            authorization="Bearer test-token",
            correlation_id="corr_router_1",
            idempotency_key="idem_router_1",
            registry=self.registry,
        )

        self.assertEqual(ack.status, JobStatus.accepted)
        self.assertEqual(ack.current_step, "queued")
        self.assertEqual(ack.correlation_id, "corr_router_1")

        # Verify job is in registry
        job = self.registry.get_job(ack.job_id)
        self.assertIsNotNone(job)
        self.assertEqual(job.job_id, ack.job_id)

    def test_get_endpoint_success(self) -> None:
        payload = _make_request(product_name="Collier chien")
        ack = create_product_enrichment(
            payload=payload,
            authorization="Bearer test-token",
            correlation_id="corr_router_2",
            idempotency_key="idem_router_2",
            registry=self.registry,
        )

        status_resp = get_product_enrichment(job_id=ack.job_id, registry=self.registry)
        self.assertEqual(status_resp.job_id, ack.job_id)
        self.assertEqual(status_resp.status, JobStatus.accepted)
        self.assertIsNone(status_resp.result)

    def test_get_endpoint_not_found(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            get_product_enrichment(job_id="job_inexistant", registry=self.registry)
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.detail, "job_not_found")

    def test_create_endpoint_unauthorized(self) -> None:
        payload = _make_request(product_name="Test")
        with self.assertRaises(HTTPException) as ctx:
            create_product_enrichment(
                payload=payload,
                authorization=None,
                correlation_id="corr_1",
                idempotency_key="idem_1",
                registry=self.registry,
            )
        self.assertEqual(ctx.exception.status_code, 401)

    def test_create_endpoint_missing_headers(self) -> None:
        payload = _make_request(product_name="Test")
        with self.assertRaises(HTTPException) as ctx:
            create_product_enrichment(
                payload=payload,
                authorization="Bearer ok",
                correlation_id=None,
                idempotency_key="idem_1",
                registry=self.registry,
            )
        self.assertEqual(ctx.exception.status_code, 422)

    def test_create_endpoint_idempotency_conflict(self) -> None:
        payload1 = _make_request(product_name="Produit 1")
        payload2 = _make_request(product_name="Produit 2")

        create_product_enrichment(
            payload=payload1,
            authorization="Bearer ok",
            correlation_id="corr_1",
            idempotency_key="same_key",
            registry=self.registry,
        )

        with self.assertRaises(HTTPException) as ctx:
            create_product_enrichment(
                payload=payload2,
                authorization="Bearer ok",
                correlation_id="corr_2",
                idempotency_key="same_key",
                registry=self.registry,
            )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail, "idempotency_conflict")


if __name__ == "__main__":
    unittest.main()

