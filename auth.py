from typing import Annotated, Any

import httpx
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
    OAuth2AuthorizationCodeBearer,
)
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError

from config import settings


oauth2_scheme = OAuth2AuthorizationCodeBearer(
    authorizationUrl=f"{settings.issuer}/protocol/openid-connect/auth",
    tokenUrl=f"{settings.issuer}/protocol/openid-connect/token",
    scopes={"openid": "Sign in with Keycloak", "profile": "Read your profile"},
    scheme_name="Keycloak",
    auto_error=False,
)

bearer_scheme = HTTPBearer(
    scheme_name="BearerToken",
    description="Paste a Keycloak access token from another client or service account.",
    auto_error=False,
)

jwks_client = PyJWKClient(
    f"{settings.issuer}/protocol/openid-connect/certs",
    cache_jwk_set=True,
    lifespan=settings.keycloak_jwks_cache_seconds,
    timeout=settings.keycloak_http_timeout_seconds,
)


def ensure_token_active(token: str) -> None:
    secret = settings.keycloak_introspection_client_secret
    if secret is None or not secret.get_secret_value():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        )
    try:
        response = httpx.post(
            f"{settings.issuer}/protocol/openid-connect/token/introspect",
            data={"token": token, "token_type_hint": "access_token"},
            auth=(settings.keycloak_introspection_client_id, secret.get_secret_value()),
            timeout=settings.keycloak_http_timeout_seconds,
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or not isinstance(result.get("active"), bool):
            raise ValueError("Invalid introspection response")
    except (httpx.HTTPError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        ) from error
    if not result["active"]:
        raise jwt.InvalidTokenError("Access token is no longer active")


def get_current_user(
    oauth_token: Annotated[str | None, Depends(oauth2_scheme)],
    bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> dict[str, Any]:
    token = oauth_token or (bearer.credentials if bearer else None)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.keycloak_audience,
            issuer=settings.issuer,
            leeway=settings.keycloak_clock_skew_seconds,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
        if claims.get("typ") != "Bearer":
            raise jwt.InvalidTokenError("Expected a Keycloak access token")
        if settings.keycloak_validation_mode == "introspection":
            ensure_token_active(token)
        return claims
    except PyJWKClientConnectionError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        ) from error
    except (jwt.InvalidTokenError, PyJWKClientError) as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired access token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error


CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]


class RequireRoles:
    def __init__(self, *roles: str, client_id: str | None = None):
        if not roles or any(not role for role in roles):
            raise ValueError("At least one non-empty role is required")
        self.roles = set(roles)
        self.client_id = client_id

    def __call__(self, current_user: CurrentUser) -> dict[str, Any]:
        if self.client_id is None:
            role_access = current_user.get("realm_access", {})
        else:
            role_access = current_user.get("resource_access", {}).get(self.client_id, {})
        if not self.roles.issubset(set(role_access.get("roles", []))):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user
