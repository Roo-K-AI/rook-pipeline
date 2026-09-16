from fastapi import FastAPI

from app.routers.enrichments import router as enrichments_router
from app.schemas.enrichment import HealthResponse

app = FastAPI(
    title="ROOK Product Enrichment API",
    version="1.0.0",
)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/ready", response_model=HealthResponse)
def ready() -> HealthResponse:
    return HealthResponse(status="ready")


app.include_router(enrichments_router)
