from __future__ import annotations

import json
import re


def format_search_results(results: list[dict]) -> str:
    """Format retrieval results as compact source snippets for the agent tool."""
    if not results:
        return (
            "No matching Chroma document chunks were found. "
            "Only files that have been vectorized into Chroma are available to RAG."
        )

    lines: list[str] = []
    for index, item in enumerate(results, start=1):
        table_lines = _table_context_lines(item)
        lines.append(
            "\n".join(
                [
                    f"Result {index}",
                    f"Retrieval: {item.get('retrieval', 'chroma')}",
                    f"Source: {item['file_name']}",
                    f"Heading: {_heading_text(item)}",
                    f"Location: {_location_text(item)}",
                    f"Chunk: {item['chunk_id']} ({item.get('chunk_type') or item['kind']} #{item['index']})",
                    f"Visual type: {item.get('visual_type') or 'none'}",
                    f"Risk level: {item.get('risk_level') or 'none'}",
                    f"Parent row: {item.get('parent_row_id') or 'none'}",
                    f"Row text: {item.get('row_text') or 'none'}",
                    f"Table cell context: {item.get('table_cell_context') or 'none'}",
                    f"Parent inline group: {item.get('parent_inline_id') or 'none'}",
                    f"Inline text: {item.get('inline_text') or 'none'}",
                    f"Score: {item['score']}",
                    f"Text: {item['text']}",
                    f"Images: {', '.join(item['images']) if item['images'] else 'none'}",
                    f"Image path: {item.get('image_path') or 'none'}",
                    f"Image URL: {_image_url(item)}",
                    f"Markdown image: {_markdown_image(item)}",
                    "Related Markdown images:",
                    _related_images_text(item),
                    *table_lines,
                ]
            )
        )
    return "\n\n".join(lines)


def _table_context_lines(item: dict) -> list[str]:
    """Return complete table context lines when a table chunk is the evidence source."""
    table_context = item.get("table_context") or {}
    if not isinstance(table_context, dict):
        return []
    canonical_markdown = str(item.get("canonical_markdown") or table_context.get("canonical_markdown") or "")
    if not canonical_markdown:
        return []
    compact_context = {
        "table_id": table_context.get("table_id"),
        "file_id": table_context.get("file_id"),
        "file_name": table_context.get("file_name"),
        "heading_path": table_context.get("heading_path"),
        "columns": table_context.get("columns"),
        "matched_row_ids": item.get("matched_row_ids") or [],
        "rows": table_context.get("rows"),
    }
    return [
        "Complete table context JSON:",
        json.dumps(compact_context, ensure_ascii=False),
        "Canonical Markdown table:",
        canonical_markdown,
    ]


def _location_text(item: dict) -> str:
    page_text = _range_text(item.get("page_start"), item.get("page_end"), "page")
    line_text = _range_text(item.get("line_start"), item.get("line_end"), "line")
    block_start = item.get("block_start") or item.get("block_index")
    block_end = item.get("block_end") or block_start
    block_text = _range_text(block_start, block_end, "block") if block_start else "block unknown"
    return f"{page_text}, {line_text}, {block_text}"


def _heading_text(item: dict) -> str:
    heading_path = item.get("heading_path") or []
    if isinstance(heading_path, list) and heading_path:
        return " > ".join(str(part) for part in heading_path)
    section_title = item.get("section_title")
    return str(section_title) if section_title else "unknown"


def _image_url(item: dict) -> str:
    chunk_type = str(item.get("chunk_type") or item.get("kind") or "")
    if chunk_type == "table_chunk" and not item.get("parent_row_id"):
        return "none"
    file_id = str(item.get("file_id") or "")
    image_id = str(item.get("image_id") or "")
    images = item.get("images") or []
    if not image_id and isinstance(images, list) and images:
        image_id = str(images[0])
    if not file_id or not image_id:
        return "none"
    return f"/api/knowledge/files/{file_id}/images/{image_id}"


def _markdown_image(item: dict) -> str:
    url = _image_url(item)
    if url == "none":
        return "none"
    alt = " ".join(
        part
        for part in [
            str(item.get("file_name") or "source image"),
            str(item.get("section_title") or ""),
            str(item.get("visual_type") or ""),
        ]
        if part
    )
    return f"![{alt}]({url})"


def _related_images_text(item: dict) -> str:
    related = _related_images(item)
    if not related:
        return "none"
    return "\n".join(
        f"- image_id: {image['image_id']}\n  markdown: {image['markdown']}\n  reason: {image['reason']}"
        for image in related
    )


def _related_images(item: dict) -> list[dict]:
    chunk_type = str(item.get("chunk_type") or item.get("kind") or "")
    if chunk_type == "table_chunk" and not item.get("parent_row_id"):
        return []

    image_ids = []
    if item.get("image_id"):
        image_ids.append(str(item["image_id"]))
    scoped_image_ids = [
        *(item.get("row_image_ids") or []),
        *(item.get("inline_image_ids") or []),
        *(item.get("images") or []),
    ]
    if _allow_text_image_ids(item):
        scoped_image_ids.extend(_image_ids_from_text(str(item.get("text") or "")))
    for image_id in scoped_image_ids:
        image_id = str(image_id)
        if image_id and image_id not in image_ids:
            image_ids.append(image_id)

    related: list[dict] = []
    for image_id in image_ids:
        image_item = {**item, "image_id": image_id, "images": [image_id]}
        reason_parts = [
            "该图片属于命中表格行",
            f"列/单元格：{item.get('parent_cell_id')}" if item.get("parent_cell_id") else "",
            f"行：{item.get('row_text')}" if item.get("row_text") else "",
            f"段落/列表项：{item.get('inline_text')}" if item.get("inline_text") else "",
        ]
        related.append(
            {
                "image_id": image_id,
                "url": _image_url(image_item),
                "markdown": _markdown_image(image_item),
                "reason": "；".join(part for part in reason_parts if part),
            }
        )
    return related


def _allow_text_image_ids(item: dict) -> bool:
    """Only parse image IDs from text for scoped evidence, not whole-table chunks."""
    chunk_type = str(item.get("chunk_type") or item.get("kind") or "")
    if chunk_type == "table_chunk" and not item.get("parent_row_id"):
        return False
    return bool(item.get("parent_row_id") or item.get("parent_inline_id") or item.get("image_id"))


def _image_ids_from_text(text: str) -> list[str]:
    """Find image IDs embedded in table markdown such as 图片:image-78."""
    seen: set[str] = set()
    image_ids: list[str] = []
    for match in re.finditer(r"(?:图片|image)\s*[:：]\s*([A-Za-z0-9_-]+)", text, flags=re.IGNORECASE):
        image_id = match.group(1)
        if image_id in seen:
            continue
        seen.add(image_id)
        image_ids.append(image_id)
    return image_ids


def _range_text(start: object, end: object, label: str) -> str:
    if start is None:
        return f"{label} unknown"
    if end is None or end == start:
        return f"{label} {start}"
    return f"{label}s {start}-{end}"
