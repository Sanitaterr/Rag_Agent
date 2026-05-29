from __future__ import annotations

import hashlib
import json
import posixpath
import re
import shutil
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from xml.etree import ElementTree

from config.settings import Settings, settings
from services.rag.models import DocChunk, ImageAsset, KnowledgeDocument
from services.rag.visual_analyzer import VisualAnalysis, VisualAnalyzer, build_image_chunk_text


_XML_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
CHUNK_STRATEGY = "typed_visual_v2"
_EMBEDDING_TEXT_LIMIT = 7600
ParseProgress = Callable[[str, int, int], None]


def parse_docx(
    path: Path,
    extracted_root: Path,
    *,
    chunk_size: int,
    chunk_overlap: int,
    app_settings: Settings = settings,
    analyze_visuals: bool = True,
    progress: ParseProgress | None = None,
) -> KnowledgeDocument:
    """Parse one DOCX into typed text, table, and image chunks."""
    file_id = file_id_for_name(path.name)
    target_dir = extracted_root / file_id
    shutil.rmtree(target_dir, ignore_errors=True)
    image_dir = target_dir / "images"
    image_dir.mkdir(parents=True, exist_ok=True)

    progress and progress("读取 DOCX 结构", 0, 1)
    with zipfile.ZipFile(path) as package:
        images, image_by_rel = _extract_images(package, image_dir)
        style_levels = _style_heading_levels(package)
        blocks = _document_blocks(package, style_levels, image_by_rel, file_id)

    progress and progress(f"解析 DOCX 结构：{len(blocks)} 个块，{len(images)} 张图片", 1, 3)
    enriched_blocks = _attach_heading_context(blocks)
    # Public API still accepts chunk_size/chunk_overlap, but text chunks are
    # grouped by heading sections instead of arbitrary character windows.
    _ = (chunk_size, chunk_overlap)
    analyzer = VisualAnalyzer(app_settings) if analyze_visuals else None
    chunks = _build_chunks(path.name, file_id, enriched_blocks, analyzer, progress)
    progress and progress(f"生成 {len(chunks)} 个知识片段", 3, 3)

    stat = path.stat()
    return KnowledgeDocument(
        id=file_id,
        name=path.name,
        path=path,
        size=stat.st_size,
        modified_at=stat.st_mtime,
        chunks=chunks,
        images=images,
    )


def file_id_for_name(filename: str) -> str:
    """Create the stable document ID used by the UI and storage paths."""
    return hashlib.sha1(filename.encode("utf-8")).hexdigest()[:16]


def clean_text(text: str) -> str:
    """Normalize whitespace in DOCX text nodes."""
    return re.sub(r"\s+", " ", text).strip()


def _extract_images(package: zipfile.ZipFile, image_dir: Path) -> tuple[list[ImageAsset], dict[str, ImageAsset]]:
    relationships = _document_relationships(package)
    images: list[ImageAsset] = []
    image_by_member: dict[str, ImageAsset] = {}
    image_by_rel: dict[str, ImageAsset] = {}

    for rel_id, member in relationships.items():
        if not member.startswith("word/media/") or member not in package.namelist():
            continue
        asset = image_by_member.get(member)
        if asset is None:
            suffix = Path(member).suffix or ".bin"
            image_id = f"image-{len(images) + 1}"
            filename = f"{image_id}{suffix}"
            target = image_dir / filename
            with package.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            asset = ImageAsset(
                id=image_id,
                filename=filename,
                path=target,
                size=target.stat().st_size,
                rel_id=rel_id,
                source_path=member,
            )
            images.append(asset)
            image_by_member[member] = asset
        image_by_rel[rel_id] = asset

    return images, image_by_rel


def _document_relationships(package: zipfile.ZipFile) -> dict[str, str]:
    rels_path = "word/_rels/document.xml.rels"
    if rels_path not in package.namelist():
        return {}
    root = ElementTree.fromstring(package.read(rels_path))
    relationships: dict[str, str] = {}
    for item in root.findall(".//{*}Relationship"):
        rel_id = str(item.attrib.get("Id", ""))
        target = str(item.attrib.get("Target", ""))
        if not rel_id or not target or "://" in target:
            continue
        relationships[rel_id] = posixpath.normpath(posixpath.join("word", target))
    return relationships


def _style_heading_levels(package: zipfile.ZipFile) -> dict[str, int]:
    """Map Word paragraph style IDs to heading levels using styles.xml."""
    if "word/styles.xml" not in package.namelist():
        return {}

    root = ElementTree.fromstring(package.read("word/styles.xml"))
    levels: dict[str, int] = {}
    for style in root.findall(".//w:style", _XML_NS):
        style_id = style.attrib.get(f"{{{_XML_NS['w']}}}styleId")
        if not style_id:
            continue

        outline = style.find("./w:pPr/w:outlineLvl", _XML_NS)
        if outline is not None:
            raw_level = outline.attrib.get(f"{{{_XML_NS['w']}}}val")
            if raw_level is not None and raw_level.isdigit():
                levels[style_id] = int(raw_level) + 1
                continue

        name = style.find("./w:name", _XML_NS)
        style_name = (name.attrib.get(f"{{{_XML_NS['w']}}}val", "") if name is not None else "").lower()
        match = re.search(r"heading\s*(\d+)|标题\s*(\d+)", style_name)
        if match:
            levels[style_id] = int(next(group for group in match.groups() if group))
    return levels


