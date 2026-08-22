from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def load_env() -> None:
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env", override=False)


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _optional(name: str, default: str) -> str:
    return os.getenv(name, default).strip() or default


@dataclass(frozen=True)
class Settings:
    kafka_bootstrap_servers: str
    kafka_topic: str
    minio_endpoint: str
    minio_root_user: str
    minio_root_password: str
    minio_bucket: str
    postgres_pooled_url: str
    clickhouse_host: str
    clickhouse_port: int
    clickhouse_user: str
    clickhouse_password: str
    clickhouse_db: str
    chroma_host: str
    chroma_port: int
    chroma_collection: str
    github_token: str
    github_webhook_secret: str


def get_settings(require_webhook_secret: bool = False) -> Settings:
    load_env()
    webhook_secret = os.getenv("GITHUB_WEBHOOK_SECRET", "").strip()
    if require_webhook_secret and not webhook_secret:
        raise RuntimeError("GITHUB_WEBHOOK_SECRET is required for webhook receiver")
    return Settings(
        kafka_bootstrap_servers=_optional("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
        kafka_topic=_optional("KAFKA_TOPIC", "raw-commits"),
        minio_endpoint=_optional("MINIO_ENDPOINT", "http://localhost:9000"),
        minio_root_user=_required("MINIO_ROOT_USER"),
        minio_root_password=_required("MINIO_ROOT_PASSWORD"),
        minio_bucket=_required("MINIO_BUCKET"),
        postgres_pooled_url=_required("POSTGRES_POOLED_URL"),
        clickhouse_host=_optional("CLICKHOUSE_HOST", "localhost"),
        clickhouse_port=int(_optional("CLICKHOUSE_PORT", "8123")),
        clickhouse_user=_optional("CLICKHOUSE_USER", "commitpulse"),
        clickhouse_password=_optional("CLICKHOUSE_PASSWORD", "commitpulse"),
        clickhouse_db=_optional("CLICKHOUSE_DB", "commitpulse"),
        chroma_host=_optional("CHROMA_HOST", "localhost"),
        chroma_port=int(_optional("CHROMA_PORT", "8001")),
        chroma_collection=_optional("CHROMA_COLLECTION", "commits"),
        github_token=os.getenv("GITHUB_TOKEN", "").strip(),
        github_webhook_secret=webhook_secret,
    )
