from __future__ import annotations

import json
import logging
import time

from kafka import KafkaConsumer

from commit_pulse.chroma_sink import ChromaSink
from commit_pulse.clickhouse_sink import ClickHouseSink
from commit_pulse.config import get_settings
from commit_pulse.github_client import fetch_commit_diff
from commit_pulse.minio_sink import MinioSink
from commit_pulse.models import CommitEvent, parse_push_event
from commit_pulse.postgres_sink import PostgresSink

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def process_event(
    event: CommitEvent,
    payload: dict,
    sinks: dict,
    token: str,
    object_key: str,
) -> None:
    if not event.files:
        try:
            event.files = fetch_commit_diff(event.repo, event.sha, token)
        except Exception as exc:
            logger.warning("diff enrichment failed for %s: %s — continuing without patches", event.sha, exc)

    sinks["minio"].archive_raw(payload, object_key)
    sinks["postgres"].upsert_commit(event)
    sinks["clickhouse"].upsert_commit(event)
    sinks["chroma"].upsert_commit(event)


def main() -> None:
    settings = get_settings()
    sinks = {
        "minio": MinioSink(settings),
        "postgres": PostgresSink(settings),
        "clickhouse": ClickHouseSink(settings),
        "chroma": ChromaSink(settings),
    }
    consumer = KafkaConsumer(
        settings.kafka_topic,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id="commit-pulse-processor",
        auto_offset_reset="earliest",
        enable_auto_commit=True,
        value_deserializer=lambda b: json.loads(b.decode("utf-8")),
    )
    logger.info("listening on topic %s", settings.kafka_topic)
    for message in consumer:
        payload = message.value
        try:
            source = payload.get("_source", "webhook")
            events = parse_push_event(payload, source=source)
            for event in events:
                object_key = f"{event.repo.replace('/', '__')}/{event.sha}.json"
                process_event(event, payload, sinks, settings.github_token, object_key)
        except Exception as exc:
            logger.exception("failed to process message: %s", exc)
            time.sleep(5)


if __name__ == "__main__":
    main()
