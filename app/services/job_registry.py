import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.schemas.enrichment import (
    JobStatus,
    ProductEnrichmentAck,
    ProductEnrichmentRequest,
    ProductEnrichmentResult,
    ProductEnrichmentStatus,
)


class IdempotencyConflictError(Exception):
    """Levée lorsqu'une clé d'idempotence est réutilisée avec un corps de requête différent."""
    pass


class InvalidJobTransitionError(Exception):
    """Levée lorsqu'une transition d'état de job est invalide."""
    pass


# Transitions d'états valides selon le contrat technique ROOK
VALID_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.accepted: {JobStatus.queued, JobStatus.cancelled, JobStatus.failed},
    JobStatus.queued: {JobStatus.collecting, JobStatus.cancelled, JobStatus.failed},
    JobStatus.collecting: {JobStatus.extracting, JobStatus.cancelled, JobStatus.failed, JobStatus.timed_out},
    JobStatus.extracting: {JobStatus.normalizing, JobStatus.cancelled, JobStatus.failed, JobStatus.timed_out},
    JobStatus.normalizing: {JobStatus.building_context, JobStatus.cancelled, JobStatus.failed, JobStatus.timed_out},
    JobStatus.accepted: {JobStatus.queued, JobStatus.generating, JobStatus.cancelled, JobStatus.failed},
    JobStatus.queued: {JobStatus.collecting, JobStatus.generating, JobStatus.cancelled, JobStatus.failed},
    JobStatus.collecting: {JobStatus.extracting, JobStatus.generating, JobStatus.cancelled, JobStatus.failed, JobStatus.timed_out},
    JobStatus.extracting: {JobStatus.normalizing, JobStatus.generating, JobStatus.cancelled, JobStatus.failed, JobStatus.timed_out},
    JobStatus.normalizing: {JobStatus.building_context, JobStatus.generating, JobStatus.cancelled, JobStatus.failed, JobStatus.timed_out},
    JobStatus.building_context: {JobStatus.generating, JobStatus.cancelled, JobStatus.failed, JobStatus.timed_out},
    JobStatus.generating: {JobStatus.validating, JobStatus.cancelled, JobStatus.failed, JobStatus.timed_out},
    JobStatus.generating: {JobStatus.validating, JobStatus.completed, JobStatus.needs_review, JobStatus.cancelled, JobStatus.failed, JobStatus.timed_out},
    JobStatus.validating: {JobStatus.completed, JobStatus.needs_review, JobStatus.cancelled, JobStatus.failed, JobStatus.timed_out},
    # États terminaux
    JobStatus.completed: set(),
    JobStatus.needs_review: set(),
    JobStatus.failed: set(),
    JobStatus.timed_out: set(),
    JobStatus.cancelled: set(),
}


@dataclass
class JobRecord:
    job_id: str
    product_id: str
    correlation_id: str
    idempotency_key: str
    status: JobStatus
    current_step: str
    progress_percent: int
    attempt: int
    max_attempts: int
    request_payload: dict[str, Any]
    payload_hash: str
    pipeline_version: str
    status_url: str
    accepted_at: datetime
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    result: ProductEnrichmentResult | None = None
    error_code: str | None = None
    error_message: str | None = None

    def to_ack(self) -> ProductEnrichmentAck:
        return ProductEnrichmentAck(
            schema_version="product-enrichment-ack.v1",
            job_id=self.job_id,
            product_id=self.product_id,
            status=self.status,
            current_step=self.current_step,
            correlation_id=self.correlation_id,
            pipeline_version=self.pipeline_version,
            status_url=self.status_url,
            accepted_at=self.accepted_at,
        )

    def to_status(self) -> ProductEnrichmentStatus:
        return ProductEnrichmentStatus(
            schema_version="product-enrichment-ack.v1",
            job_id=self.job_id,
            product_id=self.product_id,
            status=self.status,
            current_step=self.current_step,
            correlation_id=self.correlation_id,
            pipeline_version=self.pipeline_version,
            status_url=self.status_url,
            accepted_at=self.accepted_at,
            created_at=self.created_at,
            result=self.result,
        )


