from fastapi import Header, HTTPException, status

from app.core.config import settings


def verify_service_auth(authorization: str | None = Header(default=None)) -> str:
    """
    Valide l'authentification de service Laravel -> FastAPI.
    Lève HTTP 401 si le credential est absent ou invalide.
    """
    if not authorization or not authorization.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="service_authentication_failed",
        )

    parts = authorization.strip().split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        token = parts[1]
    else:
        token = authorization.strip()

    if settings.enforce_auth and token != settings.service_auth_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="service_authentication_failed",
        )

    return token

