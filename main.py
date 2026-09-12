from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI

from auth import CurrentUser, RequireRoles
from config import settings
from keycloak_client import AsyncKeycloakClient


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    async with httpx.AsyncClient(
        timeout=settings.keycloak_http_timeout_seconds,
        follow_redirects=False,
    ) as http_client:
        application.state.keycloak_client = AsyncKeycloakClient(http_client, settings)
        yield


app = FastAPI(
    lifespan=lifespan,
    title="Keycloak FastAPI",
    description=(
        "Use **Authorize → Keycloak** for browser login, or **BearerToken** to paste "
        "an existing access token. Authorize one method at a time. "
        "Admin and client-role examples return 403 when the required role is missing."
    ),
    swagger_ui_init_oauth={
        "clientId": settings.keycloak_client_id,
        "usePkceWithAuthorizationCodeGrant": True,
        "scopes": "openid profile",
        "additionalQueryStringParams": {"prompt": "login"},
    },
)


@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.get("/hello/{name}")
async def say_hello(name: str, current_user: CurrentUser):
    return {"message": f"Hello {name}"}


@app.get("/users/me")
async def read_current_user(current_user: CurrentUser):
    return {
        "id": current_user["sub"],
        "username": current_user.get("preferred_username"),
        "name": current_user.get("name"),
        "roles": current_user.get("realm_access", {}).get("roles", []),
        "client_roles": current_user.get("resource_access", {}),
    }


@app.get("/admin", tags=["Role-based access"], summary="Requires realm role: admin")
async def admin_only(
    current_user: Annotated[dict, Depends(RequireRoles("admin"))],
):
    return {"message": "Admin access granted", "user_id": current_user["sub"]}


@app.get(
    "/reports",
    tags=["Role-based access"],
    summary="Requires API client role: reports:read",
)
async def read_reports(
    current_user: Annotated[
        dict, Depends(RequireRoles("reports:read", client_id=settings.keycloak_audience))
    ],
):
    return {"reports": [], "user_id": current_user["sub"]}


@app.get(
    "/service/status",
    tags=["Service accounts"],
    summary="Requires API client role: service:read",
    description="Try this endpoint with a fastapi-service client-credentials token.",
)
async def service_status(
    current_user: Annotated[
        dict, Depends(RequireRoles("service:read", client_id=settings.keycloak_audience))
    ],
):
    return {"status": "ok", "client_id": current_user.get("azp")}
