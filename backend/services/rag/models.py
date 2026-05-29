from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ImageAsset:
    """Image extracted from a DOCX package."""

    id: str
    filename: str
    path: Path
    size: int
    rel_id: str = ""
    source_path: str = ""


@dataclass
class DocChunk:
    """Searchable unit from one DOCX file."""

    id: str
    file_id: str
    file_name: str
    index: int
    kind: str
    text: str
    page_start: int | None = None
    page_end: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    block_index: int = 0
    block_start: int = 0
    block_end: int = 0
    heading_path: list[str] = field(default_factory=list)
    section_title: str = ""
    heading_level: int = 0
    chunk_strategy: str = "heading_v1"
    images: list[str] = field(default_factory=list)
    chunk_type: str = ""
    visual_type: str = ""
    image_id: str = ""
    image_path: str = ""
    context_before: str = ""
    context_after: str = ""
    ocr_text: str = ""
    description: str = ""
    risk_level: str = ""
    structured_json: str = ""
    parse_error: str = ""
    full_text: str = ""
    parent_table_id: str = ""
    parent_row_id: str = ""
    parent_cell_id: str = ""
    row_image_ids: list[str] = field(default_factory=list)
    row_text: str = ""
    table_cell_context: str = ""
    parent_inline_id: str = ""
    inline_image_ids: list[str] = field(default_factory=list)
    inline_text: str = ""

    def __post_init__(self) -> None:
        """Keep legacy kind and the newer typed chunk field in sync."""
        if not self.chunk_type:
            self.chunk_type = _chunk_type_for_kind(self.kind)
        if not self.kind:
            self.kind = self.chunk_type


@dataclass
class KnowledgeDocument:
    """Parsed DOCX document metadata and searchable chunks."""

    id: str
    name: str
    path: Path
    size: int
    modified_at: float
    chunks: list[DocChunk]
    images: list[ImageAsset]


def _chunk_type_for_kind(kind: str) -> str:
    """Map older parser kinds to the typed chunk taxonomy."""
    mapping = {
        "paragraph": "text_chunk",
        "section": "text_chunk",
        "heading": "text_chunk",
        "table": "table_chunk",
        "image": "image_chunk",
    }
    if kind.endswith("_chunk"):
        return kind
    return mapping.get(kind, "text_chunk")
