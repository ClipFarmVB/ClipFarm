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
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
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


def _violated_constraint(err: IntegrityError) -> str:
    """The name of the constraint an IntegrityError hit, and nothing else.

    Its message is not safe to log: Postgres puts the offending row's values in
    the DETAIL line, and for `users` that is the email address — the Sentry
    event that started CF-295 carried one. Sentry's scrubbing redacts secrets,
    not personal data, so what reaches a log from here is the constraint name.
    asyncpg carries it as `constraint_name` on the driver error, which
    SQLAlchemy chains beneath its own.
    """
    cause: BaseException | None = err.orig
    while cause is not None:
        name = getattr(cause, "constraint_name", None)
        if name:
            return str(name)
        cause = cause.__cause__
    return "unknown"


async def _ensure_user_exists(user_id: uuid.UUID, email: str, db: AsyncSession) -> None:
    """Create a users row if one doesn't exist yet (first login after Supabase signup).

    **Read first.** The row exists for every request after the first, so the
    common path is a SELECT. This used to be an unconditional INSERT *and
    COMMIT* on every authenticated request — a write round-trip on the hot path
    to handle a once-per-user case.

    **`users` has two unique indexes, and the arbiter only covers one.**
    `on_conflict_do_nothing(index_elements=["id"])` names `users_pkey` as the
    arbiter; `users.email` carries its own `users_email_key` (migration 001). A
    conflict that surfaces on a *non-arbiter* index is not swallowed — it
    raises. Two different situations land there, and they are not the same
    thing, which is why this catches the error and re-reads rather than simply
    widening the arbiter to cover email:

    - **Benign race.** A fresh client fires several requests at once on first
      load and two of them insert the same (id, email). Production hit exactly
      this on 2026-09-22: two inserts 860 microseconds apart, same id, same
      email, one of them 500ing on `users_email_key`. The row is ours and the
      other request already created it, so returning is correct.
    - **Real collision.** The email belongs to a row with a *different* id —
      a Supabase account deleted and recreated, or two unlinked identities
      sharing an address. The caller's id is not in `users` and cannot be
      inserted, so every FK write they attempt would fail later. That is a 409
      here rather than a confusing failure further in.

    **A real collision is rejected, not relinked.** Adopting the existing row by
    rewriting its id would hand one Supabase identity another's games, and with
    two unlinked identities it would rewrite the user row *and every FK row* on
    each alternating sign-in. None of the six columns referencing `users.id`
    declares ON UPDATE CASCADE, so there is no cheap version of that either.
    Reconciling a collision is a deliberate, one-off operation, not something a
    request handler should do on its own.
    """
    if await db.scalar(select(User.id).where(User.id == user_id)) is not None:
        return

    stmt = pg_insert(User).values(id=user_id, email=email).on_conflict_do_nothing(
        index_elements=["id"]
    )
    try:
        await db.execute(stmt)
        await db.commit()
        return
    except IntegrityError as err:
        # The session cannot be queried again until this is rolled back.
        await db.rollback()
        integrity_error: IntegrityError = err

    # Re-read to tell the two cases apart. The row we wanted may now exist
    # because the request we raced against committed it.
    if await db.scalar(select(User.id).where(User.id == user_id)) is not None:
        return

    holder = await db.scalar(select(User.id).where(User.email == email))
    if holder is not None and holder != user_id:
        # Ids, not the email: they identify both accounts, and the address
        # can be read from the database by whoever follows this up.
        logger.warning(
            "auth: the token's email is held by user %s but the token carries "
            "%s; refusing to relink (CF-295)",
            holder,
            user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This email is already registered to a different account. "
                "Sign in with the method you originally used, or contact support."
            ),
        )

    # Neither case. Something else in this INSERT violated a constraint, and
    # returning quietly would strand the caller with no row and no explanation
    # while every later FK write failed. Name what broke rather than only
    # returning a 503 — but by constraint, not by attaching the error, whose
    # message carries the row's email (see _violated_constraint).
    logger.error(
        "auth: could not provision user %s: %s violated (%s), and no email "
        "holder explains it",
        user_id,
        _violated_constraint(integrity_error),
        type(integrity_error.orig).__name__,
    )
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Could not provision the account, please retry",
    )


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


async def get_optional_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> uuid.UUID | None:
    """Identify the caller if they're signed in, else return None (CF-108).

    Public content has to be readable by a signed-out visitor, so those routes
    can't use `get_current_user_id` — it raises 401 with no credentials. This
    returns None instead and lets `services/access.py` decide.

    A token that fails verification — expired, malformed, wrong signature —
    also yields None rather than 401.

    This route is *optionally* authenticated: its own summary is "identify the
    caller if they're signed in", and an expired token is not signed in. Raising
    would mean a user whose tab has been open past expiry opens a public link
    and is refused content that loads fine in a private window, because their
    browser helpfully attached a stale bearer. Anonymous is the honest reading
    of a credential that no longer identifies anyone, and access.py then applies
    exactly the rules a signed-out visitor gets.

    Note this only ever *reduces* what the caller can see. Routes that require
    a user keep using `get_current_user_id`, which still raises.
    """
    if credentials is None:
        # Preserve the dev fallback so the local stack behaves as it does for
        # get_current_user_id rather than silently going anonymous.
        if settings.debug and not settings.supabase_url:
            return uuid.UUID("00000000-0000-0000-0000-000000000001")
        return None
    try:
        return await get_current_user_id(credentials=credentials, db=db)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            return None
        # 5xx from the JWKS fetch, say — that is a real failure and hiding it
        # behind "anonymous" would turn an outage into silent 404s.
        raise
