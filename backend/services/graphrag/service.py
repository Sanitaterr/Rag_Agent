from __future__ import annotations

from pathlib import Path
import re
from typing import TYPE_CHECKING

from config.settings import Settings, settings
from services.graphrag.formatting import format_graph_results
from services.graphrag.ingestion import corpus_payload, payload_from_document
from services.graphrag.neo4j_store import Neo4jGraphStore
from services.graphrag.retrieval import GraphRetriever

if TYPE_CHECKING:
    from services.rag.models import KnowledgeDocument


class GraphRAGService:
    """Application-level GraphRAG facade used by the knowledge service."""

    def __init__(self, app_settings: Settings = settings) -> None:
        self._settings = app_settings
        self._store = Neo4jGraphStore(app_settings)
        self._retriever = GraphRetriever(self._store)

    @property
    def error(self) -> str:
        return self._store.error

    def set_error(self, message: str) -> None:
        self._store.error = message

    def close(self) -> None:
        self._store.close()

    def stats(self) -> dict:
        return self._store.stats()

    def visualization(self, limit: int = 120) -> dict:
        """Return graph data for the frontend knowledge graph drawer."""
        return self._store.visualization(limit=limit)

    def initialize_corpus(self, corpus_dir: Path | None = None) -> dict:
        """Initialize Neo4j from the configured industrial GraphRAG corpus."""
        source_dir = corpus_dir or Path(self._settings.graph_rag_corpus_dir)
        payload = corpus_payload(source_dir)
        result = self._store.upsert_payload(payload)
        return {"source_dir": str(source_dir), **result, **self.stats()}

    def index_document(self, document: KnowledgeDocument) -> dict:
        """Index one parsed uploaded DOCX document into Neo4j."""
        payload = payload_from_document(document)
        return self._store.upsert_payload(payload)

    def delete_document(self, doc_id: str) -> None:
        self._store.delete_document(doc_id)

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        return self._retriever.payloads(query, limit=top_k)

    def format_results(self, results: list[dict]) -> str:
        graph_items = [item.get("graph", item) for item in results if item.get("retrieval") == "graph"]
        return format_graph_results(graph_items)

    def image_path(self, doc_id: str, image_id: str) -> Path:
        """Resolve seed-corpus image IDs used by GraphRAG evidence."""
        assets_dir = Path(self._settings.graph_rag_corpus_dir) / "assets" / "images"
        candidates = _corpus_image_candidates(assets_dir, doc_id, image_id)
        for path in candidates:
            if path.exists() and path.is_file():
                return path
        raise FileNotFoundError(image_id)


graph_rag = GraphRAGService()


def _corpus_image_candidates(assets_dir: Path, doc_id: str, image_id: str) -> list[Path]:
    """Build possible corpus asset paths for graph-only image identifiers."""
    doc = _doc_id_from_values(doc_id, image_id)
    names: list[str] = []
    for stem in [image_id, image_id.removeprefix("IMG-")]:
        if stem:
            names.extend(f"{stem}{suffix}" for suffix in [".png", ".jpg", ".jpeg", ".webp"])

    if doc:
        image_id_lower = image_id.lower()
        if any(term in image_id_lower for term in ["flow", "system"]):
            names.append(f"{doc}_system_flow.png")
        names.append(f"{doc}_alarm_logic.png")
        names.append(f"{doc}_system_flow.png")

    seen: set[str] = set()
    return [assets_dir / name for name in names if name and not (name in seen or seen.add(name))]


def _doc_id_from_values(doc_id: str, image_id: str) -> str:
    for value in [doc_id, image_id]:
        match = re.search(r"DOC-[A-Z0-9]+", str(value or ""), flags=re.IGNORECASE)
        if match:
            return match.group(0).upper()
    return ""
