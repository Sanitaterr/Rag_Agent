from __future__ import annotations

import json

from langchain_core.documents import Document

from agent.runtime.agent_graph import ChatAgentRuntime
from config.settings import Settings
from services.rag.docx_parser import CHUNK_STRATEGY
from services.rag.formatting import format_search_results
from services.rag.query_processing import ProcessedQuery
from services.rag.vector_store import ChromaKnowledgeStore, _result_payload


CANONICAL_MARKDOWN = (
    "| Icon | Name | Description |\n"
    "| --- | --- | --- |\n"
    "| ![Width button icon](/api/knowledge/files/file-1/images/image-1) | Width | Set line width |\n"
    "|  | Endpoint size | Set arrow size |"
)


def test_table_related_hit_attaches_complete_table_context(monkeypatch) -> None:
    store = ChromaKnowledgeStore(Settings(embedding_api_key="test-key"))
    table_doc = _document(
        chunk_id="table-1",
        chunk_type="table_chunk",
        text="Table semantic text. Name: Width. Description: Set line width.",
        parent_row_id="",
        images=["image-1"],
        row_image_ids=[],
        row_text="",
        structured_json=_table_context_json(),
    )
    row_doc = _document(
        chunk_id="row-1",
        chunk_type="table_row_chunk",
        text="Name: Width | Description: Set line width | Icon: 图片:image-1",
        parent_row_id="doc_table_1_row_2",
        images=["image-1"],
        row_image_ids=["image-1"],
        row_text="Name: Width | Description: Set line width | Icon: 图片:image-1",
    )
    monkeypatch.setattr(store, "_documents_for_files", lambda file_ids: [table_doc, row_doc])

    expanded = store._attach_table_context_evidence(
        [
            {
                "chunk_id": "row-1",
                "chunk_type": "table_row_chunk",
                "parent_table_id": "doc_table_1",
                "parent_row_id": "doc_table_1_row_2",
                "row_image_ids": ["image-1"],
            }
        ],
        {"file-1"},
    )

    assert [item["chunk_id"] for item in expanded] == ["row-1", "table-1"]
    assert expanded[1]["retrieval"] == "table_context"
    assert expanded[1]["table_context"]["table_id"] == "doc_table_1"
    assert expanded[1]["canonical_markdown"] == CANONICAL_MARKDOWN


def test_direct_table_chunk_gets_matched_row_ids(monkeypatch) -> None:
    store = ChromaKnowledgeStore(Settings(embedding_api_key="test-key"))
    query = ProcessedQuery(
        original="line width",
        normalized="line width",
        variants=["line width"],
        tokens=["line", "width"],
    )
    table_doc = _document(
        chunk_id="table-1",
        chunk_type="table_chunk",
        text="Table semantic text. Name: Width. Description: Set line width.",
        parent_row_id="",
        images=["image-1"],
        row_image_ids=[],
        row_text="",
        structured_json=_table_context_json(),
    )
    payload = _result_payload(table_doc, 0.9)
    monkeypatch.setattr(store, "_documents_for_files", lambda file_ids: [table_doc])

    expanded = store._attach_table_context_evidence([payload], {"file-1"}, query)

    assert [item["chunk_id"] for item in expanded] == ["table-1"]
    assert expanded[0]["matched_row_ids"] == ["doc_table_1_row_2"]
    assert expanded[0]["is_table_listing"] is False
    assert expanded[0]["canonical_markdown"] == CANONICAL_MARKDOWN


def test_table_listing_marks_all_rows_from_complete_table(monkeypatch) -> None:
    store = ChromaKnowledgeStore(Settings(embedding_api_key="test-key"))
    query = ProcessedQuery(
        original="图形工具栏有哪些按钮",
        normalized="图形工具栏有哪些按钮",
        variants=["图形工具栏有哪些按钮"],
        tokens=["图形工具栏", "哪些", "按钮"],
    )
    table_doc = _document(
        chunk_id="table-1",
        chunk_type="table_chunk",
        text="完整表格语义文本",
        parent_row_id="",
        images=["image-1"],
        row_image_ids=[],
        row_text="",
        structured_json=_table_context_json(),
    )
    payload = _result_payload(table_doc, 0.9)
    monkeypatch.setattr(store, "_documents_for_files", lambda file_ids: [table_doc])

    expanded = store._attach_table_context_evidence([payload], {"file-1"}, query)

    assert expanded[0]["matched_row_ids"] == ["doc_table_1_row_2", "doc_table_1_row_3"]
    assert expanded[0]["is_table_listing"] is True
    assert expanded[0]["canonical_markdown"] == CANONICAL_MARKDOWN