def _document_blocks(
    package: zipfile.ZipFile,
    style_levels: dict[str, int],
    image_by_rel: dict[str, ImageAsset],
    file_id: str,
) -> list[dict]:
    root = ElementTree.fromstring(package.read("word/document.xml"))
    body = root.find(".//w:body", _XML_NS)
    if body is None:
        return []

    blocks: list[dict] = []
    current_page = 1
    current_line = 1
    table_index = 0
    paragraph_index = 0

    for element in body:
        tag = _local_name(element.tag)
        page_start = current_page
        page_end = current_page + _page_break_count(element)

        if tag == "p":
            paragraph_index += 1
            text = _element_text(element)
            rel_ids = _paragraph_image_rel_ids(element)
            inline_images = [image_by_rel[rel_id] for rel_id in rel_ids if rel_id in image_by_rel]
            parent_inline_id = f"{file_id}_inline_{paragraph_index}" if text and inline_images else ""
            inline_image_ids = [image.id for image in inline_images]
            if text:
                heading_level = _paragraph_heading_level(element, style_levels) or _inferred_heading_level(text)
                blocks.append(
                    _block(
                        kind="heading" if heading_level else "paragraph",
                        text=text,
                        page_start=page_start,
                        page_end=page_end,
                        line_start=current_line,
                        line_end=current_line,
                        heading_level=heading_level,
                        block_index=len(blocks) + 1,
                        extra=(
                            {
                                "parent_inline_id": parent_inline_id,
                                "inline_image_ids": inline_image_ids,
                                "inline_text": text,
                            }
                            if parent_inline_id
                            else None
                        ),
                    )
                )
                current_line += 1
            for rel_id in rel_ids:
                image = image_by_rel.get(rel_id)
                if image is None:
                    continue
                blocks.append(
                    _block(
                        kind="image",
                        text="",
                        page_start=page_start,
                        page_end=page_end,
                        line_start=current_line,
                        line_end=current_line,
                        heading_level=0,
                        block_index=len(blocks) + 1,
                        image=image,
                        extra=(
                            {
                                "parent_inline_id": parent_inline_id,
                                "inline_image_ids": inline_image_ids,
                                "inline_text": text,
                                "inline_context": _inline_image_context(text, inline_image_ids),
                            }
                            if parent_inline_id
                            else None
                        ),
                    )
                )
        elif tag == "tbl":
            table_index += 1
            table_text, row_count, table_images, table_rows = _table_content(element, image_by_rel, file_id, table_index)
            if table_text:
                table_block_index = len(blocks) + 1
                blocks.append(
                    _block(
                        kind="table",
                        text=table_text,
                        page_start=page_start,
                        page_end=page_end,
                        line_start=current_line,
                        line_end=current_line + max(row_count - 1, 0),
                        heading_level=0,
                        block_index=table_block_index,
                        extra={
                            "parent_table_id": f"{file_id}_table_{table_index}",
                            "table_columns": table_rows[0].get("table_columns", []) if table_rows else [],
                            "table_rows": table_rows,
                        },
                    )
                )
                for table_row in table_rows:
                    row_line = current_line + max(int(table_row.get("row") or 1) - 1, 0)
                    blocks.append(
                        _block(
                            kind="table_row",
                            text=str(table_row.get("row_text") or ""),
                            page_start=page_start,
                            page_end=page_end,
                            line_start=row_line,
                            line_end=row_line,
                            heading_level=0,
                            block_index=len(blocks) + 1,
                            extra=table_row,
                        )
                    )
            for table_image in table_images:
                image_line = current_line + max(int(table_image.get("row") or 1) - 1, 0)
                blocks.append(
                    _block(
                        kind="image",
                        text="",
                        page_start=page_start,
                        page_end=page_end,
                        line_start=image_line,
                        line_end=image_line,
                        heading_level=0,
                        block_index=len(blocks) + 1,
                        image=table_image["image"],
                        extra={
                            "table_context": _table_image_context(table_image),
                            "table_row": table_image["row"],
                            "table_col": table_image["col"],
                            "table_header": table_image["header"],
                            "table_cell_text": table_image["cell_text"],
                            "table_row_text": table_image["row_text"],
                            "parent_table_id": table_image["parent_table_id"],
                            "parent_row_id": table_image["parent_row_id"],
                            "parent_cell_id": table_image["parent_cell_id"],
                            "row_image_ids": table_image["row_image_ids"],
                            "row_text": table_image["row_text"],
                            "table_cell_context": table_image["table_cell_context"],
                            "row_markdown": table_image["row_markdown"],
                            "parent_table_markdown": table_image["parent_table_markdown"],
                        },
                    )
                )
            if table_text:
                current_line += max(row_count, 1)

        current_page = page_end
    return blocks


def _block(
    *,
    kind: str,
    text: str,
    page_start: int,
    page_end: int,
    line_start: int,
    line_end: int,
    heading_level: int,
    block_index: int,
    image: ImageAsset | None = None,
    extra: dict | None = None,
) -> dict:
    data = {
        "kind": kind,
        "text": text,
        "heading_level": heading_level,
        "page_start": page_start,
        "page_end": page_end,
        "line_start": line_start,
        "line_end": line_end,
        "block_start": block_index,
        "block_end": block_index,
        "image": image,
    }
    if extra:
        data.update(extra)
    return data


def _attach_heading_context(blocks: list[dict]) -> list[dict]:
    heading_stack: list[str] = []
    enriched: list[dict] = []
    for block in blocks:
        heading_level = int(block.get("heading_level") or 0)
        if heading_level:
            heading_stack = heading_stack[: heading_level - 1]
            heading_stack.append(block["text"])
        block["heading_path"] = list(heading_stack)
        block["section_title"] = heading_stack[-1] if heading_stack else ""
        enriched.append(block)
    return enriched


def _build_chunks(
    file_name: str,
    file_id: str,
    blocks: list[dict],
    analyzer: VisualAnalyzer | None,
    progress: ParseProgress | None,
) -> list[DocChunk]:
    chunks: list[DocChunk] = []
    chunks.extend(_text_chunks(file_name, file_id, blocks, len(chunks)))
    chunks.extend(_inline_group_chunks(file_name, file_id, blocks, len(chunks)))
    chunks.extend(_table_chunks(file_name, file_id, blocks, len(chunks)))
    chunks.extend(_table_row_chunks(file_name, file_id, blocks, len(chunks)))
    chunks.extend(_image_chunks(file_name, file_id, blocks, analyzer, len(chunks), progress))
    _enrich_table_chunks_with_image_semantics(chunks)
    chunks.sort(key=lambda chunk: (chunk.block_start, chunk.index))
    for index, chunk in enumerate(chunks, start=1):
        chunk.index = index
    return chunks


