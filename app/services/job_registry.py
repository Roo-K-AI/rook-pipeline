import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from app.db import SessionLocal, engine
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


VALID_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.accepted: {
        JobStatus.queued,
        JobStatus.generating,
        JobStatus.cancelled,
        JobStatus.failed,
    },
    JobStatus.queued: {
        JobStatus.collecting,
        JobStatus.generating,
        JobStatus.cancelled,
        JobStatus.failed,
    },
    JobStatus.collecting: {
        JobStatus.extracting,
        JobStatus.generating,
        JobStatus.cancelled,
        JobStatus.failed,
        JobStatus.timed_out,
    },
    JobStatus.extracting: {
        JobStatus.normalizing,
        JobStatus.generating,
        JobStatus.cancelled,
        JobStatus.failed,
        JobStatus.timed_out,
    },
    JobStatus.normalizing: {
        JobStatus.building_context,
        JobStatus.generating,
        JobStatus.cancelled,
        JobStatus.failed,
        JobStatus.timed_out,
    },
    JobStatus.building_context: {
        JobStatus.generating,
        JobStatus.cancelled,
        JobStatus.failed,
        JobStatus.timed_out,
    },
    JobStatus.generating: {
        JobStatus.validating,
        JobStatus.completed,
        JobStatus.needs_review,
        JobStatus.cancelled,
        JobStatus.failed,
        JobStatus.timed_out,
    },
    JobStatus.validating: {
        JobStatus.completed,
        JobStatus.needs_review,
        JobStatus.cancelled,
        JobStatus.failed,
        JobStatus.timed_out,
    },
    # États terminaux
    JobStatus.completed: set(),
    JobStatus.needs_review: set(),
    JobStatus.failed: set(),
    JobStatus.timed_out: set(),
    JobStatus.cancelled: set(),
}

