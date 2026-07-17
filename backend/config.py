"""Application configuration: paths, defaults, config models, and persistence."""

import json
from pathlib import Path

from pydantic import BaseModel, Field


class ConfigError(RuntimeError):
    """Raised when the config file cannot be read or written.

    Kept free of FastAPI so this module stays usable from background workers;
    main.py translates it into an HTTP 500 for request contexts.
    """


APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
DATA_DIR = PROJECT_DIR / "data"
CONFIG_PATH = DATA_DIR / "config.json"

DEFAULT_ENDPOINT_URL = "http://localhost:8000"
DEFAULT_API_KEY = "EMPTY"
DEFAULT_DEPLOYMENT_PORT = 8000
DEFAULT_TEE_DB_PATH = "data/request_tee.sqlite3"
DEFAULT_MAX_BODY_BYTES = 1_000_000
DEFAULT_OPTIMIZATION_MAX_MP = 0.0
VALIDATION_SCHEMA_TYPES = {"string", "number", "integer", "boolean", "object", "array"}


class ProxyConfig(BaseModel):
    base_url: str = DEFAULT_ENDPOINT_URL
    api_key: str = DEFAULT_API_KEY
    capture_bodies: bool = True
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    db_path: str = DEFAULT_TEE_DB_PATH


class SchemaField(BaseModel):
    name: str = ""
    type: str = "string"
    validator_code: str = ""


class ValidationConfig(BaseModel):
    fields: list[SchemaField] = Field(default_factory=list)


class DeploymentConfig(BaseModel):
    port: int = DEFAULT_DEPLOYMENT_PORT


class OptimizationConfig(BaseModel):
    max_mp: float = DEFAULT_OPTIMIZATION_MAX_MP


class AppConfig(BaseModel):
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    deployment: DeploymentConfig = Field(default_factory=DeploymentConfig)
    optimization: OptimizationConfig = Field(default_factory=OptimizationConfig)


def normalize_endpoint_url(url: str) -> str:
    normalized = url.strip().rstrip("/")
    if normalized.endswith("/v1"):
        normalized = normalized[:-3].rstrip("/")
    return normalized or DEFAULT_ENDPOINT_URL


def normalize_body_limit(value: int) -> int:
    return max(0, min(value, 50_000_000))


def normalize_db_path(path: str) -> str:
    normalized = path.strip() or DEFAULT_TEE_DB_PATH
    candidate = Path(normalized)
    if candidate.is_absolute():
        return str(candidate)
    return str(PROJECT_DIR / candidate)


def normalize_app_config(config: AppConfig) -> AppConfig:
    config.proxy.base_url = normalize_endpoint_url(config.proxy.base_url)
    config.proxy.api_key = config.proxy.api_key.strip() or DEFAULT_API_KEY
    config.proxy.max_body_bytes = normalize_body_limit(config.proxy.max_body_bytes)
    config.proxy.db_path = normalize_db_path(config.proxy.db_path)
    config.deployment.port = max(1, min(config.deployment.port, 65_535))
    config.validation.fields = [
        SchemaField(
            name=field.name.strip(),
            type=field.type if field.type in VALIDATION_SCHEMA_TYPES else "string",
            validator_code=field.validator_code,
        )
        for field in config.validation.fields
        if field.name.strip()
    ]
    config.optimization.max_mp = max(0.0, config.optimization.max_mp)
    return config


def read_app_config() -> AppConfig:
    if not CONFIG_PATH.exists():
        return normalize_app_config(AppConfig())

    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return normalize_app_config(AppConfig.model_validate(raw))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ConfigError(f"Could not read config file: {exc}") from exc


def write_app_config(config: AppConfig) -> AppConfig:
    normalized = normalize_app_config(config)
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(normalized.model_dump_json(indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Could not write config file: {exc}") from exc
    return normalized