def _text_chunks(
    file_name: str,
    file_id: str,
    blocks: list[dict],
    offset: int,
) -> list[DocChunk]:
    sections = _heading_sections([block for block in blocks if block["kind"] in {"heading", "paragraph"}])
    chunks: list[DocChunk] = []
    for section_index, section in enumerate(sections, start=1):
        full_text = section["text"].strip()
        chunk_type = _classify_text_chunk(full_text, section["section_title"])
        chunks.append(
            DocChunk(
                id=f"{file_id}-{chunk_type}-{offset + len(chunks) + 1}",
                file_id=file_id,
                file_name=file_name,
                index=offset + len(chunks) + 1,
                kind=chunk_type,
                chunk_type=chunk_type,
                text=_truncate_embedding_text(full_text),
                full_text=full_text,
                page_start=section["page_start"],
                page_end=section["page_end"],
                line_start=section["line_start"],
                line_end=section["line_end"],
                block_index=section_index,
                block_start=section["block_start"],
                block_end=section["block_end"],
                heading_path=section["heading_path"],
                section_title=section["section_title"],
                heading_level=section["heading_level"],
                chunk_strategy=CHUNK_STRATEGY,
            )
        )
    return chunks


def _inline_group_chunks(file_name: str, file_id: str, blocks: list[dict], offset: int) -> list[DocChunk]:
    chunks: list[DocChunk] = []
    seen_inline_ids: set[str] = set()
    for block in blocks:
        parent_inline_id = str(block.get("parent_inline_id") or "")
        if not parent_inline_id or parent_inline_id in seen_inline_ids:
            continue
        seen_inline_ids.add(parent_inline_id)
        inline_image_ids = [str(item) for item in block.get("inline_image_ids", []) if str(item)]
        inline_text = str(block.get("inline_text") or block.get("text") or "")
        chunks.append(
            DocChunk(
                id=f"{file_id}-inline_image_group_chunk-{offset + len(chunks) + 1}",
                file_id=file_id,
                file_name=file_name,
                index=offset + len(chunks) + 1,
                kind="inline_image_group_chunk",
                chunk_type="inline_image_group_chunk",
                text=_inline_group_chunk_text(file_name, block, inline_text, inline_image_ids),
                page_start=block["page_start"],
                page_end=block["page_end"],
                line_start=block["line_start"],
                line_end=block["line_end"],
                block_index=block["block_start"],
                block_start=block["block_start"],
                block_end=block["block_end"],
                heading_path=block["heading_path"],
                section_title=block["section_title"],
                heading_level=block["heading_level"],
                chunk_strategy=CHUNK_STRATEGY,
                images=inline_image_ids,
                parent_inline_id=parent_inline_id,
                inline_image_ids=inline_image_ids,
                inline_text=inline_text,
                structured_json=json.dumps(
                    {
                        "parent_inline_id": parent_inline_id,
                        "inline_image_ids": inline_image_ids,
                        "inline_text": inline_text,
                    },
                    ensure_ascii=False,
                ),
            )
        )
    return chunks


def _table_chunks(file_name: str, file_id: str, blocks: list[dict], offset: int) -> list[DocChunk]:
    chunks: list[DocChunk] = []
    for block in blocks:
        if block["kind"] != "table":
            continue
        table_context = _table_context_payload(file_id, file_name, block, {})
        chunks.append(
            DocChunk(
                id=f"{file_id}-table_chunk-{offset + len(chunks) + 1}",
                file_id=file_id,
                file_name=file_name,
                index=offset + len(chunks) + 1,
                kind="table_chunk",
                chunk_type="table_chunk",
                text=_table_embedding_text(table_context),
                page_start=block["page_start"],
                page_end=block["page_end"],
                line_start=block["line_start"],
                line_end=block["line_end"],
                block_index=block["block_start"],
                block_start=block["block_start"],
                block_end=block["block_end"],
                heading_path=block["heading_path"],
                section_title=block["section_title"],
                chunk_strategy=CHUNK_STRATEGY,
                parent_table_id=str(block.get("parent_table_id") or ""),
                structured_json=json.dumps(
                    {
                        "table_context": table_context,
                        "canonical_markdown": table_context["canonical_markdown"],
                        "columns": table_context["columns"],
                        "rows": table_context["rows"],
                        "parent_table_id": table_context["table_id"],
                    },
                    ensure_ascii=False,
                ),
            )
        )
    return chunks


def _table_row_chunks(file_name: str, file_id: str, blocks: list[dict], offset: int) -> list[DocChunk]:
    chunks: list[DocChunk] = []
    for block in blocks:
        if block["kind"] != "table_row":
            continue
        text = _table_row_chunk_text(file_name, block)
        row_image_ids = [str(item) for item in block.get("row_image_ids", []) if str(item)]
        chunks.append(
            DocChunk(
                id=f"{file_id}-table_row_chunk-{offset + len(chunks) + 1}",
                file_id=file_id,
                file_name=file_name,
                index=offset + len(chunks) + 1,
                kind="table_row_chunk",
                chunk_type="table_row_chunk",
                text=text,
                page_start=block["page_start"],
                page_end=block["page_end"],
                line_start=block["line_start"],
                line_end=block["line_end"],
                block_index=block["block_start"],
                block_start=block["block_start"],
                block_end=block["block_end"],
                heading_path=block["heading_path"],
                section_title=block["section_title"],
                chunk_strategy=CHUNK_STRATEGY,
                images=row_image_ids,
                parent_table_id=str(block.get("parent_table_id") or ""),
                parent_row_id=str(block.get("parent_row_id") or ""),
                row_image_ids=row_image_ids,
                row_text=str(block.get("row_text") or ""),
                structured_json=json.dumps(
                    {
                        "parent_table_id": block.get("parent_table_id") or "",
                        "parent_row_id": block.get("parent_row_id") or "",
                        "row_image_ids": row_image_ids,
                        "row_text": block.get("row_text") or "",
                        "row_markdown": block.get("row_markdown") or "",
                        "parent_table_markdown": _truncate_structured_text(str(block.get("parent_table_markdown") or "")),
                    },
                    ensure_ascii=False,
                ),
            )
        )
    return chunks