def test_format_search_results_exposes_complete_table_context() -> None:
    payload = _result_payload(
        _document(
            chunk_id="table-1",
            chunk_type="table_chunk",
            text="Table semantic text. Name: Width. Description: Set line width.",
            parent_row_id="",
            images=["image-1"],
            row_image_ids=[],
            row_text="",
            structured_json=_table_context_json(),
        ),
        0.9,
    )
    payload["matched_row_ids"] = ["doc_table_1_row_2"]

    text = format_search_results([payload])

    assert "Complete table context JSON:" in text
    assert '"matched_row_ids": ["doc_table_1_row_2"]' in text
    assert "Canonical Markdown table:" in text
    assert CANONICAL_MARKDOWN in text


def test_final_answer_appends_only_canonical_table_markdown() -> None:
    answer = ChatAgentRuntime._apply_rag_display_context(
        "模型说明。\n\n| 序号 | 按钮名称 |\n| --- | --- |\n| 1 | Width |",
        [
            {
                "chunk_type": "table_chunk",
                "parent_table_id": "doc_table_1",
                "table_context": json.loads(_table_context_json())["table_context"],
                "canonical_markdown": CANONICAL_MARKDOWN,
            }
        ],
    )

    assert "模型说明。" in answer
    assert "| 序号 | 按钮名称 |" not in answer
    assert CANONICAL_MARKDOWN in answer
    assert "图片:image-" not in answer


def test_final_answer_places_canonical_table_before_sources() -> None:
    answer = ChatAgentRuntime._apply_rag_display_context(
        "\u8fd9\u662f\u5de5\u5177\u680f\u8bf4\u660e\u3002\n\n\u6765\u6e90: manual.docx, page 1",
        [
            {
                "chunk_type": "table_chunk",
                "parent_table_id": "doc_table_1",
                "table_context": json.loads(_table_context_json())["table_context"],
                "canonical_markdown": CANONICAL_MARKDOWN,
            }
        ],
    )

    assert answer.index(CANONICAL_MARKDOWN) < answer.index("\u6765\u6e90:")
    assert "Toolbar" not in answer


def test_final_answer_still_appends_images_when_table_context_exists() -> None:
    answer = ChatAgentRuntime._apply_rag_display_context(
        "\u8fd9\u662f\u5de5\u5177\u680f\u8bf4\u660e\u3002\n\n\u6765\u6e90: manual.docx, page 1",
        [
            {
                "chunk_type": "table_chunk",
                "parent_table_id": "doc_table_1",
                "table_context": json.loads(_table_context_json())["table_context"],
                "canonical_markdown": CANONICAL_MARKDOWN,
            },
            {
                "chunk_type": "image_chunk",
                "file_name": "manual.docx",
                "section_title": "Toolbar example",
                "related_images": [
                    {
                        "image_id": "image-2",
                        "url": "/api/knowledge/files/file-1/images/image-2",
                    }
                ],
            },
        ],
    )

    assert CANONICAL_MARKDOWN in answer
    assert "/api/knowledge/files/file-1/images/image-2" in answer
    assert answer.index("/api/knowledge/files/file-1/images/image-2") < answer.index("\u6765\u6e90:")


def test_table_row_hits_do_not_rebuild_markdown_tables() -> None:
    answer = ChatAgentRuntime._apply_rag_display_context(
        "线宽度按钮用于设置绘图线宽。",
        [
            {
                "retrieval": "row_expansion",
                "chunk_type": "table_row_chunk",
                "parent_table_id": "doc_table_1",
                "parent_row_id": "doc_table_1_row_2",
                "row_text": "Icon: 图片:image-1 | Name: Width | Description: Set line width",
                "related_images": [
                    {
                        "image_id": "image-1",
                        "url": "/api/knowledge/files/file-1/images/image-1",
                    }
                ],
            }
        ],
    )

    assert answer == "线宽度按钮用于设置绘图线宽。"
    assert "| 图标 | 按钮名称 | 功能说明 |" not in answer
    assert "/api/knowledge/files/file-1/images/image-1" not in answer


