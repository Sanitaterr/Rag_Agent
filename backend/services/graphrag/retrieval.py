from __future__ import annotations

import re

from services.graphrag.formatting import graph_result_to_rag_payload
from services.graphrag.neo4j_store import Neo4jGraphStore


ALARM_CODE_RE = re.compile(r"\b[A-Z]{1,8}\d{2,5}-A\d{3}\b")
TAG_RE = re.compile(r"\b[A-Z]{1,8}\d{2,5}_[A-Z0-9_]{2,}\b")


class GraphRetriever:
    """GraphRAG read path that normalizes user queries for Neo4j lookup."""

    def __init__(self, store: Neo4jGraphStore) -> None:
        self._store = store

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """Return graph evidence with exact identifiers queried first."""
        variants = _query_variants(query)
        results: list[dict] = []
        seen: set[str] = set()
        for variant in variants:
            for item in self._store.search(variant, limit=limit):
                key = str(item.get("alarm_code") or item.get("chunk_id") or item)
                if key in seen:
                    continue
                seen.add(key)
                if variant != query and (variant in str(item.get("alarm_code")) or variant in str(item.get("tag"))):
                    item["score"] = max(float(item.get("score") or 0), 0.95)
                results.append(item)
                if len(results) >= limit:
                    return results
        return results

    def payloads(self, query: str, limit: int = 5) -> list[dict]:
        return [graph_result_to_rag_payload(item) for item in self.search(query, limit=limit)]


def _query_variants(query: str) -> list[str]:
    variants = [query.strip()]
    variants.extend(ALARM_CODE_RE.findall(query))
    variants.extend(TAG_RE.findall(query))
    seen: set[str] = set()
    return [item for item in variants if item and not (item in seen or seen.add(item))]
