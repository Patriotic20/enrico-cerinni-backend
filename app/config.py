import os
from typing import Optional
from dotenv import load_dotenv
from pydantic import BaseModel, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class DatabaseConfig(BaseModel):
    database_url: str

    @property
    def sync_database_url(self) -> str:
        """Normalize DATABASE_URL for SQLAlchemy psycopg2 driver."""
        url = self.database_url
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return url


class JwtConfig(BaseModel):
    jwt_secret: str = "your-jwt-secret-key-here"
    jwt_refresh_secret: str = "your-refresh-secret-key-here"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7


class ServerConfig(BaseModel):
    port: int = 8000
    host: str = "0.0.0.0"
    debug: bool = True
    environment: str = "development"  # development, staging, production

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
    secret_key: str = "your-secret-key-here"
    bcrypt_rounds: int = 12


class RateLimitConfig(BaseModel):
    rate_limit_per_minute: int = 60


class NotificationConfig(BaseModel):
    telegram_bot_token: Optional[str] = None
    sms_provider: Optional[str] = ""
    sms_api_key: Optional[str] = None
    sms_from_number: Optional[str] = None
    sms_base_url: Optional[str] = None


# Flat environment variable names accepted as a fallback for the nested
# APP_CONFIG__<SECTION>__<FIELD> form. Deployment targets (Railway, docker-compose,
# env.example) supply the flat names, so both spellings must resolve.
FLAT_ENV_ALIASES = {
    "database": {"database_url": "DATABASE_URL"},
    "jwt": {
        "jwt_secret": "JWT_SECRET",
        "jwt_refresh_secret": "JWT_REFRESH_SECRET",
        "jwt_algorithm": "JWT_ALGORITHM",
        "access_token_expire_minutes": "ACCESS_TOKEN_EXPIRE_MINUTES",
        "refresh_token_expire_days": "REFRESH_TOKEN_EXPIRE_DAYS",
    },
    "server": {
        "port": "PORT",
        "host": "HOST",
        "debug": "DEBUG",
        "environment": "ENVIRONMENT",
    },
    "security": {
        "secret_key": "SECRET_KEY",
        "bcrypt_rounds": "BCRYPT_ROUNDS",
    },
    "rate_limit": {"rate_limit_per_minute": "RATE_LIMIT_PER_MINUTE"},
    "notification": {
        "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
        "sms_provider": "SMS_PROVIDER",
        "sms_api_key": "SMS_API_KEY",
        "sms_from_number": "SMS_FROM_NUMBER",
        "sms_base_url": "SMS_BASE_URL",
    },
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        env_nested_delimiter="__",
        env_prefix="APP_CONFIG__",
        extra="ignore",
    )

    database: DatabaseConfig
    jwt: JwtConfig = JwtConfig()
    server: ServerConfig = ServerConfig()
    security: SecurityConfig = SecurityConfig()
    rate_limit: RateLimitConfig = RateLimitConfig()
    notification: NotificationConfig = NotificationConfig()
    cors_origin: str = "http://localhost:3000"
    cors_origin_regex: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def apply_flat_env_aliases(cls, values):
        """Fill sections from flat env vars when the nested form is absent."""
        if not isinstance(values, dict):
            return values

        for section, fields in FLAT_ENV_ALIASES.items():
            section_values = dict(values.get(section) or {})
            for field, env_name in fields.items():
                if field in section_values and section_values[field] is not None:
                    continue
                env_value = os.getenv(env_name)
                if env_value is not None:
                    section_values[field] = env_value
            if section_values:
                values[section] = section_values

        for field, env_name in (
            ("cors_origin", "CORS_ORIGIN"),
            ("cors_origin_regex", "CORS_ORIGIN_REGEX"),
        ):
            if values.get(field) is None:
                env_value = os.getenv(env_name)
                if env_value is not None:
                    values[field] = env_value

        return values

    @property
    def cors_origins(self) -> list[str]:
        """CORS origins as a list; the env var holds a comma-separated string."""
        return [origin.strip() for origin in self.cors_origin.split(",") if origin.strip()]


settings = Settings()