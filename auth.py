from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
    OAuth2AuthorizationCodeBearer,
)
from config import settings
from keycloak_client import AsyncKeycloakClient, AuthenticationServiceUnavailable


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

async def get_keycloak_client(request: Request) -> AsyncKeycloakClient:
    return request.app.state.keycloak_client


async def get_current_user(
    oauth_token: Annotated[str | None, Depends(oauth2_scheme)],
    bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    keycloak: Annotated[AsyncKeycloakClient, Depends(get_keycloak_client)],
) -> dict[str, Any]:
    token = oauth_token or (bearer.credentials if bearer else None)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        signing_key = await keycloak.get_signing_key_from_jwt(token)
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
            await keycloak.ensure_token_active(token)
        return claims
    except AuthenticationServiceUnavailable as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service unavailable",
        ) from error
    except jwt.InvalidTokenError as error:
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

    async def __call__(self, current_user: CurrentUser) -> dict[str, Any]:
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
