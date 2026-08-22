from __future__ import annotations

import json
import logging

from kafka import KafkaProducer

from .config import Settings

logger = logging.getLogger(__name__)


def make_producer(settings: Settings) -> KafkaProducer:
    return KafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        acks="all",
    )


def publish_push_payload(producer: KafkaProducer, settings: Settings, payload: dict) -> None:
    producer.send(settings.kafka_topic, value=payload).get(timeout=30)
    producer.flush()
    logger.info("published payload to topic %s", settings.kafka_topic)
