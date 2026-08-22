from __future__ import annotations

import logging

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

from .config import Settings
from .models import CommitEvent

logger = logging.getLogger(__name__)
MAX_DOCUMENT_CHARS = 8000


class ChromaSink:
    def __init__(self, settings: Settings):
        self.client = chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
        self.collection = self.client.get_or_create_collection(
            name=settings.chroma_collection,
            embedding_function=SentenceTransformerEmbeddingFunction(
                model_name="sentence-transformers/all-MiniLM-L6-v2"
            ),
        )

    def upsert_commit(self, event: CommitEvent) -> None:
        document = self._build_document(event)
        self.collection.upsert(
            ids=[event.sha],
            documents=[document],
            metadatas=[{
                "repo": event.repo,
                "sha": event.sha,
                "author": event.author_username or event.author_email or "unknown",
                "committed_at": event.committed_at.isoformat(),
            }],
        )
        logger.info("upserted commit %s into chroma", event.sha)

    @staticmethod
    def _build_document(event: CommitEvent) -> str:
        parts = [event.message]
        for f in event.files:
            parts.append(f"\n--- {f.path} ({f.change_type}) ---")
            if f.patch:
                parts.append(f.patch)
        return "\n".join(parts)[:MAX_DOCUMENT_CHARS]
