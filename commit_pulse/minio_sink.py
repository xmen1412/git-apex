from __future__ import annotations

import json
import logging

from minio import Minio

from .config import Settings

logger = logging.getLogger(__name__)


class MinioSink:
    def __init__(self, settings: Settings):
        endpoint = settings.minio_endpoint.replace("http://", "").replace("https://", "")
        secure = settings.minio_endpoint.startswith("https://")
        self.client = Minio(endpoint, access_key=settings.minio_root_user,
                            secret_key=settings.minio_root_password, secure=secure)
        self.bucket = settings.minio_bucket

    def archive_raw(self, payload: dict, object_key: str) -> None:
        import io
        data = json.dumps(payload).encode("utf-8")
        self.client.put_object(
            self.bucket,
            object_key,
            io.BytesIO(data),
            length=len(data),
            content_type="application/json",
        )
        logger.info("archived raw payload to minio://%s/%s", self.bucket, object_key)