def _image_chunks(
    file_name: str,
    file_id: str,
    blocks: list[dict],
    analyzer: VisualAnalyzer | None,
    offset: int,
    progress: ParseProgress | None,
) -> list[DocChunk]:
    image_blocks = [block for block in blocks if block["kind"] == "image" and block.get("image") is not None]
    if not image_blocks:
        progress and progress("未发现需要处理的图片", 0, 0)
        return []

    if analyzer is None or analyzer.workers == 1 or len(image_blocks) == 1:
        chunks = []
        for image_index, block in enumerate(image_blocks, start=1):
            progress and progress(f"处理图片 {image_index}/{len(image_blocks)}：{block['image'].filename}", image_index - 1, len(image_blocks))
            chunks.append(_image_chunk(file_name, file_id, blocks, block, analyzer, offset + len(chunks) + 1))
            progress and progress(f"完成图片 {image_index}/{len(image_blocks)}：{block['image'].filename}", image_index, len(image_blocks))
        return chunks

    chunks_by_position: dict[int, DocChunk] = {}
    completed = 0
    worker_count = min(analyzer.workers, len(image_blocks))
    progress and progress(f"并行处理图片：0/{len(image_blocks)}，workers={worker_count}", 0, len(image_blocks))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(_image_chunk, file_name, file_id, blocks, block, analyzer, offset + image_index): (image_index, block)
            for image_index, block in enumerate(image_blocks, start=1)
        }
        for future in as_completed(futures):
            image_index, block = futures[future]
            chunks_by_position[image_index] = future.result()
            completed += 1
            progress and progress(
                f"完成图片 {completed}/{len(image_blocks)}：{block['image'].filename}",
                completed,
                len(image_blocks),
            )
    return [chunks_by_position[index] for index in sorted(chunks_by_position)]


def _image_chunk(
    file_name: str,
    file_id: str,
    blocks: list[dict],
    block: dict,
    analyzer: VisualAnalyzer | None,
    chunk_index: int,
) -> DocChunk:
    image: ImageAsset = block["image"]
    table_context = str(block.get("table_context") or "")
    table_cell_context = str(block.get("table_cell_context") or table_context)
    inline_context = str(block.get("inline_context") or "")
    context_before = "\n".join(
        part
        for part in [
            table_context,
            table_cell_context,
            inline_context,
            _nearby_text(blocks, block["block_start"], direction=-1),
        ]
        if part
    )
    context_after = _nearby_text(blocks, block["block_start"], direction=1)
    if analyzer is None:
        analysis = VisualAnalysis(
            visual_type="generic_image",
            description="预览模式未执行 OCR 和图像描述；点击向量化后会自动解析。",
        )
    elif image.size < analyzer.min_image_bytes:
        analysis = VisualAnalysis(
            visual_type="generic_image",
            description="图片文件较小，已按配置跳过 OCR 和视觉描述。",
            structured={"skipped": "small_image", "size": image.size},
        )
    else:
        analysis = analyzer.analyze(
            image.path,
            document_name=file_name,
            section=block["section_title"],
            context_before=context_before,
            context_after=context_after,
            table_cell_context=table_cell_context,
        )
    image_path = str(image.path)
    text = build_image_chunk_text(
        document_name=file_name,
        page=block["page_start"],
        section=block["section_title"],
        image_path=image_path,
        analysis=analysis,
        context_before=context_before,
        context_after=context_after,
        table_cell_context=table_cell_context,
    )
    structured = dict(analysis.structured or {})
    row_image_ids = [str(item) for item in block.get("row_image_ids", []) if str(item)]
    inline_image_ids = [str(item) for item in block.get("inline_image_ids", []) if str(item)]
    if table_cell_context:
        structured.update(
            {
                "is_table_cell_image": True,
                "parent_chunk_type": "table_chunk",
                "parent_table_id": block.get("parent_table_id"),
                "parent_row_id": block.get("parent_row_id"),
                "parent_cell_id": block.get("parent_cell_id"),
                "table_row": block.get("table_row"),
                "table_col": block.get("table_col"),
                "table_header": block.get("table_header"),
                "table_cell_text": block.get("table_cell_text"),
                "table_row_text": block.get("table_row_text"),
                "table_cell_context": table_cell_context,
                "row_image_ids": row_image_ids,
                "row_text": block.get("row_text") or block.get("table_row_text"),
                "row_markdown": block.get("row_markdown"),
                "parent_table_markdown": _truncate_structured_text(str(block.get("parent_table_markdown") or "")),
            }
        )
    if block.get("parent_inline_id"):
        structured.update(
            {
                "is_inline_image": True,
                "parent_inline_id": block.get("parent_inline_id"),
                "inline_image_ids": inline_image_ids,
                "inline_text": block.get("inline_text") or "",
                "inline_context": inline_context,
            }
        )
    return DocChunk(
        id=f"{file_id}-image_chunk-{chunk_index}",
        file_id=file_id,
        file_name=file_name,
        index=chunk_index,
        kind="image_chunk",
        chunk_type="image_chunk",
        text=text,
        page_start=block["page_start"],
        page_end=block["page_end"],
        line_start=block["line_start"],
        line_end=block["line_end"],
        block_index=block["block_start"],
        block_start=block["block_start"],
        block_end=block["block_end"],
        heading_path=block["heading_path"],
        section_title=block["section_title"],
        chunk_strategy=CHUNK_STRATEGY,
        images=[image.id],
        visual_type=analysis.visual_type,
        image_id=image.id,
        image_path=image_path,
        context_before=context_before,
        context_after=context_after,
        ocr_text=analysis.ocr_text,
        description=analysis.description,
        risk_level=analysis.risk_level,
        structured_json=json.dumps(structured, ensure_ascii=False),
        parse_error=analysis.error,
        parent_table_id=str(block.get("parent_table_id") or ""),
        parent_row_id=str(block.get("parent_row_id") or ""),
        parent_cell_id=str(block.get("parent_cell_id") or ""),
        row_image_ids=row_image_ids,
        row_text=str(block.get("row_text") or block.get("table_row_text") or ""),
        table_cell_context=table_cell_context,
        parent_inline_id=str(block.get("parent_inline_id") or ""),
        inline_image_ids=inline_image_ids,
        inline_text=str(block.get("inline_text") or ""),
    )


