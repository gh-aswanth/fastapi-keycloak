# FastAPI with Keycloak

Keycloak handles login. FastAPI validates access-token signatures using Keycloak's
cached public keys, and checks the issuer, audience, expiry, and token type.
Swagger UI uses the OAuth2 authorization code flow with PKCE; no client secret is
needed in the browser.

## Authentication options

| Option | Use case | How to use it |
| --- | --- | --- |
| Browser login with PKCE | People using Swagger or a frontend | **Authorize → Keycloak** |
| Bearer access token | Tokens obtained by other clients | **Authorize → BearerToken**, paste the token without `Bearer ` |
| Client credentials | Backend services with no interactive user | Request a token using the `fastapi-service` client below |

Authorize one method at a time in Swagger; both methods send the same
`Authorization` header. Every access token receives the same signature, issuer,
audience, expiry, and role checks, regardless of how it was obtained.

## Run locally

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), and Docker Compose.

```sh
uv sync --locked
cp .env.example .env
docker compose up -d
uv run uvicorn main:app --reload --port 8001
```

Wait for Keycloak to start (check `docker compose logs -f keycloak`), then:

1. Open <http://localhost:8001/docs> and click **Authorize**.
2. Leave client ID as `fastapi-docs`, leave client secret empty, and select
   `openid` and `profile` if they are not already selected.
3. Click **Authorize** in the dialog and sign in to Keycloak with username
   `demo` and password `demo-password`.
4. Close the authorization dialog and try `GET /users/me` or `GET /hello/{name}`.
   Swagger sends the access token automatically.

`GET /` is public. The hello and current-user endpoints require a valid bearer
access token and return `401` otherwise. `/docs` and `/openapi.json` remain public
so users can initiate login. An unreachable Keycloak key endpoint returns `503`
when no usable cached signing key is available.

Other clients can call protected endpoints with their Keycloak access token:

```sh
curl http://localhost:8001/users/me \
  -H "Authorization: Bearer $ACCESS_TOKEN"
```

The local Keycloak admin console is <http://localhost:8180/admin>, with username
`admin` and password `admin`. This Compose setup and its credentials are for local
development only. `docker compose down` stops Keycloak and preserves its data.
Realm imports only apply when the realm does not already exist; update an existing
realm through the admin console after changing the import file.

## Login and logout in Swagger

Swagger's **Logout** removes the token from Swagger. Keycloak keeps its own SSO
session in a browser cookie, so it can normally sign you back in without asking
for credentials. This is separate from FastAPI's public-key cache.

Swagger now sends `prompt=login` on every authorization request. After **Logout**,
clicking **Authorize** requires authentication at Keycloak again, even if its SSO
session still exists. Reload `/docs` after updating the application to load this
setting. If Keycloak remembers the username, use **Restart login** beside it to
sign in as another user.

This forces reauthentication; it does not end the Keycloak SSO session or revoke
previously issued tokens. To end that session, sign out from the Keycloak account
console at <http://localhost:8180/realms/fastapi-app/account>. Locally validated JWTs
can remain usable until expiry; see the introspection option below.

## Realm roles and client roles

| Endpoint | Required permission | Demo access |
| --- | --- | --- |
| `/users/me`, `/hello/{name}` | Any valid access token | All three accounts |
| `/admin` | Realm role `admin` | `demo-admin` |
| `/reports` | `fastapi-api` client role `reports:read` | `demo`, `demo-admin` |
| `/service/status` | `fastapi-api` client role `service:read` | `fastapi-service` service account |

The application admin login is `demo-admin` / `demo-admin-password`. It is
separate from the Keycloak administration account. A valid token with missing
roles returns `403`; a missing or invalid token returns `401`. Realm roles and
client roles are checked separately, so a similarly named role belonging to a
different client grants no access. Multiple roles passed to `RequireRoles` must
all be present.

Reuse the dependency on your own endpoints:

```python
from typing import Annotated

from fastapi import Depends

from auth import RequireRoles


@app.get("/management")
async def management(user: Annotated[dict, Depends(RequireRoles("admin"))]):
    return {"user_id": user["sub"]}
```

Use `RequireRoles("reports:read", client_id="fastapi-api")` for client roles.
`/reports` returns an empty example report list; replace it with your application
logic. The `/service/status` example checks a role, so another client or human
assigned that role can access it too.

## Service accounts

The bundled confidential `fastapi-service` client has only the `service:read`
API role. Obtain a token from a trusted backend or your terminal:

```sh
export SERVICE_CLIENT_SECRET=local-service-secret-change-me
curl --fail-with-body \
  http://localhost:8180/realms/fastapi-app/protocol/openid-connect/token \
  --data-urlencode grant_type=client_credentials \
  --data-urlencode client_id=fastapi-service \
  --data-urlencode "client_secret=$SERVICE_CLIENT_SECRET"
```

