from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    database_url: str = "postgresql+asyncpg://massclaw:massclaw@localhost:5432/massclaw"
    database_sync_url: str = "postgresql://massclaw:massclaw@localhost:5432/massclaw"
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_pool_timeout: int = 30
    db_pool_recycle: int = 1800

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Security
    jwt_secret_key: str = "change-me-in-production-use-a-secure-random-string"
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 60
    auth_required: bool = True  # Set to False in local development to skip auth

    # LLM Providers
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # LLM Resilience
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 3

    # Tool System
    tool_max_iterations: int = 5
    tool_code_timeout_seconds: int = 30
    tool_code_memory_mb: int = 256
    tool_workspace_base: str = "/tmp/massclaw/workspaces"
    brave_search_api_key: str = ""
    tool_docker_image: str = "python:3.12-slim"

    # Agent Protocol
    mcp_server_enabled: bool = True
    mcp_client_enabled: bool = True
    agent_message_ttl_seconds: int = 3600
    agent_message_max_size_bytes: int = 100000
    websocket_agent_heartbeat_seconds: int = 30
    websocket_agent_reconnect_delay_seconds: int = 5

    # Embedding Model
    embedding_model: str = "all-MiniLM-L6-v2"
    embedding_dimensions: int = 384

    # Logging
    log_level: str = "INFO"
    log_format: str = "console"  # "console" or "json"

    # CORS
    cors_origins: list[str] = ["http://localhost:3000"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            return json.loads(v)
        return v

    # Rate Limiting
    rate_limit_requests: int = 100
    rate_limit_window_seconds: int = 60

    # Celery
    celery_broker_url: str = "redis://localhost:6379/3"
    celery_result_backend: str = "redis://localhost:6379/3"

    # Health Check
    health_check_interval_seconds: int = 60
    health_check_timeout_seconds: int = 5
    health_check_degraded_threshold: int = 3
    health_check_suspended_threshold: int = 5

    # Trust
    trust_decay_rate: float = 0.01
    trust_decay_interval_hours: int = 24
    trust_learning_rate: float = 0.3
    trust_score_weights: dict[str, float] = {
        "quality": 0.35,
        "speed": 0.15,
        "cost": 0.15,
        "consistency": 0.15,
        "reliability": 0.20,
    }

    @field_validator("trust_score_weights", mode="before")
    @classmethod
    def parse_trust_weights(cls, v: Any) -> dict[str, float]:
        if isinstance(v, str):
            return json.loads(v)
        return v

    # Memory
    memory_gc_interval_hours: int = 6
    memory_default_ttl_hours: int = 168
    memory_freshness_decay_lambda: float = 0.01
    memory_min_similarity_threshold: float = 0.5

    # Rate Limit Behavior
    rate_limit_fail_open: bool = False  # When True, allow requests if Redis is down

    # Application
    app_name: str = "MassClaw"
    app_version: str = "1.0.0"
    debug: bool = False

    def validate_production_settings(self) -> None:
        """Validate settings for production safety. Called during app startup."""
        _INSECURE_JWT_DEFAULT = "change-me-in-production-use-a-secure-random-string"
        if self.auth_required and self.jwt_secret_key == _INSECURE_JWT_DEFAULT:
            raise RuntimeError(
                "FATAL: JWT_SECRET_KEY must be set to a secure value when AUTH_REQUIRED=true. "
                "Generate one with: openssl rand -hex 32"
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
