# Agent Guide

## Project overview

This project integrates FastAPI with Keycloak authentication, Swagger OAuth2
login, realm and client roles, and service accounts. It uses Python 3.12+,
uv for dependency management, and Python 3.13 in the Docker image.

Read `README.md` for setup, local demo credentials, and authentication examples.

| File | Responsibility |
| --- | --- |
| `main.py` | FastAPI routes, Swagger configuration, and application lifespan |
| `auth.py` | Async authentication dependencies and role authorization |
| `keycloak_client.py` | Async JWKS retrieval, key caching, and introspection |
| `config.py` | Validated environment settings and public/internal Keycloak URLs |
| `keycloak/realm-export.json` | Development realm, clients, roles, and demo accounts |
| `Dockerfile` | uv-based backend image and non-root runtime |
| `compose.yaml` | Backend, Keycloak, readiness checks, and persistent data |
| `pyproject.toml`, `uv.lock` | Dependencies and their locked resolution |

## Async I/O requirements

- Keep routes and authentication dependencies `async def`.
- Await every request-time network, database, and filesystem operation.
- Reuse the lifespan-managed `httpx.AsyncClient` for Keycloak requests; close it
  asynchronously during shutdown.
- Do not introduce `requests`, synchronous `httpx.Client`, `httpx.get/post`,
  `urllib` networking, `PyJWKClient`, or `time.sleep` into request handling.
- Use native async I/O APIs rather than wrapping blocking I/O in worker threads.
- Keep JWKS cache coordination asynchronous with `asyncio.Lock`. Concurrent
  requests should share refresh work, while valid cached keys remain available.
- Preserve cache expiry and the 30-second unknown-key refresh cooldown. Do not
  accept expired cached keys when a refresh fails.
- Local JWT cryptography, claim parsing, and role comparisons are CPU operations
  and may remain ordinary function calls. Settings load once at initialization.

## Authentication invariants

- Validate RS256 signatures, the public issuer, the API audience, and time claims.
  Require `exp`, `iat`, `sub`, `iss`, and `aud`, and require token type `Bearer`.
- Retrieve keys only from the configured Keycloak endpoint, never from a URL
  supplied by an unverified token.
- Preserve authorization code flow with PKCE and `prompt=login` in Swagger.
  The Swagger client is public; never embed confidential-client secrets in it.
- Swagger login and pasted bearer tokens are alternative authorization methods.
  Both must pass the same token and role validation.
- Check realm roles and client roles separately. Client roles must belong to the
  configured API client, and `RequireRoles` requires all supplied roles.
- Return `401` with `WWW-Authenticate: Bearer` for missing or invalid tokens,
  `403` for insufficient roles, and `503` for authentication-provider failures.
- Introspection mode adds a server-side activity check after JWT validation.
  Never fall back to JWT-only acceptance when introspection fails.
- On Keycloak 26.7.3, the introspection client must also be in the token audience.
  Check audience mappers if a freshly issued token is reported inactive; preserve
  the API audience requirement when adding an introspection audience.
- Swagger Logout clears Swagger's token, not the Keycloak SSO session. Forced
  reauthentication is separate from ending a session or revoking access tokens.

## URLs and Docker networking

| Setting or service | Local value | Meaning |
| --- | --- | --- |
| Backend | `http://localhost:8001` | Host access; container listens on port 8000 |
| `KEYCLOAK_SERVER_URL` | `http://localhost:8180` | Browser URL and expected JWT issuer base |
| `KEYCLOAK_INTERNAL_SERVER_URL` | `http://keycloak:8080` in Compose | Backend JWKS and introspection requests |
| `KC_HOSTNAME` | Same as `KEYCLOAK_SERVER_URL` | Consistent public Keycloak issuer |

Leave the internal URL unset when running the backend on the host. Do not use
the Docker service hostname in browser authorization URLs or as the expected
public issuer. Keep Swagger callback URLs and web origins aligned with its port.

## Development commands

Run the full stack:

```sh
docker compose up --build -d
docker compose ps
docker compose logs -f backend
```

Run the backend on the host with Keycloak in Docker:

```sh
uv sync --locked
docker compose stop backend
docker compose up -d keycloak
uv run uvicorn main:app --reload --port 8001
```

Create `.env` from `.env.example` only if needed and if `.env` does not already
exist. Use the configured project interpreter for Python tooling; resolve the
IDE's Python environment first when its environment tools are available.

Use uv to manage dependencies and keep `pyproject.toml` and `uv.lock` consistent.
The Dockerfile copies application modules explicitly: update its `COPY` list
when adding modules required at runtime. Rebuild the image after source changes.
Preserve its non-root user and exclusion of local environments and secrets.

## Validation

There is currently no committed automated test suite or configured formatter.
Choose checks appropriate to the change and report exactly what was run.

```sh
git diff --check
docker compose config --quiet
```

For Docker changes, build the backend and verify Compose startup, readiness,
public Swagger URLs, and authenticated requests across the container network.
For authentication changes, verify missing/invalid/expired tokens, role boundaries,
key rotation, cache expiry, provider failures, and concurrent requests.

Use `httpx.AsyncClient` with `ASGITransport` for async API checks, and enter the
application lifespan so its shared Keycloak client is initialized and closed.
Include a responsiveness check while JWKS or introspection requests are waiting.
Do not weaken authentication or enable password grants merely to simplify tests.

## Change boundaries

- Keep changes focused and preserve existing user work.
- Use descriptive names, type hints, and the existing dependency-injection style.
- Update `README.md` and relevant environment/Compose settings when behavior changes.
- Never commit `.env`, real credentials, private keys, or captured access tokens.
  Bundled credentials and `start-dev` are for local development only.
- Keycloak imports skip an existing realm. Rebuilding containers does not apply
  realm-file changes to persisted data; plan explicit updates for existing realms.
- Preserve the Keycloak data volume. Do not run `docker compose down -v` or reset
  realm data unless the user explicitly requests that destructive operation.
- Do not commit or push changes unless requested.