def _enrich_table_chunks_with_image_semantics(chunks: list[DocChunk]) -> None:
    """Copy analyzed table-image semantics back into the full table chunk."""
    image_semantics_by_table: dict[str, dict[str, dict]] = {}
    for chunk in chunks:
        if chunk.chunk_type != "image_chunk" or not chunk.parent_table_id or not chunk.image_id:
            continue
        image_semantics_by_table.setdefault(chunk.parent_table_id, {})[chunk.image_id] = {
            "image_id": chunk.image_id,
            "url": _image_url(chunk.file_id, chunk.image_id),
            "alt": _image_alt_from_chunk(chunk),
            "ocr": chunk.ocr_text,
            "description": chunk.description,
            "caption": _image_caption_from_chunk(chunk),
            "row_name": _row_name_from_text(chunk.row_text),
            "column_name": _column_name_from_cell_context(chunk.table_cell_context),
            "context": chunk.table_cell_context or chunk.context_before,
        }

    for chunk in chunks:
        if chunk.chunk_type != "table_chunk" or not chunk.parent_table_id:
            continue
        try:
            structured = json.loads(chunk.structured_json)
        except json.JSONDecodeError:
            structured = {}
        table_context = structured.get("table_context")
        if not isinstance(table_context, dict):
            continue

        enriched = _table_context_with_image_semantics(
            table_context,
            image_semantics_by_table.get(chunk.parent_table_id, {}),
        )
        structured.update(
            {
                "table_context": enriched,
                "canonical_markdown": enriched["canonical_markdown"],
                "columns": enriched["columns"],
                "rows": enriched["rows"],
            }
        )
        chunk.structured_json = json.dumps(structured, ensure_ascii=False)
        chunk.text = _table_embedding_text(enriched)
        chunk.images = _table_context_image_ids(enriched)


def _table_context_with_image_semantics(table_context: dict, image_semantics: dict[str, dict]) -> dict:
    """Replace table image placeholders with OCR/description-aware image objects."""
    enriched = dict(table_context)
    rows: list[dict] = []
    for row in table_context.get("rows") or []:
        row_copy = dict(row)
        images_by_column: dict[str, list[dict]] = {}
        for column, images in (row.get("images_by_column") or {}).items():
            column_images = []
            for image in images:
                image_id = str(image.get("image_id") or "")
                semantic = image_semantics.get(image_id)
                column_images.append({**image, **semantic} if semantic else image)
            if column_images:
                images_by_column[str(column)] = column_images
        row_copy["images_by_column"] = images_by_column
        rows.append(row_copy)
    enriched["rows"] = rows
    enriched["canonical_markdown"] = _table_context_markdown(enriched)
    return enriched


def _table_context_payload(file_id: str, file_name: str, block: dict, image_semantics: dict[str, dict]) -> dict:
    """Build the full structured table context stored in one table chunk."""
    columns = [str(item) for item in block.get("table_columns", [])]
    rows: list[dict] = []
    for row in block.get("table_rows", []):
        cells = {str(key): str(value or "") for key, value in (row.get("cells") or {}).items()}
        images_by_column: dict[str, list[dict]] = {}
        for column, image_ids in (row.get("images_by_column_ids") or {}).items():
            images = []
            for image_id in image_ids:
                semantic = image_semantics.get(str(image_id), {})
                images.append(
                    {
                        "image_id": str(image_id),
                        "url": _image_url(file_id, str(image_id)),
                        "alt": semantic.get("alt") or _image_alt(cells, str(column), str(image_id)),
                        "ocr": semantic.get("ocr", ""),
                        "description": semantic.get("description", ""),
                        "caption": semantic.get("caption") or _image_caption(cells, str(column)),
                        "row_name": semantic.get("row_name") or _row_name(cells),
                        "column_name": semantic.get("column_name") or str(column),
                        "context": semantic.get("context") or _table_row_context(block, row),
                    }
                )
            if images:
                images_by_column[str(column)] = images
        rows.append(
            {
                "row_id": str(row.get("parent_row_id") or ""),
                "order": int(row.get("row") or row.get("row_position") or len(rows) + 1),
                "cells": cells,
                "images_by_column": images_by_column,
                "source_location": {
                    "page_start": block.get("page_start"),
                    "page_end": block.get("page_end"),
                    "line_start": block.get("line_start"),
                    "line_end": block.get("line_end"),
                    "block_start": block.get("block_start"),
                    "block_end": block.get("block_end"),
                },
            }
        )

    context = {
        "table_id": str(block.get("parent_table_id") or ""),
        "file_id": file_id,
        "file_name": file_name,
        "heading_path": list(block.get("heading_path") or []),
        "columns": columns,
        "rows": rows,
    }
    context["canonical_markdown"] = _table_context_markdown(context)
    return context


def _table_embedding_text(table_context: dict) -> str:
    """Build table embedding text from semantics, not image URLs."""
    heading = " > ".join(table_context.get("heading_path") or []) or "未知"
    lines = [
        f"文档：{table_context.get('file_name')}",
        f"章节：{heading}",
        f"类型：table_chunk",
        f"表格列：{'，'.join(table_context.get('columns') or [])}",
    ]
    for row in table_context.get("rows") or []:
        cells = row.get("cells") or {}
        lines.append("本行字段：" + " | ".join(f"{key}: {value}" for key, value in cells.items()))
        for column, images in (row.get("images_by_column") or {}).items():
            for image in images:
                parts = [
                    f"{column}列包含图片：{image.get('alt') or image.get('caption') or image.get('image_id')}",
                    f"OCR：{image.get('ocr')}" if image.get("ocr") else "",
                    f"图片说明：{image.get('description') or image.get('caption')}" if image.get("description") or image.get("caption") else "",
                    f"所在行名称：{image.get('row_name')}" if image.get("row_name") else "",
                    f"上下文：{image.get('context')}" if image.get("context") else "",
                ]
                lines.append("。".join(part for part in parts if part) + "。")
    return _truncate_embedding_text("\n".join(line for line in lines if line))


def _table_context_markdown(table_context: dict) -> str:
    """Render the authoritative markdown table from structured table data."""
    columns = [str(column) for column in table_context.get("columns") or []]
    if not columns:
        return ""
    lines = [
        f"| {' | '.join(_markdown_cell(column) for column in columns)} |",
        f"| {' | '.join('---' for _ in columns)} |",
    ]
    for row in table_context.get("rows") or []:
        cells = row.get("cells") or {}
        images_by_column = row.get("images_by_column") or {}
        values = []
        for column in columns:
            parts = []
            cell_text = str(cells.get(column) or "")
            if cell_text and not _cell_text_is_image_marker(cell_text):
                parts.append(_strip_image_refs(cell_text))
            for image in images_by_column.get(column) or []:
                alt = _markdown_alt(str(image.get("alt") or image.get("caption") or image.get("image_id") or "image"))
                url = str(image.get("url") or "")
                if url:
                    parts.append(f"![{alt}]({url})")
            values.append("<br>".join(part for part in parts if part))
        lines.append(f"| {' | '.join(_markdown_cell(value) for value in values)} |")
    return "\n".join(lines)