TERMINAL_STATES: set[JobStatus] = {
    JobStatus.completed,
    JobStatus.needs_review,
    JobStatus.failed,
    JobStatus.timed_out,
    JobStatus.cancelled,
}


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS enrichment_jobs (
    job_id           TEXT PRIMARY KEY,
    product_id       TEXT,
    correlation_id   TEXT NOT NULL,
    idempotency_key  TEXT UNIQUE,
    status           TEXT NOT NULL DEFAULT 'accepted',
    current_step     TEXT NOT NULL DEFAULT 'queued',
    progress_percent INTEGER NOT NULL DEFAULT 0,
    attempt          INTEGER NOT NULL DEFAULT 1,
    max_attempts     INTEGER NOT NULL DEFAULT 3,
    request_payload  JSONB NOT NULL,
    payload_hash     TEXT NOT NULL,
    pipeline_version TEXT NOT NULL,
    status_url       TEXT NOT NULL,
    result           JSONB,
    error_code       TEXT,
    error_message    TEXT,
    accepted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at       TIMESTAMPTZ,
    completed_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_enrichment_jobs_status
    ON enrichment_jobs(status);

CREATE INDEX IF NOT EXISTS idx_enrichment_jobs_created_at
    ON enrichment_jobs(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_enrichment_jobs_idempotency
    ON enrichment_jobs(idempotency_key);
"""


def _ensure_schema() -> None:
    """Crée la table et les index si nécessaire (idempotent)."""
    with engine.begin() as conn:
        for statement in SCHEMA_SQL.strip().split(";"):
            stmt = statement.strip()
            if stmt:
                conn.execute(text(stmt))


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


def _row_to_record(row: Any) -> JobRecord:
    """Convertit une ligne SQL en JobRecord."""
    result_json = row["result"]
    result_obj: ProductEnrichmentResult | None = None
    if result_json:
        if isinstance(result_json, str):
            result_json = json.loads(result_json)
        result_obj = ProductEnrichmentResult.model_validate(result_json)

    payload = row["request_payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)

    return JobRecord(
        job_id=row["job_id"],
        product_id=row["product_id"] or "",
        correlation_id=row["correlation_id"],
        idempotency_key=row["idempotency_key"] or "",
        status=JobStatus(row["status"]),
        current_step=row["current_step"],
        progress_percent=row["progress_percent"],
        attempt=row["attempt"],
        max_attempts=row["max_attempts"],
        request_payload=payload,
        payload_hash=row["payload_hash"],
        pipeline_version=row["pipeline_version"],
        status_url=row["status_url"],
        accepted_at=row["accepted_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        result=result_obj,
        error_code=row["error_code"],
        error_message=row["error_message"],
    )


class PostgresJobRegistry:
    """Registre persistant des jobs d'enrichissement, adossé à PostgreSQL."""

    def __init__(self) -> None:
        _ensure_schema()

    # ------------------------------------------------------------------ #
    # Création / récupération idempotente
    # ------------------------------------------------------------------ #
    def create_or_get_job(
        self,
        payload: ProductEnrichmentRequest,
        correlation_id: str,
        idempotency_key: str,
        pipeline_version: str = "product-enrichment.v1",
    ) -> tuple[JobRecord, bool]:
        """
        Crée un nouveau job ou retourne le job existant associé à la clé
        d'idempotence. Retourne (job_record, created).
        Lève IdempotencyConflictError si la clé est réutilisée avec un payload différent.
        """
        payload_hash = _compute_payload_hash(payload)

        with SessionLocal() as session:
            existing = session.execute(
                text(
                    "SELECT * FROM enrichment_jobs WHERE idempotency_key = :key"
                ),
                {"key": idempotency_key},
            ).mappings().first()

            if existing:
                if existing["payload_hash"] != payload_hash:
                    raise IdempotencyConflictError(
                        f"Idempotency key '{idempotency_key}' "
                        f"already used with different payload"
                    )
                return _row_to_record(existing), False

            job_id = payload.job_id or f"job_{uuid4().hex}"
            product_id = payload.product_id or f"product_{uuid4().hex}"
            now = datetime.now(UTC)
            status_url = f"/internal/v1/product-enrichments/jobs/{job_id}"

            session.execute(
                text(
                    """
                    INSERT INTO enrichment_jobs (
                        job_id, product_id, correlation_id, idempotency_key,
                        status, current_step, progress_percent, attempt, max_attempts,
                        request_payload, payload_hash, pipeline_version, status_url,
                        accepted_at, created_at, updated_at
                    ) VALUES (
                        :job_id, :product_id, :correlation_id, :idempotency_key,
                        :status, :current_step, 0, 1, 3,
                        CAST(:request_payload AS JSONB), :payload_hash,
                        :pipeline_version, :status_url,
                        :now, :now, :now
                    )
                    """
                ),
                {
                    "job_id": job_id,
                    "product_id": product_id,
                    "correlation_id": correlation_id,
                    "idempotency_key": idempotency_key,
                    "status": JobStatus.accepted.value,
                    "current_step": "queued",
                    "request_payload": json.dumps(payload.model_dump(mode="json")),
                    "payload_hash": payload_hash,
                    "pipeline_version": pipeline_version,
                    "status_url": status_url,
                    "now": now,
                },
            )
            session.commit()

            row = session.execute(
                text("SELECT * FROM enrichment_jobs WHERE job_id = :id"),
                {"id": job_id},
            ).mappings().first()

            return _row_to_record(row), True

    # ------------------------------------------------------------------ #
    # Lecture
    # ------------------------------------------------------------------ #
    def get_job(self, job_id: str) -> JobRecord | None:
        """Récupère un job par son identifiant."""
        with SessionLocal() as session:
            row = session.execute(
                text("SELECT * FROM enrichment_jobs WHERE job_id = :id"),
                {"id": job_id},
            ).mappings().first()
            return _row_to_record(row) if row else None

    def get_by_idempotency_key(self, idempotency_key: str) -> JobRecord | None:
        """Récupère un job par sa clé d'idempotence."""
        with SessionLocal() as session:
            row = session.execute(
                text(
                    "SELECT * FROM enrichment_jobs WHERE idempotency_key = :key"
                ),
                {"key": idempotency_key},
            ).mappings().first()
            return _row_to_record(row) if row else None

    def list_jobs(self, limit: int = 100) -> list[JobRecord]:
        """Retourne les derniers jobs créés."""
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    "SELECT * FROM enrichment_jobs "
                    "ORDER BY created_at DESC LIMIT :limit"
                ),
                {"limit": limit},
            ).mappings().all()
            return [_row_to_record(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Mise à jour
    # ------------------------------------------------------------------ #
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
        with SessionLocal() as session:
            row = session.execute(
                text(
                    "SELECT * FROM enrichment_jobs "
                    "WHERE job_id = :id FOR UPDATE"
                ),
                {"id": job_id},
            ).mappings().first()

            if row is None:
                raise KeyError(f"Job '{job_id}' not found")

            current_status = JobStatus(row["status"])

            if validate_transition and status != current_status:
                allowed = VALID_TRANSITIONS.get(current_status, set())
                if status not in allowed:
                    raise InvalidJobTransitionError(
                        f"Cannot transition job '{job_id}' "
                        f"from {current_status.value} to {status.value}"
                    )

            now = datetime.now(UTC)
            params: dict[str, Any] = {
                "job_id": job_id,
                "status": status.value,
                "updated_at": now,
            }

            sets = ["status = :status", "updated_at = :updated_at"]

            if current_step is not None:
                sets.append("current_step = :current_step")
                params["current_step"] = current_step

            if progress_percent is not None:
                sets.append("progress_percent = :progress_percent")
                params["progress_percent"] = progress_percent

            if result is not None:
                sets.append("result = CAST(:result AS JSONB)")
                params["result"] = json.dumps(result.model_dump(mode="json"))

            if error_code is not None:
                sets.append("error_code = :error_code")
                params["error_code"] = error_code

            if error_message is not None:
                sets.append("error_message = :error_message")
                params["error_message"] = error_message

            if status == JobStatus.generating and row["started_at"] is None:
                sets.append("started_at = :started_at")
                params["started_at"] = now

            if status in TERMINAL_STATES:
                sets.append("completed_at = :completed_at")
                params["completed_at"] = now

            session.execute(
                text(
                    f"UPDATE enrichment_jobs SET {', '.join(sets)} "
                    f"WHERE job_id = :job_id"
                ),
                params,
            )
            session.commit()

            updated = session.execute(
                text("SELECT * FROM enrichment_jobs WHERE job_id = :id"),
                {"id": job_id},
            ).mappings().first()

            return _row_to_record(updated)

    def reset_for_retry(self, job_id: str) -> JobRecord:
        """Réinitialise un job échoué pour une nouvelle tentative."""
        with SessionLocal() as session:
            row = session.execute(
                text(
                    "SELECT * FROM enrichment_jobs "
                    "WHERE job_id = :id FOR UPDATE"
                ),
                {"id": job_id},
            ).mappings().first()

            if row is None:
                raise KeyError(f"Job '{job_id}' not found")

            status = JobStatus(row["status"])
            if status not in (JobStatus.failed, JobStatus.timed_out):
                raise InvalidJobTransitionError(
                    f"Only failed or timed_out jobs can be retried, "
                    f"current status is '{status.value}'"
                )

            if row["attempt"] >= row["max_attempts"]:
                raise InvalidJobTransitionError(
                    f"Job '{job_id}' has reached maximum attempts "
                    f"({row['max_attempts']})"
                )

            now = datetime.now(UTC)
            session.execute(
                text(
                    """
                    UPDATE enrichment_jobs
                    SET status = :status,
                        current_step = :current_step,
                        progress_percent = 0,
                        attempt = attempt + 1,
                        error_code = NULL,
                        error_message = NULL,
                        completed_at = NULL,
                        updated_at = :now
                    WHERE job_id = :job_id
                    """
                ),
                {
                    "job_id": job_id,
                    "status": JobStatus.accepted.value,
                    "current_step": "queued",
                    "now": now,
                },
            )
            session.commit()

            updated = session.execute(
                text("SELECT * FROM enrichment_jobs WHERE job_id = :id"),
                {"id": job_id},
            ).mappings().first()

            return _row_to_record(updated)

    def clear(self) -> None:
        """Vide la table (tests uniquement)."""
        with SessionLocal() as session:
            session.execute(text("DELETE FROM enrichment_jobs"))
            session.commit()


_registry_instance: PostgresJobRegistry | None = None


def get_job_registry() -> PostgresJobRegistry:
    """Dépendance FastAPI pour injecter le registre de jobs."""
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = PostgresJobRegistry()
    return _registry_instance