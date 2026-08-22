# App image for commit-pulse services (webhook, processor, backfill).
# Host distro has no pip/venv, so runtime + tests run in this container.
FROM python:3.12-slim

WORKDIR /app

# CPU-only torch first, so sentence-transformers doesn't pull the CUDA build (~2GB)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Source is bind-mounted at runtime (`-v $PWD:/app`); no COPY of code/.env here.
CMD ["python", "--version"]