def _table_context_image_ids(table_context: dict) -> list[str]:
    """Return de-duplicated image IDs in table order."""
    image_ids: list[str] = []
    for row in table_context.get("rows") or []:
        for images in (row.get("images_by_column") or {}).values():
            for image in images:
                image_id = str(image.get("image_id") or "")
                if image_id and image_id not in image_ids:
                    image_ids.append(image_id)
    return image_ids


def _image_url(file_id: str, image_id: str) -> str:
    return f"/api/knowledge/files/{file_id}/images/{image_id}"


def _image_alt(cells: dict[str, str], column: str, image_id: str) -> str:
    name = _row_name(cells)
    return " ".join(part for part in [name, column, "图片"] if part) or image_id


def _image_caption(cells: dict[str, str], column: str) -> str:
    name = _row_name(cells)
    description = _row_description(cells)
    return "，".join(part for part in [name, column, description] if part)


def _image_alt_from_chunk(chunk: DocChunk) -> str:
    return chunk.description or _image_alt(_row_fields(chunk.row_text), _column_name_from_cell_context(chunk.table_cell_context), chunk.image_id)


def _image_caption_from_chunk(chunk: DocChunk) -> str:
    return chunk.description or _image_caption(_row_fields(chunk.row_text), _column_name_from_cell_context(chunk.table_cell_context))


def _row_name(cells: dict[str, str]) -> str:
    for key in ["名称", "按钮名称", "Name", "name"]:
        if cells.get(key):
            return cells[key]
    for key, value in cells.items():
        if value and not _cell_text_is_image_marker(value):
            return value
    return ""


def _row_name_from_text(row_text: str) -> str:
    return _row_name(_row_fields(row_text))


def _row_description(cells: dict[str, str]) -> str:
    for key in ["描述说明", "功能说明", "描述", "Description", "description"]:
        if cells.get(key):
            return _strip_image_refs(cells[key])
    return ""


