import json
from datetime import datetime, timezone
from sqlalchemy import text
from app.db import SessionLocal


def create_job(job_id: str, product_id: str | None, payload: dict,
               correlation_id: str, pipeline_version: str) -> None:
    with SessionLocal() as session:
        session.execute(
            text("""
                INSERT INTO enrichment_jobs
                    (job_id, product_id, status, current_step,
                     correlation_id, pipeline_version, payload)
                VALUES
                    (:job_id, :product_id, 'accepted', 'accepted',
                     :correlation_id, :pipeline_version, CAST(:payload AS JSONB))
            """),
            {
                "job_id": job_id,
                "product_id": product_id,
                "correlation_id": correlation_id,
                "pipeline_version": pipeline_version,
                "payload": json.dumps(payload),
            },
        )
        session.commit()


def get_job(job_id: str) -> dict | None:
    with SessionLocal() as session:
        row = session.execute(
            text("SELECT * FROM enrichment_jobs WHERE job_id = :job_id"),
            {"job_id": job_id},
        ).mappings().first()
        return dict(row) if row else None


def update_job_status(job_id: str, status: str, current_step: str) -> None:
    with SessionLocal() as session:
        session.execute(
            text("""
                UPDATE enrichment_jobs
                SET status = :status,
                    current_step = :current_step,
                    updated_at = NOW()
                WHERE job_id = :job_id
            """),
            {"job_id": job_id, "status": status, "current_step": current_step},
        )
        session.commit()


def complete_job(job_id: str, result: dict) -> None:
    with SessionLocal() as session:
        session.execute(
            text("""
                UPDATE enrichment_jobs
                SET status = 'completed',
                    current_step = 'completed',
                    result = CAST(:result AS JSONB),
                    updated_at = NOW()
                WHERE job_id = :job_id
            """),
            {"job_id": job_id, "result": json.dumps(result)},
        )
        session.commit()


def fail_job(job_id: str, error: str) -> None:
    with SessionLocal() as session:
        session.execute(
            text("""
                UPDATE enrichment_jobs
                SET status = 'failed',
                    current_step = 'failed',
                    error = :error,
                    updated_at = NOW()
                WHERE job_id = :job_id
            """),
            {"job_id": job_id, "error": error},
        )
        session.commit()