Copy the response's `access_token` into Swagger's **BearerToken** field, or set
`ACCESS_TOKEN` and call:

```sh
curl http://localhost:8001/service/status \
  -H "Authorization: Bearer $ACCESS_TOKEN"
```

Keep service-client secrets in backend secret storage. The browser login client
remains public and does not need a secret. Service accounts obtain another token
with client credentials when their token expires.

## Validation options

| Mode | Behavior | Tradeoff |
| --- | --- | --- |
| `jwt` (default) | Validates signed JWTs locally using cached Keycloak public keys | Fast; a signed token can remain usable until expiry after logout |
| `introspection` | Performs the same JWT checks and asks Keycloak whether the token is active on every request | Checks server-side activity; needs a reachable Keycloak and an additional HTTP request |

To enable introspection with the local realm, set these in `.env` and restart
FastAPI:

```dotenv
KEYCLOAK_VALIDATION_MODE=introspection
KEYCLOAK_INTROSPECTION_CLIENT_ID=fastapi-introspection
KEYCLOAK_INTROSPECTION_CLIENT_SECRET=local-introspection-secret-change-me
```

This uses a separate confidential client for validation. These settings are
server-side only. An inactive token returns `401`. Network errors, invalid
introspection credentials, or malformed Keycloak responses return `503`; the
application does not fall back to accepting tokens when introspection fails.
This mode accepts signed Keycloak JWT access tokens, not opaque tokens.

Additional settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `KEYCLOAK_HTTP_TIMEOUT_SECONDS` | `10` | Timeout for key retrieval and introspection |
| `KEYCLOAK_JWKS_CACHE_SECONDS` | `300` | Public-key cache lifetime; an unknown key ID triggers a refresh |
| `KEYCLOAK_CLOCK_SKEW_SECONDS` | `0` | Allowed clock difference for JWT time claims, up to 300 seconds |

## Update an existing development realm

For a new realm the import includes all accounts, roles, and clients. An existing
realm is preserved by `--import-realm`, so restarting its container alone does
not add these examples. In the Keycloak admin console for `fastapi-app`:

1. Create realm role `admin`. Under client `fastapi-api`, create client roles
   `reports:read` and `service:read`.
2. Assign `reports:read` to `demo`. Create `demo-admin`, set its password, and
   assign realm roles `user` and `admin`, plus client role `reports:read`.
3. Create confidential client `fastapi-service`, enable service accounts, disable
   standard flow and direct access grants, and assign `service:read` to its service
   account. Keep default client scopes `basic` and `roles`, and add the
   `fastapi-api` access-token audience mapper shown in `realm-export.json`.
4. Create confidential client `fastapi-introspection` with interactive flows and
   service accounts disabled. Copy its generated secret into the FastAPI setting
   if enabling introspection. Use the service client's generated secret for token
   requests; the literal secrets above are only the bundled import's defaults.
5. Ensure `fastapi-docs` allows the exact callback URL and web origin for the
   FastAPI port you use. The import allows local ports 8000 and 8001.

Obtain fresh tokens after changing roles. See **Login and logout in Swagger** for
reauthentication, switching demo users, and ending the Keycloak SSO session.

Keycloak can also provide MFA, passkeys, social login, and LDAP federation through
its authentication and identity-provider configuration. They use the same browser
login integration; they are not enabled by this example.

## Use an existing Keycloak server

Set these values in `.env` or the process environment:

| Variable | Default | Purpose |
| --- | --- | --- |
| `KEYCLOAK_SERVER_URL` | `http://localhost:8180` | Base URL, including a path prefix if used |
| `KEYCLOAK_REALM` | `fastapi-app` | Realm issuing access tokens |
| `KEYCLOAK_CLIENT_ID` | `fastapi-docs` | Public Swagger OAuth client |
| `KEYCLOAK_AUDIENCE` | `fastapi-api` | Required access-token audience |

In that realm, create a public OpenID Connect client with standard flow enabled,
PKCE method `S256`, and direct access grants disabled. Register the exact Swagger
callback URL (`http://localhost:8001/docs/oauth2-redirect` locally) and its web
origin (`http://localhost:8001`). Add an audience mapper that includes
`KEYCLOAK_AUDIENCE` in access tokens. The bundled realm import provides an example.
Keep the built-in `basic`, `profile`, and `roles` default client scopes assigned
so access tokens include the subject, profile, and realm roles.
Use RS256 to sign tokens. The configured issuer must match the token's `iss`
exactly and be reachable by both the browser and FastAPI; consistently use
`localhost` for the bundled Keycloak server.

For deployment, use HTTPS, deployment-specific callback URLs and origins, secure
credentials, and a production Keycloak deployment with an external database.