def _row_fields(row_text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in row_text.replace("\n", " | ").split(" | "):
        if ":" in part:
            key, value = part.split(":", 1)
        elif "：" in part:
            key, value = part.split("：", 1)
        else:
            continue
        key = key.strip()
        value = value.strip()
        if key and value:
            fields[key] = value
    return fields


def _column_name_from_cell_context(context: str) -> str:
    match = re.search(r"列标题[:：]\s*([^\n]+)", context or "")
    return match.group(1).strip() if match else ""


def _table_row_context(block: dict, row: dict) -> str:
    heading = " > ".join(block.get("heading_path") or [])
    return "；".join(
        part
        for part in [
            f"章节：{heading}" if heading else "",
            f"表格：{block.get('parent_table_id')}" if block.get("parent_table_id") else "",
            f"行：{row.get('row_text')}" if row.get("row_text") else "",
        ]
        if part
    )


def _cell_text_is_image_marker(value: str) -> bool:
    return bool(re.fullmatch(r"(?:图片|image)\s*[:：]\s*[A-Za-z0-9_, -]+|空", str(value or "").strip(), flags=re.IGNORECASE))


def _strip_image_refs(value: str) -> str:
    return re.sub(r"(?:图片|image)\s*[:：]\s*[A-Za-z0-9_, -]+", "", str(value or "")).strip(" ；;，,")


def _markdown_cell(value: object) -> str:
    return str(value or "").replace("|", "\\|").replace("\r", " ").replace("\n", "<br>").strip()


def _markdown_alt(value: str) -> str:
    clean = _strip_image_refs(value).replace("\r", " ").replace("\n", " ").strip()
    return (clean or "image").replace("[", "(").replace("]", ")")


def _heading_sections(blocks: list[dict]) -> list[dict]:
    sections: list[dict] = []
    current: dict | None = None
    for block in blocks:
        heading_level = int(block.get("heading_level") or 0)
        if heading_level:
            if current is not None and _section_has_body(current):
                sections.append(_finish_section(current))
            current = _new_section(block)
            continue

        if current is None:
            current = _new_section(block)
        current["parts"].append(block["text"])
        current["body_count"] += 1
        current["page_end"] = block["page_end"]
        current["line_end"] = block["line_end"]
        current["block_end"] = block["block_end"]

    if current is not None and _section_has_body(current):
        sections.append(_finish_section(current))
    return sections


def _new_section(block: dict) -> dict:
    heading_level = int(block.get("heading_level") or 0)
    return {
        "parts": [block["text"]],
        "body_count": 0 if heading_level else 1,
        "heading_path": list(block.get("heading_path") or []),
        "section_title": block.get("section_title") or "",
        "heading_level": heading_level,
        "page_start": block["page_start"],
        "page_end": block["page_end"],
        "line_start": block["line_start"],
        "line_end": block["line_end"],
        "block_start": block["block_start"],
        "block_end": block["block_end"],
    }


def _section_has_body(section: dict) -> bool:
    return bool(section["body_count"] or not section["heading_path"]) and any(part.strip() for part in section["parts"])


def _finish_section(section: dict) -> dict:
    heading_context = " > ".join(section["heading_path"])
    body = "\n".join(part for part in section["parts"] if part.strip())
    section["text"] = f"标题路径：{heading_context}\n{body}" if heading_context else body
    return section


def _classify_text_chunk(text: str, section_title: str) -> str:
    searchable = f"{section_title}\n{text}"
    if any(term in searchable for term in ["危险", "警告", "注意", "禁止", "必须", "佩戴", "防护", "急停"]):
        return "safety_chunk"
    if re.search(r"\b[EFA]\d{2,5}\b|故障|报警|复位|处理方法|原因分析", searchable, flags=re.IGNORECASE):
        return "fault_chunk"
    if re.search(r"(^|\n)\s*\d+[\.、]|步骤|操作|启动|停止|切换|确认", searchable):
        return "procedure_chunk"
    return "text_chunk"


def _table_chunk_text(file_name: str, block: dict) -> str:
    heading = " > ".join(block.get("heading_path") or []) or "未知"
    page = block.get("page_start")
    return "\n".join(
        [
            f"文档：{file_name}",
            f"页码：第 {page} 页" if page else "页码：未知",
            f"章节：{heading}",
            "类型：table_chunk",
            "",
            block["text"],
        ]
    )


def _inline_group_chunk_text(file_name: str, block: dict, inline_text: str, inline_image_ids: list[str]) -> str:
    heading = " > ".join(block.get("heading_path") or []) or "未知"
    page = block.get("page_start")
    return "\n".join(
        [
            f"文档：{file_name}",
            f"页码：第 {page} 页" if page else "页码：未知",
            f"章节：{heading}",
            "类型：inline_image_group_chunk",
            f"内嵌图片组：{block.get('parent_inline_id') or '未知'}",
            "",
            "列表项/段落文字：",
            inline_text or "无",
            "",
            f"关联图片：{', '.join(inline_image_ids) if inline_image_ids else 'none'}",
        ]
    )


def _inline_image_context(inline_text: str, inline_image_ids: list[str]) -> str:
    return "\n".join(
        [
            "段落/列表项内嵌图片：",
            f"文字：{inline_text or '无'}",
            f"关联图片：{', '.join(inline_image_ids) if inline_image_ids else 'none'}",
        ]
    )


def _table_row_chunk_text(file_name: str, block: dict) -> str:
    heading = " > ".join(block.get("heading_path") or []) or "未知"
    page = block.get("page_start")
    row_image_ids = [str(item) for item in block.get("row_image_ids", []) if str(item)]
    parts = [
        f"文档：{file_name}",
        f"页码：第 {page} 页" if page else "页码：未知",
        f"章节：{heading}",
        "类型：table_row_chunk",
        f"父表格：{block.get('parent_table_id') or '未知'}",
        f"表格行：{block.get('parent_row_id') or '未知'}",
        "",
        "本行字段：",
        str(block.get("row_text") or "无"),
    ]
    if row_image_ids:
        parts.extend(["", f"本行关联图片：{', '.join(row_image_ids)}"])
    if block.get("row_markdown"):
        parts.extend(["", "本行 Markdown：", str(block["row_markdown"])])
    return "\n".join(parts)


def _nearby_text(blocks: list[dict], block_index: int, *, direction: int, limit: int = 2) -> str:
    candidates = [block for block in blocks if block["kind"] in {"paragraph", "heading", "table", "table_row"}]
    if direction < 0:
        selected = [block for block in candidates if block["block_start"] < block_index][-limit:]
    else:
        selected = [block for block in candidates if block["block_start"] > block_index][:limit]
    return "\n".join(block["text"] for block in selected if block.get("text"))


def _paragraph_heading_level(element: ElementTree.Element, style_levels: dict[str, int]) -> int:
    p_style = element.find("./w:pPr/w:pStyle", _XML_NS)
    if p_style is not None:
        style_id = p_style.attrib.get(f"{{{_XML_NS['w']}}}val")
        if style_id in style_levels:
            return style_levels[style_id]

    outline = element.find("./w:pPr/w:outlineLvl", _XML_NS)
    if outline is not None:
        raw_level = outline.attrib.get(f"{{{_XML_NS['w']}}}val")
        if raw_level is not None and raw_level.isdigit():
            return int(raw_level) + 1
    return 0


def _inferred_heading_level(text: str) -> int:
    """Infer heading levels from numbered manual titles when Word styles are absent."""
    normalized = clean_text(text)
    if not normalized or len(normalized) > 90:
        return 0

    if re.match(r"^第[一二三四五六七八九十百千\d]+章\b", normalized):
        return 1
    if re.match(r"^第[一二三四五六七八九十百千\d]+节\b", normalized):
        return 2

    if re.match(r"^\d+\s+\S", normalized) and not re.search(r"[。；;：:，,]$", normalized):
        return 1

    match = re.match(r"^(\d+(?:[.．]\d+){1,5})(?:\s+|[、.．])?\S", normalized)
    if not match:
        return 0

    # Avoid treating long sentences or procedure text as headings.
    if re.search(r"[。；;：:，,]$", normalized):
        return 0
    return min(match.group(1).replace("．", ".").count(".") + 1, 6)


def _element_text(element: ElementTree.Element) -> str:
    texts = [node.text or "" for node in element.findall(".//w:t", _XML_NS)]
    return clean_text("".join(texts))


def _paragraph_image_rel_ids(element: ElementTree.Element) -> list[str]:
    rel_ids: list[str] = []
    for blip in element.findall(".//a:blip", _XML_NS):
        rel_id = blip.attrib.get(f"{{{_XML_NS['r']}}}embed") or blip.attrib.get(f"{{{_XML_NS['r']}}}link")
        if rel_id:
            rel_ids.append(rel_id)
    return rel_ids


def _table_content(
    table: ElementTree.Element,
    image_by_rel: dict[str, ImageAsset],
    file_id: str = "",
    table_index: int = 1,
) -> tuple[str, int, list[dict], list[dict]]:
    table_id = f"{file_id}_table_{table_index}" if file_id else f"table_{table_index}"
    raw_rows: list[dict] = []
    for row_index, row in enumerate(table.findall(".//w:tr", _XML_NS), start=1):
        row_cells = row.findall("./w:tc", _XML_NS)
        cells = [clean_text(_element_text(cell)) for cell in row_cells]
        cell_images: dict[int, list[ImageAsset]] = {}
        for col_index, cell in enumerate(row_cells, start=1):
            for rel_id in _paragraph_image_rel_ids(cell):
                image = image_by_rel.get(rel_id)
                if image is None:
                    continue
                cell_images.setdefault(col_index, []).append(image)
        if any(cells) or cell_images:
            raw_rows.append({"row": row_index, "cells": cells, "cell_images": cell_images})
    if not raw_rows:
        return "", 0, [], []

    column_count = max(len(row["cells"]) for row in raw_rows)
    normalized = [row["cells"] + [""] * (column_count - len(row["cells"])) for row in raw_rows]
    header = normalized[0]
    separator = ["---"] * column_count
    display_rows = [
        _display_row_cells(cells, raw_row["cell_images"], column_count)
        for cells, raw_row in zip(normalized, raw_rows, strict=False)
    ]
    markdown_rows = [f"| {' | '.join(display_rows[0])} |", f"| {' | '.join(separator)} |"]
    markdown_rows.extend(f"| {' | '.join(row)} |" for row in display_rows[1:])
    parent_table_markdown = "\n".join(markdown_rows)

    table_rows: list[dict] = []
    table_images: list[dict] = []
    for row_position, (raw_row, cells, display_cells) in enumerate(zip(raw_rows, normalized, display_rows, strict=False), start=1):
        row_number = int(raw_row["row"])
        parent_row_id = f"{table_id}_row_{row_number}"
        row_image_ids = [
            image.id
            for images in raw_row["cell_images"].values()
            for image in images
        ]
        cells_by_column = {
            (_header_for_col(header, col_index) or f"列{col_index}"): cells[col_index - 1]
            for col_index in range(1, column_count + 1)
        }
        images_by_column_ids = {
            (_header_for_col(header, col_index) or f"列{col_index}"): [image.id for image in images]
            for col_index, images in raw_row["cell_images"].items()
        }
        row_text = _row_field_text(header, cells, raw_row["cell_images"])
        text_only_row_text = " | ".join(cell for cell in cells if cell)
        row_markdown = f"| {' | '.join(display_rows[0])} |\n| {' | '.join(display_cells)} |"
        table_rows.append(
            {
                "parent_table_id": table_id,
                "parent_row_id": parent_row_id,
                "row": row_number,
                "row_position": row_position,
                "table_columns": header,
                "cells": cells_by_column,
                "images_by_column_ids": images_by_column_ids,
                "row_text": row_text,
                "text_only_row_text": text_only_row_text,
                "row_image_ids": row_image_ids,
                "row_markdown": row_markdown,
                "parent_table_markdown": parent_table_markdown,
            }
        )
        for col_index, images in raw_row["cell_images"].items():
            header_text = _header_for_col(header, col_index)
            cell_text = cells[col_index - 1] if col_index <= len(cells) else ""
            parent_cell_id = f"{parent_row_id}_col_{col_index}"
            for image in images:
                table_image = {
                    "image": image,
                    "row": row_number,
                    "col": col_index,
                    "header": header_text,
                    "cell_text": cell_text,
                    "row_text": row_text,
                    "text_only_row_text": text_only_row_text,
                    "parent_table_id": table_id,
                    "parent_row_id": parent_row_id,
                    "parent_cell_id": parent_cell_id,
                    "row_image_ids": row_image_ids,
                    "row_markdown": row_markdown,
                    "parent_table_markdown": parent_table_markdown,
                }
                table_image["table_cell_context"] = _table_cell_context(table_image)
                table_images.append(table_image)
    return parent_table_markdown, len(raw_rows), table_images, table_rows


def _header_for_col(header: list[str], col_index: int) -> str:
    """Return the first-row header for a table image cell when available."""
    if col_index < 1 or col_index > len(header):
        return ""
    return header[col_index - 1]


def _display_row_cells(cells: list[str], cell_images: dict[int, list[ImageAsset]], column_count: int) -> list[str]:
    display: list[str] = []
    for col_index in range(1, column_count + 1):
        cell_text = cells[col_index - 1] if col_index <= len(cells) else ""
        images = cell_images.get(col_index, [])
        image_text = ",".join(image.id for image in images)
        if cell_text and image_text:
            display.append(f"{cell_text} (图片:{image_text})")
        elif image_text:
            display.append(f"图片:{image_text}")
        else:
            display.append(cell_text)
    return display


def _row_field_text(header: list[str], cells: list[str], cell_images: dict[int, list[ImageAsset]]) -> str:
    fields: list[str] = []
    column_count = max(len(header), len(cells), max(cell_images.keys(), default=0))
    for col_index in range(1, column_count + 1):
        label = _header_for_col(header, col_index) or f"列{col_index}"
        cell_text = cells[col_index - 1] if col_index <= len(cells) else ""
        image_ids = [image.id for image in cell_images.get(col_index, [])]
        value_parts = [cell_text] if cell_text else []
        if image_ids:
            value_parts.append(f"图片:{','.join(image_ids)}")
        value = "；".join(value_parts) if value_parts else "空"
        fields.append(f"{label}: {value}")
    return " | ".join(fields)


def _table_cell_context(table_image: dict) -> str:
    parts = [
        "表格单元格上下文：",
        f"父表格：{table_image.get('parent_table_id')}",
        f"父行：{table_image.get('parent_row_id')}",
        f"单元格：{table_image.get('parent_cell_id')}",
        f"行：{table_image.get('row')}",
        f"列：{table_image.get('col')}",
    ]
    if table_image.get("header"):
        parts.append(f"列标题：{table_image['header']}")
    if table_image.get("cell_text"):
        parts.append(f"单元格文字：{table_image['cell_text']}")
    if table_image.get("row_text"):
        parts.append(f"本行字段：{table_image['row_text']}")
    if table_image.get("row_image_ids"):
        parts.append(f"本行关联图片：{', '.join(table_image['row_image_ids'])}")
    return "\n".join(parts)


def _table_image_context(table_image: dict) -> str:
    """Describe the table cell that contains an embedded image."""
    parts = [
        "表格内图片：",
        f"行：{table_image.get('row')}",
        f"列：{table_image.get('col')}",
    ]
    if table_image.get("header"):
        parts.append(f"列标题：{table_image['header']}")
    if table_image.get("cell_text"):
        parts.append(f"单元格文字：{table_image['cell_text']}")
    if table_image.get("text_only_row_text"):
        parts.append(f"同行文字：{table_image['text_only_row_text']}")
    if table_image.get("row_text"):
        parts.append(f"本行字段：{table_image['row_text']}")
    return "\n".join(parts)


def _table_markdown(table: ElementTree.Element) -> tuple[str, int]:
    """Backward-compatible helper for tests and callers that only need text."""
    text, row_count, _, _ = _table_content(table, {})
    return text, row_count


def _truncate_structured_text(text: str, limit: int = 6000) -> str:
    """Keep display metadata useful without embedding or storing huge tables."""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...（已截断）"


def _truncate_embedding_text(text: str, limit: int = _EMBEDDING_TEXT_LIMIT) -> str:
    """Keep provider embedding input under the strict 8192 token/char limit."""
    text = str(text or "").strip()
    if len(text) <= limit:
        return text or "empty table"
    return f"{text[:limit]}\n...（embedding 文本已截断，完整表格见 metadata.table_context）"


def _page_break_count(element: ElementTree.Element) -> int:
    count = 0
    for node in element.iter():
        tag = _local_name(node.tag)
        if tag == "lastRenderedPageBreak":
            count += 1
        elif tag == "br" and node.attrib.get(f"{{{_XML_NS['w']}}}type") == "page":
            count += 1
    return count


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
