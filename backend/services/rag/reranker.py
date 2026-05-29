from __future__ import annotations

import httpx
from langsmith import traceable

from config.settings import Settings
from services.rag.retrieval import RetrievalCandidate, rule_rerank


class QwenReranker:
    """DashScope reranker adapter with deterministic fallback reranking."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.error = ""

    @traceable(name="rag.qwen_rerank", run_type="chain")
    def rerank(self, query: str, candidates: list[RetrievalCandidate], top_n: int) -> list[RetrievalCandidate]:
        """Rerank candidates with qwen rerank when configured, otherwise fallback."""
        if not candidates:
            return []
        if not self._settings.rerank_enabled or not self._settings.rerank_api_key:
            return rule_rerank_candidate_text(query, candidates)[:top_n]

        try:
            reranked = self._remote_rerank(query, candidates, top_n)
            self.error = ""
            return reranked
        except Exception as exc:
            self.error = f"Reranker unavailable: {exc}"
            return rule_rerank_candidate_text(query, candidates)[:top_n]

    def _remote_rerank(
        self,
        query: str,
        candidates: list[RetrievalCandidate],
        top_n: int,
    ) -> list[RetrievalCandidate]:
        payload = {
            "model": self._settings.rerank_model,
            "input": {
                "query": query,
                "documents": [candidate.rerank_text() for candidate in candidates],
            },
            "parameters": {
                "top_n": min(top_n, len(candidates)),
                "return_documents": False,
            },
        }
        headers = {
            "Authorization": f"Bearer {self._settings.rerank_api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self._settings.rerank_timeout_seconds) as client:
            response = client.post(self._settings.rerank_url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        results = data.get("output", {}).get("results", data.get("results", []))
        ordered: list[RetrievalCandidate] = []
        used_indexes: set[int] = set()
        for result in results:
            index = result.get("index")
            if index is None or not 0 <= int(index) < len(candidates):
                continue
            candidate = candidates[int(index)]
            candidate.rerank_score = float(result.get("relevance_score", result.get("score", candidate.score)))
            ordered.append(candidate)
            used_indexes.add(int(index))

        if len(ordered) < top_n:
            remaining = [candidate for index, candidate in enumerate(candidates) if index not in used_indexes]
            ordered.extend(rule_rerank_candidate_text(query, remaining))
        return ordered[:top_n]


def rule_rerank_candidate_text(query: str, candidates: list[RetrievalCandidate]) -> list[RetrievalCandidate]:
    """Avoid a Settings dependency in the local fallback path."""
    from services.rag.query_processing import preprocess_query

    return rule_rerank(preprocess_query(query), candidates)
