from typing import Literal, Self

from pydantic import Field, HttpUrl, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    keycloak_server_url: HttpUrl = HttpUrl("http://localhost:8180")
    keycloak_internal_server_url: HttpUrl | None = None
    keycloak_realm: str = Field(default="fastapi-app", min_length=1)
    keycloak_client_id: str = Field(default="fastapi-docs", min_length=1)
    keycloak_audience: str = Field(default="fastapi-api", min_length=1)
    keycloak_validation_mode: Literal["jwt", "introspection"] = "jwt"
    keycloak_introspection_client_id: str = Field(
        default="fastapi-introspection", min_length=1
    )
    keycloak_introspection_client_secret: SecretStr | None = None
    keycloak_http_timeout_seconds: float = Field(default=10, gt=0, le=60)
    keycloak_jwks_cache_seconds: int = Field(default=300, ge=1)
    keycloak_clock_skew_seconds: int = Field(default=0, ge=0, le=300)

    @model_validator(mode="after")
    def validate_introspection_credentials(self) -> Self:
        if self.keycloak_validation_mode == "introspection" and (
            self.keycloak_introspection_client_secret is None
            or not self.keycloak_introspection_client_secret.get_secret_value()
        ):
            raise ValueError(
                "KEYCLOAK_INTROSPECTION_CLIENT_SECRET is required for introspection"
            )
        return self

    @property
    def issuer(self) -> str:
        return f"{str(self.keycloak_server_url).rstrip('/')}/realms/{self.keycloak_realm}"

    @property
    def internal_realm_url(self) -> str:
        server_url = self.keycloak_internal_server_url or self.keycloak_server_url
        return f"{str(server_url).rstrip('/')}/realms/{self.keycloak_realm}"


settings = Settings()
