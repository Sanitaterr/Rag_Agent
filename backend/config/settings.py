from functools import lru_cache
from pathlib import Path
import os

from dotenv import dotenv_values
from pydantic import BaseModel, Field


BASE_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = BASE_DIR / ".env"


def _env_value(name: str, default: str = "") -> str:
    """Read process env first, then backend/.env for local development."""
    file_values = dotenv_values(ENV_FILE) if ENV_FILE.exists() else {}
    return os.getenv(name) or str(file_values.get(name, default))


def _env_int(name: str, default: int) -> int:
    raw_value = _env_value(name, str(default))
    return int(raw_value) if raw_value else default


def _env_float(name: str, default: float) -> float:
    raw_value = _env_value(name, str(default))
    return float(raw_value) if raw_value else default


def _env_list(name: str, default: list[str]) -> list[str]:
    raw_value = _env_value(name, "")
    if not raw_value:
        return default
    return [item.strip() for item in raw_value.split(",") if item.strip()]


class Settings(BaseModel):
    """Centralized backend settings for DB, MQTT, and the DeepSeek chat model."""

    app_name: str = Field(default_factory=lambda: _env_value("APP_NAME", "LangGraph Agent Backend"))
    app_env: str = Field(default_factory=lambda: _env_value("APP_ENV", "local"))
    cors_origins: list[str] = Field(default_factory=lambda: _env_list("CORS_ORIGINS", ["*"]))

    mysql_host: str = Field(default_factory=lambda: _env_value("MYSQL_HOST", "localhost"))
    mysql_port: int = Field(default_factory=lambda: _env_int("MYSQL_PORT", 3306))
    mysql_user: str = Field(default_factory=lambda: _env_value("MYSQL_USER", "root"))
    mysql_password: str = Field(default_factory=lambda: _env_value("MYSQL_PASSWORD", ""))
    mysql_database: str = Field(default_factory=lambda: _env_value("MYSQL_DATABASE", "rag"))

    mqtt_host: str = Field(default_factory=lambda: _env_value("MQTT_HOST", "localhost"))
    mqtt_port: int = Field(default_factory=lambda: _env_int("MQTT_PORT", 1883))
    mqtt_topic: str = Field(default_factory=lambda: _env_value("MQTT_TOPIC", "factory/source/+/telemetry"))
    mqtt_client_id: str = Field(default_factory=lambda: _env_value("MQTT_CLIENT_ID", "rag-agent-backend"))
    mqtt_username: str = Field(default_factory=lambda: _env_value("MQTT_USERNAME", ""))
    mqtt_password: str = Field(default_factory=lambda: _env_value("MQTT_PASSWORD", ""))
    mqtt_keepalive: int = Field(default_factory=lambda: _env_int("MQTT_KEEPALIVE", 60))

    deepseek_api_key: str = Field(default_factory=lambda: _env_value("DEEPSEEK_API_KEY", ""))
    deepseek_base_url: str = Field(default_factory=lambda: _env_value("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
    deepseek_model: str = Field(default_factory=lambda: _env_value("DEEPSEEK_MODEL", "deepseek-v4-pro"))
    deepseek_tool_model: str = Field(default_factory=lambda: _env_value("DEEPSEEK_TOOL_MODEL", "deepseek-v4-pro"))
    deepseek_fallback_model: str = Field(default_factory=lambda: _env_value("DEEPSEEK_FALLBACK_MODEL", ""))
    deepseek_fallback_tool_model: str = Field(default_factory=lambda: _env_value("DEEPSEEK_FALLBACK_TOOL_MODEL", ""))
    llm_temperature: float = Field(default_factory=lambda: _env_float("LLM_TEMPERATURE", 0.2))
    llm_context_messages: int = Field(default_factory=lambda: _env_int("LLM_CONTEXT_MESSAGES", 12))
    llm_timeout_seconds: float = Field(default_factory=lambda: _env_float("LLM_TIMEOUT_SECONDS", 30.0))
    agent_recursion_limit: int = Field(default_factory=lambda: _env_int("AGENT_RECURSION_LIMIT", 12))
    agent_max_tool_rounds: int = Field(default_factory=lambda: _env_int("AGENT_MAX_TOOL_ROUNDS", 4))
    summary_trigger_messages: int = Field(default_factory=lambda: _env_int("SUMMARY_TRIGGER_MESSAGES", 20))
    summary_keep_messages: int = Field(default_factory=lambda: _env_int("SUMMARY_KEEP_MESSAGES", 5))
    tool_timeout_seconds: float = Field(default_factory=lambda: _env_float("TOOL_TIMEOUT_SECONDS", 10.0))
    tavily_api_key: str = Field(default_factory=lambda: _env_value("TAVILY_API_KEY", ""))
    tavily_search_url: str = Field(default_factory=lambda: _env_value("TAVILY_SEARCH_URL", "https://api.tavily.com/search"))
    tavily_search_depth: str = Field(default_factory=lambda: _env_value("TAVILY_SEARCH_DEPTH", "basic"))
    tavily_max_results: int = Field(default_factory=lambda: _env_int("TAVILY_MAX_RESULTS", 5))

    @property
    def mysql_async_url(self) -> str:
        """SQLAlchemy async MySQL connection URL."""
        return (
            f"mysql+asyncmy://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
            "?charset=utf8mb4"
        )

    @property
    def mysql_checkpoint_url(self) -> str:
        """LangGraph MySQL checkpointer connection URL."""
        return (
            f"mysql+asyncmy://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}"
            "?charset=utf8mb4"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
