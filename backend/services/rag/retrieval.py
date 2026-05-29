from __future__ import annotations

from dataclasses import dataclass, field

from langchain_core.documents import Document
from langsmith import traceable

from services.rag.query_processing import ProcessedQuery


@dataclass
class RetrievalCandidate:
    """One Chroma candidate with retrieval and rerank metadata."""

    document: Document
    score: float
    sources: set[str] = field(default_factory=set)
    rerank_score: float | None = None

    @property
    def chunk_id(self) -> str:
        return str(self.document.metadata.get("chunk_id", ""))

    def rerank_text(self) -> str:
        """Text sent to the reranker, including structural context."""
        metadata = self.document.metadata
        heading = metadata.get("heading_path", "")
        title = metadata.get("section_title", "")
        chunk_type = metadata.get("chunk_type", metadata.get("kind", ""))
        visual_type = metadata.get("visual_type", "")
        parent_row = metadata.get("parent_row_id", "")
        row_text = metadata.get("row_text", "")
        table_cell_context = metadata.get("table_cell_context", "")
        parent_inline = metadata.get("parent_inline_id", "")
        inline_text = metadata.get("inline_text", "")
        return "\n".join(
            part
            for part in [
                f"\u6807\u9898\u8DEF\u5F84: {heading}" if heading else "",
                f"\u7AE0\u8282\u6807\u9898: {title}" if title else "",
                f"CHUNK_TYPE: {chunk_type}" if chunk_type else "",
                f"VISUAL_TYPE: {visual_type}" if visual_type else "",
                f"PARENT_ROW: {parent_row}" if parent_row else "",
                f"ROW_TEXT: {row_text}" if row_text else "",
                f"TABLE_CELL_CONTEXT: {table_cell_context}" if table_cell_context else "",
                f"PARENT_INLINE: {parent_inline}" if parent_inline else "",
                f"INLINE_TEXT: {inline_text}" if inline_text else "",
                self.document.page_content,
            ]
            if part
        )


def merge_candidate(
    candidates: dict[str, RetrievalCandidate],
    document: Document,
    score: float,
    source: str,
) -> None:
    """Merge duplicate chunk candidates from multiple retrieval channels."""
    chunk_id = str(document.metadata.get("chunk_id", ""))
    if not chunk_id:
        return
    existing = candidates.get(chunk_id)
    if existing is None:
        candidates[chunk_id] = RetrievalCandidate(document=document, score=score, sources={source})
        return
    existing.score = max(existing.score, score)
    existing.sources.add(source)


def keyword_score(query: ProcessedQuery, document: Document) -> float:
    """Score exact term overlap against heading metadata and body text."""
    metadata = document.metadata
    heading = str(metadata.get("heading_path", "")).lower()
    title = str(metadata.get("section_title", "")).lower()
    chunk_type = str(metadata.get("chunk_type", metadata.get("kind", ""))).lower()
    visual_type = str(metadata.get("visual_type", "")).lower()
    row_text = str(metadata.get("row_text", "")).lower()
    table_cell_context = str(metadata.get("table_cell_context", "")).lower()
    inline_text = str(metadata.get("inline_text", "")).lower()
    body = "\n".join([document.page_content, row_text, table_cell_context, inline_text]).lower()
    score = 0.0

    for variant in query.variants:
        term = variant.lower().strip()
        if not term:
            continue
        if term in title:
            score += 4.0
        if term in heading:
            score += 3.0
        if term in body:
            score += 2.0

    intent_text = f"{query.normalized} {' '.join(query.tokens)}"
    if chunk_type == "safety_chunk" or visual_type == "safety_warning":
        if any(term in intent_text for term in ["安全", "危险", "禁止", "防护", "佩戴", "检修", "急停"]):
            score += 3.0
    if chunk_type in {"fault_chunk", "table_chunk", "table_row_chunk"}:
        if any(term in intent_text for term in ["故障", "报警", "代码", "含义", "处理", "复位"]):
            score += 2.4
    if chunk_type == "table_row_chunk" and str(metadata.get("row_image_ids", "")):
        score += 0.5
    if chunk_type == "inline_image_group_chunk" and str(metadata.get("inline_image_ids", "")):
        score += 0.7
    if chunk_type == "image_chunk":
        if any(term in intent_text for term in ["在哪", "位置", "按钮", "点击", "界面", "页面", "切换", "确认"]):
            score += 2.2
    if visual_type == "hmi_screen" and any(term in intent_text for term in ["按钮", "点击", "确认", "手动", "自动", "界面"]):
        score += 1.4
    if visual_type == "equipment_diagram" and any(term in intent_text for term in ["位置", "在哪", "部件", "传感器", "轴承", "电机"]):
        score += 1.4

    for token in query.tokens:
        if not token:
            continue
        if token in title:
            score += 0.8
        if token in heading:
            score += 0.55
        count = body.count(token)
        if count:
            score += 0.25 + min(count, 4) * 0.08

    return score / max(len(query.tokens), 1)


@traceable(name="rag.rule_rerank", run_type="chain")
def rule_rerank(query: ProcessedQuery, candidates: list[RetrievalCandidate]) -> list[RetrievalCandidate]:
    """Deterministic fallback reranking when the external reranker is unavailable."""
    for candidate in candidates:
        structural_score = keyword_score(query, candidate.document)
        # Reward distinct retrieval channels without letting many query rewrites
        # overpower a precise hit from the user's original question.
        source_bonus = 0.0
        if "vector" in candidate.sources:
            source_bonus += 0.06
        if "keyword" in candidate.sources:
            source_bonus += 0.06
        if candidate.sources.intersection({"original", "normalized", "spaced", "compact"}):
            source_bonus += 0.03
        candidate.rerank_score = candidate.score + structural_score + source_bonus
    return sorted(
        candidates,
        key=lambda item: (
            item.rerank_score or 0,
            item.score,
            -int(item.document.metadata.get("index", 0) or 0),
        ),
        reverse=True,
    )
