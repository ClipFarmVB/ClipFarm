"""
Supabase Auth JWT verification for FastAPI.

Verifies tokens using Supabase's JWKS endpoint (supports ES256 ECC keys).

Usage in routers:
    from app.auth import get_current_user_id

    @router.get("/something")
    async def endpoint(user_id: uuid.UUID = Depends(get_current_user_id)):
        ...
"""
import logging
import uuid

import jwt as pyjwt
from jwt import PyJWKClient
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.user import User

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)

# JWKS client — caches keys automatically
_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        jwks_url = f"{settings.supabase_url}/auth/v1/.well-known/jwks.json"
        _jwks_client = PyJWKClient(jwks_url, cache_keys=True)
    return _jwks_client


async def _ensure_user_exists(user_id: uuid.UUID, email: str, db: AsyncSession) -> None:
    """Create a users row if one doesn't exist yet (first login after Supabase signup)."""
    stmt = pg_insert(User).values(id=user_id, email=email).on_conflict_do_nothing(
        index_elements=["id"]
    )
    await db.execute(stmt)
    await db.commit()


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> uuid.UUID:
    """
    Validate the Supabase JWT from the Authorization header
    and return the user's UUID.

    Falls back to DEV_USER_ID when no auth header is present
    and no Supabase URL is configured (local dev convenience).
    """
    DEV_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

    if credentials is None:
        # Dev fallback ONLY when explicitly enabled
        if settings.debug and not settings.supabase_url:
            return DEV_USER_ID
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
        )

    if not settings.supabase_url:
        # Refuse to verify tokens without a configured issuer
        logger.error("Auth attempted but supabase_url is not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured",
        )

    token = credentials.credentials
    try:
        # Fetch the signing key from Supabase JWKS endpoint
        jwks_client = _get_jwks_client()
        signing_key = jwks_client.get_signing_key_from_jwt(token)

        payload = pyjwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256"],
            audience="authenticated",
            issuer=f"{settings.supabase_url}/auth/v1",
            leeway=30,  # allow 30s clock skew between local machine and Supabase
        )
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        )
    except pyjwt.InvalidTokenError:
        logger.warning("Invalid JWT presented", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
    except Exception:
        logger.exception("Token verification failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token verification failed",
        )

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing 'sub' claim",
        )

    try:
        user_id = uuid.UUID(sub)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user ID in token",
        )

    # Auto-create user row on first authenticated request
    email = payload.get("email", f"{sub}@unknown")
    await _ensure_user_exists(user_id, email, db)

    return user_id
