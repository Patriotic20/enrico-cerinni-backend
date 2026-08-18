import os
import secrets
from typing import Optional
from dotenv import load_dotenv
from pydantic import AliasChoices, BaseModel, field_validator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()

# Placeholder values shipped in env.example and as docker-compose defaults. They
# are public, so a deployment that still carries them can have its tokens forged
# by anyone who has read the repository.
PLACEHOLDER_SECRETS = frozenset(
    {
        "your-jwt-secret-key-here",
        "your-refresh-secret-key-here",
        "your-secret-key-here",
    }
)


def _env_field(default, *env_names: str):
    """Field readable both from an UPPERCASE env var and by its own name.

    The nested configs below are built from `os.environ` directly, so without an
    alias a field named `jwt_secret` never matches the documented `JWT_SECRET`
    variable and silently keeps its default. Keeping the field name among the
    alias choices preserves the `JWT__JWT_SECRET` nested-delimiter form too.
    """
    return Field(default=default, validation_alias=AliasChoices(*env_names))


class DatabaseConfig(BaseModel):
    # Прямая связь со стандартной переменной Railway
    database_url: str = Field(validation_alias="DATABASE_URL")

    @property
    def sync_database_url(self) -> str:
        """Normalize DATABASE_URL for SQLAlchemy psycopg2 driver."""
        url = self.database_url
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return url


class JwtConfig(BaseModel):
    jwt_secret: str = _env_field(
        "your-jwt-secret-key-here", "JWT_SECRET", "jwt_secret"
    )
    jwt_refresh_secret: str = _env_field(
        "your-refresh-secret-key-here", "JWT_REFRESH_SECRET", "jwt_refresh_secret"
    )
    jwt_algorithm: str = _env_field("HS256", "JWT_ALGORITHM", "jwt_algorithm")
    access_token_expire_minutes: int = _env_field(
        30, "ACCESS_TOKEN_EXPIRE_MINUTES", "access_token_expire_minutes"
    )
    refresh_token_expire_days: int = _env_field(
        7, "REFRESH_TOKEN_EXPIRE_DAYS", "refresh_token_expire_days"
    )


class ServerConfig(BaseModel):
    # Прямая связь со стандартным портом Railway
    port: int = _env_field(8000, "PORT", "port")
    host: str = _env_field("0.0.0.0", "HOST", "host")
    debug: bool = _env_field(True, "DEBUG", "debug")
    # development, staging, production
    environment: str = _env_field("development", "ENVIRONMENT", "environment")

    @property
    def is_production(self) -> bool:
        """Check if we're running in production environment."""
        return self.environment.lower() == "production"

    @property
    def is_development(self) -> bool:
        """Check if we're running in development environment."""
        return self.environment.lower() == "development"

    @field_validator("debug", mode="before")
    @classmethod
    def parse_debug(cls, v):
        if isinstance(v, str):
            return v.lower() == "true"
        return v


class SecurityConfig(BaseModel):
    secret_key: str = _env_field("your-secret-key-here", "SECRET_KEY", "secret_key")
    bcrypt_rounds: int = _env_field(12, "BCRYPT_ROUNDS", "bcrypt_rounds")


class RateLimitConfig(BaseModel):
    rate_limit_per_minute: int = _env_field(
        60, "RATE_LIMIT_PER_MINUTE", "rate_limit_per_minute"
    )


class NotificationConfig(BaseModel):
    telegram_bot_token: Optional[str] = None
    sms_provider: Optional[str] = ""  # "eskiz" or empty for the generic HTTP provider
    sms_api_key: Optional[str] = None
    sms_from_number: Optional[str] = None
    sms_base_url: Optional[str] = None
    # Eskiz.uz cabinet credentials (my.eskiz.uz); the API token is issued from these
    eskiz_email: Optional[str] = None
    eskiz_password: Optional[str] = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        env_nested_delimiter="__",  # ОСТАВЛЯЕМ, чтобы работали вложенные переменные
        extra="ignore",
    )

    # Автоматическая сборка объектов при старте
    # default_factory lambdas capture os.environ at instantiation time (not import
    # time) by calling dict(os.environ) inside the lambda body, so Railway's
    # injected DATABASE_URL and PORT are always present when Settings() is called.
    database: DatabaseConfig = Field(
        default_factory=lambda: DatabaseConfig.model_validate(dict(os.environ))
    )
    jwt: JwtConfig = Field(
        default_factory=lambda: JwtConfig.model_validate(dict(os.environ))
    )
    server: ServerConfig = Field(
        default_factory=lambda: ServerConfig.model_validate(dict(os.environ))
    )
    security: SecurityConfig = Field(
        default_factory=lambda: SecurityConfig.model_validate(dict(os.environ))
    )
    rate_limit: RateLimitConfig = Field(
        default_factory=lambda: RateLimitConfig.model_validate(dict(os.environ))
    )
    notification: NotificationConfig = Field(default_factory=NotificationConfig)
    cors_origin: str = "http://localhost:3001"
    admin_email: str = "admin@enrico.uz"
    admin_username: str = "admin"
    admin_password: str = "admin123"
    # Re-apply admin_password to the existing admin on startup. Off by default so
    # a password changed in the running system survives a restart; turn it on for
    # one boot to recover a lost admin password.
    admin_force_reset: bool = False
    # Fill an empty database with demo products, clients and debts. Off by default
    # so a fresh production database never gets fake records mixed into real ones.
    seed_mock_data: bool = False
    cookie_samesite: str = "lax"
    cookie_secure: bool = True


settings = Settings()


def _replace_placeholder_secrets(cfg: Settings) -> None:
    """Swap public placeholder signing keys for per-process random ones.

    Refusing to boot would take a running shop offline, so instead the tokens are
    made unforgeable immediately; the cost is that everyone is logged out on each
    restart until real secrets are configured.
    """
    for attr, env_name in (
        ("jwt_secret", "JWT_SECRET"),
        ("jwt_refresh_secret", "JWT_REFRESH_SECRET"),
    ):
        if getattr(cfg.jwt, attr) in PLACEHOLDER_SECRETS:
            setattr(cfg.jwt, attr, secrets.token_urlsafe(64))
            print(
                f"⚠️  {env_name} is still the public placeholder from env.example. "
                f"Using a random key for this process — set {env_name} to a strong "
                "secret, otherwise every restart logs all users out.",
                flush=True,
            )


_replace_placeholder_secrets(settings)
