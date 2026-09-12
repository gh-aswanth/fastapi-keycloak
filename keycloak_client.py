import asyncio
from time import monotonic

import httpx
import jwt

from config import Settings


class AuthenticationServiceUnavailable(Exception):
    pass


class AsyncKeycloakClient:
    def __init__(self, http_client: httpx.AsyncClient, settings: Settings):
        self.http_client = http_client
        self.settings = settings
        self._signing_keys: dict[str, jwt.PyJWK] = {}
        self._expires_at = 0.0
        self._refresh_after = 0.0
        self._refresh_lock = asyncio.Lock()

    async def _refresh_signing_keys(self) -> None:
        try:
            response = await self.http_client.get(
                f"{self.settings.internal_realm_url}/protocol/openid-connect/certs"
            )
            response.raise_for_status()
            data = response.json()
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("keys"), list)
                or any(not isinstance(key, dict) for key in data["keys"])
            ):
                raise ValueError("Invalid JWKS response")
            key_set = jwt.PyJWKSet(
                [
                    key
                    for key in data["keys"]
                    if key.get("kty") == "RSA"
                    and key.get("alg") in ("RS256", None)
                    and key.get("use") in ("sig", None)
                ]
            )
            signing_keys = {
                key.key_id: key
                for key in key_set.keys
                if isinstance(key.key_id, str)
                and key.key_id
            }
            if not signing_keys:
                raise ValueError("No usable RS256 signing keys")
        except (httpx.HTTPError, ValueError, TypeError, jwt.PyJWTError) as error:
            raise AuthenticationServiceUnavailable("Unable to load signing keys") from error
        fetched_at = monotonic()
        self._signing_keys = signing_keys
        self._expires_at = fetched_at + self.settings.keycloak_jwks_cache_seconds
        self._refresh_after = fetched_at + 30

    async def get_signing_key_from_jwt(self, token: str) -> jwt.PyJWK:
        header = jwt.get_unverified_header(token)
        key_id = header.get("kid")
        if header.get("alg") != "RS256" or not isinstance(key_id, str) or not key_id:
            raise jwt.InvalidTokenError("Expected an RS256 token with a signing key ID")
        signing_key = self._signing_keys.get(key_id)
        if signing_key is not None and monotonic() < self._expires_at:
            return signing_key
        async with self._refresh_lock:
            now = monotonic()
            if now >= self._expires_at or (
                key_id not in self._signing_keys and now >= self._refresh_after
            ):
                await self._refresh_signing_keys()
            signing_key = self._signing_keys.get(key_id)
            if signing_key is None:
                raise jwt.InvalidTokenError("Unknown signing key")
            return signing_key

    async def ensure_token_active(self, token: str) -> None:
        secret = self.settings.keycloak_introspection_client_secret
        if secret is None or not secret.get_secret_value():
            raise AuthenticationServiceUnavailable("Introspection credentials are missing")
        try:
            response = await self.http_client.post(
                f"{self.settings.internal_realm_url}/protocol/openid-connect/token/introspect",
                data={"token": token, "token_type_hint": "access_token"},
                auth=(
                    self.settings.keycloak_introspection_client_id,
                    secret.get_secret_value(),
                ),
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict) or not isinstance(result.get("active"), bool):
                raise ValueError("Invalid introspection response")
        except (httpx.HTTPError, ValueError) as error:
            raise AuthenticationServiceUnavailable("Unable to introspect token") from error
        if not result["active"]:
            raise jwt.InvalidTokenError("Access token is no longer active")