def test_result_payload_exposes_table_context_and_canonical_markdown() -> None:
    payload = _result_payload(
        _document(
            chunk_id="table-1",
            chunk_type="table_chunk",
            text="Table semantic text. Name: Width. Description: Set line width.",
            parent_row_id="",
            images=["image-1"],
            row_image_ids=[],
            row_text="",
            structured_json=_table_context_json(),
        ),
        0.8,
    )

    assert payload["table_context"]["columns"] == ["Icon", "Name", "Description"]
    assert payload["canonical_markdown"] == CANONICAL_MARKDOWN
    assert "图片:image-" not in payload["canonical_markdown"]


def test_format_search_results_does_not_dump_whole_table_images() -> None:
    text = format_search_results(
        [
            {
                "retrieval": "chroma",
                "file_id": "file-1",
                "file_name": "manual.docx",
                "heading_path": ["Toolbar"],
                "page_start": 1,
                "page_end": 1,
                "line_start": 3,
                "line_end": 9,
                "block_start": 5,
                "block_end": 5,
                "chunk_id": "table-1",
                "chunk_type": "table_chunk",
                "kind": "table_chunk",
                "index": 2,
                "visual_type": "",
                "risk_level": "",
                "parent_row_id": "",
                "parent_cell_id": "",
                "row_text": "",
                "table_cell_context": "",
                "parent_inline_id": "",
                "inline_text": "",
                "score": 0.9,
                "text": "Table semantic text. Name: Width. Description: Set line width.",
                "images": ["image-1"],
                "image_id": "",
                "image_path": "",
                "row_image_ids": [],
                "inline_image_ids": [],
            }
        ]
    )

    assert "Related Markdown images:\nnone" in text
    assert "/api/knowledge/files/file-1/images/image-1" not in text


def _document(
    *,
    chunk_id: str,
    chunk_type: str,
    text: str,
    parent_row_id: str,
    images: list[str],
    row_image_ids: list[str],
    row_text: str,
    image_id: str = "",
    table_cell_context: str = "",
    parent_inline_id: str = "",
    inline_image_ids: list[str] | None = None,
    inline_text: str = "",
    structured_json: str = "",
) -> Document:
    return Document(
        page_content=text,
        metadata={
            "file_id": "file-1",
            "file_name": "manual.docx",
            "chunk_id": chunk_id,
            "kind": chunk_type,
            "chunk_type": chunk_type,
            "index": 1,
            "block_index": 1,
            "block_start": 1,
            "block_end": 1,
            "heading_path": json.dumps(["Toolbar"], ensure_ascii=False),
            "section_title": "Toolbar",
            "chunk_strategy": CHUNK_STRATEGY,
            "images": json.dumps(images, ensure_ascii=False),
            "image_id": image_id,
            "parent_table_id": "doc_table_1",
            "parent_row_id": parent_row_id,
            "parent_cell_id": f"{parent_row_id}_col_3" if image_id else "",
            "row_image_ids": json.dumps(row_image_ids, ensure_ascii=False),
            "row_text": row_text,
            "table_cell_context": table_cell_context,
            "parent_inline_id": parent_inline_id,
            "inline_image_ids": json.dumps(inline_image_ids or [], ensure_ascii=False),
            "inline_text": inline_text,
            "structured_json": structured_json,
            "retrieval": "chroma",
        },
    )


def _table_context_json() -> str:
    return json.dumps(
        {
            "table_context": {
                "table_id": "doc_table_1",
                "file_id": "file-1",
                "file_name": "manual.docx",
                "heading_path": ["Toolbar"],
                "columns": ["Icon", "Name", "Description"],
                "rows": [
                    {
                        "row_id": "doc_table_1_row_2",
                        "order": 2,
                        "cells": {"Icon": "图片:image-1", "Name": "Width", "Description": "Set line width"},
                        "images_by_column": {
                            "Icon": [
                                {
                                    "image_id": "image-1",
                                    "url": "/api/knowledge/files/file-1/images/image-1",
                                    "alt": "Width button icon",
                                    "ocr": "Width",
                                    "description": "Line width toolbar button",
                                    "caption": "Width button",
                                    "row_name": "Width",
                                    "column_name": "Icon",
                                    "context": "Name: Width | Description: Set line width",
                                }
                            ]
                        },
                    },
                    {
                        "row_id": "doc_table_1_row_3",
                        "order": 3,
                        "cells": {"Icon": "", "Name": "Endpoint size", "Description": "Set arrow size"},
                        "images_by_column": {},
                    },
                ],
                "canonical_markdown": CANONICAL_MARKDOWN,
            }
        },
        ensure_ascii=False,
    )
