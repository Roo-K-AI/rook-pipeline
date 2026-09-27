from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

_url = settings.database_url
if not _url:
    raise RuntimeError("DATABASE_URL non configurée (voir .env / Render env)")

if "sslmode" not in _url:
    sep = "&" if "?" in _url else "?"
    _url = f"{_url}{sep}sslmode=require"

engine = create_engine(
    _url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    pool_recycle=1800,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)