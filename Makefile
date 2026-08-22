.PHONY: up down logs ps schema verify reset

up:
	docker compose up -d
	@echo "Waiting for init containers..."
	@docker compose logs kafka-init minio-init

down:
	docker compose down

logs:
	docker compose logs -f

ps:
	docker compose ps

# Applies the Postgres schema to Neon. Uses the DIRECT (unpooled) URL —
# the pooled endpoint runs PgBouncer in transaction mode, which breaks
# prepared statements and can time out on migrations.
# Requires psql installed locally.
schema:
	@set -a && . ./.env && set +a && \
		psql "$$POSTGRES_DIRECT_URL" -f infra/postgres/01-schema.sql

# Confirms every service is actually reachable and schemas exist.
verify:
	@echo "--- Kafka topics ---"
	@docker compose exec kafka kafka-topics --bootstrap-server kafka:29092 --list
	@echo "--- MinIO buckets ---"
	@docker compose run --rm --entrypoint sh minio-init -c \
		'mc alias set local http://minio:9000 $$MINIO_ROOT_USER $$MINIO_ROOT_PASSWORD >/dev/null && mc ls local'
	@echo "--- Neon Postgres tables ---"
	@set -a && . ./.env && set +a && psql "$$POSTGRES_DIRECT_URL" -c '\dt'
	@echo "--- ClickHouse tables ---"
	@docker compose exec clickhouse clickhouse-client \
		--user $${CLICKHOUSE_USER:-commitpulse} --password $${CLICKHOUSE_PASSWORD:-commitpulse} \
		--database $${CLICKHOUSE_DB:-commitpulse} --query 'SHOW TABLES'
	@echo "--- Chroma heartbeat ---"
	@curl -sf http://localhost:8001/api/v2/heartbeat && echo "" || echo "chroma not responding"

# DESTRUCTIVE: wipes LOCAL volumes only (Kafka/MinIO/ClickHouse/Chroma).
# Does NOT touch Neon — drop/recreate tables there manually if needed.
reset:
	docker compose down -v
	docker compose up -d
