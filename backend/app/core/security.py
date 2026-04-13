from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any
from uuid import uuid4

from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

from app.config import get_settings
from app.exceptions import AuthenticationError

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class TokenPayload(BaseModel):
    sub: str
    scopes: list[str] = []
    jti: str = ""
    exp: datetime | None = None
    iat: datetime | None = None


def create_access_token(
    subject: str,
    scopes: list[str] | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    """Create a JWT access token."""
    settings = get_settings()
    now = datetime.now(timezone.utc)

    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.jwt_expiry_minutes)

    payload: dict[str, Any] = {
        "sub": subject,
        "scopes": scopes or [],
        "jti": str(uuid4()),
        "iat": now,
        "exp": now + expires_delta,
    }

    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> TokenPayload:
    """Decode and validate a JWT token."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        return TokenPayload(**payload)
    except JWTError as e:
        raise AuthenticationError(detail=f"Invalid token: {e}") from e


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def generate_api_key() -> tuple[str, str]:
    """Generate an API key pair.

    Returns:
        Tuple of (raw_key, hashed_key). The raw_key is shown once to the user;
        the hashed_key is stored in the database for verification.
    """
    raw_key = f"mc_{secrets.token_urlsafe(32)}"
    hashed_key = sha256(raw_key.encode()).hexdigest()
    return raw_key, hashed_key


def verify_api_key(raw_key: str, hashed_key: str) -> bool:
    """Verify a raw API key against its stored hash."""
    return sha256(raw_key.encode()).hexdigest() == hashed_key
