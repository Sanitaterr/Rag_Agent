from __future__ import annotations


def format_graph_results(results: list[dict]) -> str:
    """Format graph evidence as compact tool context."""
    if not results:
        return "GraphRAG: no matching Neo4j graph evidence."
    blocks = []
    for index, item in enumerate(results, start=1):
        blocks.append(
            "\n".join(
                [
                    f"Graph result {index}",
                    f"Retrieval: {item.get('retrieval', 'graph')}",
                    f"Alarm: {item.get('alarm_code') or 'none'} {item.get('alarm_name') or ''}".strip(),
                    f"Severity: {item.get('severity') or 'none'}",
                    f"System: {item.get('system') or 'none'}",
                    f"Device: {item.get('device') or 'none'}",
                    f"Area: {item.get('area') or 'none'}",
                    f"Parameter tag: {item.get('tag') or 'none'}",
                    f"Trigger: {item.get('trigger') or 'none'}",
                    f"Causes: {_joined(item.get('causes'))}",
                    f"Actions: {_joined(item.get('actions'))}",
                    f"Reset condition: {item.get('reset_condition') or 'none'}",
                    f"Rows: {_joined(item.get('row_ids'))}",
                    f"Images: {_joined(item.get('image_ids'))}",
                    f"Document: {item.get('doc_id') or 'none'}",
                    f"Chunk: {item.get('chunk_id') or 'none'}",
                    f"Score: {item.get('score', 0)}",
                ]
            )
        )
    return "\n\n".join(blocks)


def graph_result_to_rag_payload(item: dict) -> dict:
    """Convert graph evidence into the same loose payload shape as RAG results."""
    alarm_code = item.get("alarm_code") or ""
    text_parts = [
        f"Alarm {alarm_code}: {item.get('alarm_name')}" if alarm_code else "",
        f"Severity: {item.get('severity')}" if item.get("severity") else "",
        f"System: {item.get('system')}" if item.get("system") else "",
        f"Device: {item.get('device')}" if item.get("device") else "",
        f"Area: {item.get('area')}" if item.get("area") else "",
        f"Parameter: {item.get('tag')}" if item.get("tag") else "",
        f"Trigger: {item.get('trigger')}" if item.get("trigger") else "",
        f"Causes: {_joined(item.get('causes'))}" if item.get("causes") else "",
        f"Actions: {_joined(item.get('actions'))}" if item.get("actions") else "",
        f"Reset condition: {item.get('reset_condition')}" if item.get("reset_condition") else "",
    ]
    image_ids = [str(image_id) for image_id in item.get("image_ids") or [] if str(image_id)]
    doc_id = str(item.get("doc_id") or "")
    return {
        "retrieval": "graph",
        "score": float(item.get("score") or 0),
        "file_id": doc_id,
        "file_name": doc_id or "neo4j",
        "chunk_id": str(item.get("chunk_id") or alarm_code or "graph"),
        "kind": "graph",
        "chunk_type": "graph",
        "visual_type": "",
        "index": 0,
        "page_start": None,
        "page_end": None,
        "line_start": None,
        "line_end": None,
        "block_index": 0,
        "block_start": 0,
        "block_end": 0,
        "heading_path": [part for part in [item.get("system"), item.get("device"), alarm_code] if part],
        "section_title": alarm_code,
        "heading_level": 0,
        "chunk_strategy": "graphrag",
        "text": "\n".join(part for part in text_parts if part),
        "images": image_ids,
        "image_id": image_ids[0] if image_ids else "",
        "image_path": "",
        "context_before": "",
        "context_after": "",
        "ocr_text": "",
        "description": "",
        "risk_level": str(item.get("severity") or ""),
        "structured_json": "",
        "parse_error": "",
        "full_text": "",
        "parent_table_id": "",
        "parent_row_id": _first(item.get("row_ids")),
        "parent_cell_id": "",
        "row_image_ids": image_ids,
        "row_text": "",
        "table_cell_context": "",
        "parent_inline_id": "",
        "inline_image_ids": [],
        "inline_text": "",
        "related_images": [
            {
                "image_id": image_id,
                "url": f"/api/knowledge/files/{doc_id}/images/{image_id}" if doc_id else "",
                "reason": f"Neo4j graph evidence for {alarm_code}",
            }
            for image_id in image_ids
        ],
        "graph": item,
    }


def _joined(value: object) -> str:
    items = [str(item) for item in value or [] if str(item)]
    return "; ".join(items) if items else "none"


def _first(value: object) -> str:
    items = [str(item) for item in value or [] if str(item)]
    return items[0] if items else ""