def _compute_payload_hash(payload: ProductEnrichmentRequest) -> str:
    """Calcule une empreinte SHA-256 stable du payload de requête."""
    serialized = json.dumps(payload.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class InMemoryJobRegistry:
    """Registre en mémoire pour gérer le cycle de vie et l'idempotence des jobs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, JobRecord] = {}
        self._idempotency_map: dict[str, str] = {}  # idempotency_key -> job_id

    def create_or_get_job(
        self,
        payload: ProductEnrichmentRequest,
        correlation_id: str,
        idempotency_key: str,
        pipeline_version: str = "product-enrichment.v1",
    ) -> tuple[JobRecord, bool]:
        """
        Crée un nouveau job en mémoire ou retourne le job existant associé à la clé d'idempotence.
        Retourne (job_record, created).
        Lève IdempotencyConflictError si la clé est déjà utilisée avec un payload différent.
        """
        payload_hash = _compute_payload_hash(payload)

        with self._lock:
            existing_job_id = self._idempotency_map.get(idempotency_key)
            if existing_job_id is not None:
                existing_job = self._jobs[existing_job_id]
                if existing_job.payload_hash != payload_hash:
                    raise IdempotencyConflictError(
                        f"Idempotency key '{idempotency_key}' already used with different payload"
                    )
                return existing_job, False

            job_id = payload.job_id or f"job_{uuid4().hex}"
            product_id = payload.product_id or f"product_{uuid4().hex}"
            now = datetime.now(UTC)

            job = JobRecord(
                job_id=job_id,
                product_id=product_id,
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
                status=JobStatus.accepted,
                current_step="queued",
                progress_percent=0,
                attempt=1,
                max_attempts=3,
                request_payload=payload.model_dump(mode="json"),
                payload_hash=payload_hash,
                pipeline_version=pipeline_version,
                status_url=f"/internal/v1/product-enrichments/jobs/{job_id}",
                accepted_at=now,
                created_at=now,
                updated_at=now,
            )

            self._jobs[job_id] = job
            self._idempotency_map[idempotency_key] = job_id
            return job, True

    def get_job(self, job_id: str) -> JobRecord | None:
        """Récupère un job par son identifiant."""
        with self._lock:
            return self._jobs.get(job_id)

    def get_by_idempotency_key(self, idempotency_key: str) -> JobRecord | None:
        """Récupère un job par sa clé d'idempotence."""
        with self._lock:
            job_id = self._idempotency_map.get(idempotency_key)
            if job_id:
                return self._jobs.get(job_id)
            return None

    def update_status(
        self,
        job_id: str,
        status: JobStatus,
        current_step: str | None = None,
        progress_percent: int | None = None,
        result: ProductEnrichmentResult | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        validate_transition: bool = True,
    ) -> JobRecord:
        """Met à jour le statut et l'étape d'un job dans son cycle de vie."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"Job '{job_id}' not found")

            if validate_transition:
                allowed_next_states = VALID_TRANSITIONS.get(job.status, set())
                if status not in allowed_next_states and status != job.status:
                    raise InvalidJobTransitionError(
                        f"Cannot transition job '{job_id}' from {job.status} to {status}"
                    )

            now = datetime.now(UTC)
            job.status = status
            job.updated_at = now

            if current_step is not None:
                job.current_step = current_step
            if progress_percent is not None:
                job.progress_percent = progress_percent
            if result is not None:
                job.result = result
            if error_code is not None:
                job.error_code = error_code
            if error_message is not None:
                job.error_message = error_message

            if status in {JobStatus.completed, JobStatus.needs_review, JobStatus.failed, JobStatus.timed_out, JobStatus.cancelled}:
                job.completed_at = now

            return job

    def reset_for_retry(self, job_id: str) -> JobRecord:
        """Réinitialise un job échoué pour une nouvelle tentative."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"Job '{job_id}' not found")

            if job.status not in (JobStatus.failed, JobStatus.timed_out):
                raise InvalidJobTransitionError(
                    f"Only failed or timed_out jobs can be retried, current status is '{job.status}'"
                )

            if job.attempt >= job.max_attempts:
                raise InvalidJobTransitionError(
                    f"Job '{job_id}' has reached maximum attempts ({job.max_attempts})"
                )

            now = datetime.now(UTC)
            job.attempt += 1
            job.status = JobStatus.accepted
            job.current_step = "queued"
            job.progress_percent = 0
            job.error_code = None
            job.error_message = None
            job.completed_at = None
            job.updated_at = now
            return job

    def list_jobs(self) -> list[JobRecord]:
        """Retourne la liste de tous les jobs actuellement en mémoire."""
        with self._lock:
            return list(self._jobs.values())

    def clear(self) -> None:
        """Vide le registre (principalement pour les tests)."""
        with self._lock:
            self._jobs.clear()
            self._idempotency_map.clear()


# Instance singleton globale pour l'application
_registry_instance = InMemoryJobRegistry()


def get_job_registry() -> InMemoryJobRegistry:
    """Dépendance FastAPI pour injecter le registre de jobs."""
    return _registry_instance

