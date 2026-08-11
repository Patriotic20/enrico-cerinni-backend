import os
from typing import Optional
from dotenv import load_dotenv
from pydantic import BaseModel, field_validator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


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
    jwt_secret: str = "your-jwt-secret-key-here"
    jwt_refresh_secret: str = "your-refresh-secret-key-here"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7


class ServerConfig(BaseModel):
    # Прямая связь со стандартным портом Railway
    port: int = Field(default=8000, validation_alias="PORT")
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
    jwt: JwtConfig = Field(default_factory=JwtConfig)
    server: ServerConfig = Field(
        default_factory=lambda: ServerConfig.model_validate(dict(os.environ))
    )
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    notification: NotificationConfig = Field(default_factory=NotificationConfig)
    cors_origin: str = "http://localhost:3001"
    admin_email: str = "admin@example.com"
    admin_username: str = "admin"
    admin_password: str = "admin123"
    cookie_samesite: str = "lax"
    cookie_secure: bool = True


settings = Settings()
