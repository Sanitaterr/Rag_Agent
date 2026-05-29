from __future__ import annotations

import re

from services.rag.models import KnowledgeDocument


def search_keywords(documents: dict[str, KnowledgeDocument], query: str, limit: int) -> list[dict]:
    """Fallback keyword retrieval used when embeddings are unavailable."""
    query_tokens = _tokens(query)
    if not query_tokens:
        return []

    scored = []
    for document in documents.values():
        for chunk in document.chunks:
            score = _keyword_score(query_tokens, chunk.text)
            if score > 0:
                scored.append((score, chunk))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "score": round(score, 3),
            "file_id": chunk.file_id,
            "file_name": chunk.file_name,
            "chunk_id": chunk.id,
            "kind": chunk.kind,
            "index": chunk.index,
            "text": chunk.text,
            "images": chunk.images,
            "retrieval": "keyword_fallback",
        }
        for score, chunk in scored[:limit]
    ]


def _tokens(text: str) -> list[str]:
    normalized = text.lower()
    ascii_words = re.findall(r"[a-z0-9_./-]+", normalized)
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", normalized)
    return ascii_words + cjk_chars


def _keyword_score(query_tokens: list[str], text: str) -> float:
    normalized = text.lower()
    score = 0.0
    for token in query_tokens:
        count = normalized.count(token)
        if count:
            score += 1.0 + min(count, 6) * 0.25
    return score / max(len(query_tokens), 1)
