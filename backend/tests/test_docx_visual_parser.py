from __future__ import annotations

import json
import zipfile

from config.settings import Settings
from services.rag.docx_parser import parse_docx


PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000100ffff03000006000557bfab0d0000000049454e44ae426082"
)


def test_docx_image_chunk_keeps_position_context_and_path(tmp_path) -> None:
    docx_path = tmp_path / "manual.docx"
    _write_docx(
        docx_path,
        body="""
        <w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:r><w:t>3.2 启动前检查</w:t></w:r></w:p>
        <w:p><w:r><w:t>启动设备前，应检查控制柜电源、急停按钮和压力表状态。</w:t></w:r></w:p>
        <w:p><w:r><w:drawing><a:blip r:embed="rId1"/></w:drawing></w:r></w:p>
        <w:p><w:r><w:t>确认无报警后，点击启动按钮。</w:t></w:r></w:p>
        """,
        rels='<Relationship Id="rId1" Type="image" Target="media/panel.png"/>',
        media={"word/media/panel.png": PNG_BYTES},
    )

    document = parse_docx(
        docx_path,
        tmp_path / "extracted",
        chunk_size=800,
        chunk_overlap=0,
        app_settings=Settings(ocr_enabled=False, vision_enabled=False),
    )

    image_chunks = [chunk for chunk in document.chunks if chunk.chunk_type == "image_chunk"]
    assert len(image_chunks) == 1
    chunk = image_chunks[0]
    assert chunk.section_title == "3.2 启动前检查"
    assert "启动设备前" in chunk.context_before
    assert "点击启动按钮" in chunk.context_after
    assert chunk.image_id == "image-1"
    assert chunk.image_path.endswith("image-1.png")
    assert chunk.images == ["image-1"]
    assert "图片路径" in chunk.text


def test_docx_table_and_rule_typed_chunks(tmp_path) -> None:
    docx_path = tmp_path / "faults.docx"
    _write_docx(
        docx_path,
        body="""
        <w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:r><w:t>故障处理</w:t></w:r></w:p>
        <w:p><w:r><w:t>检修前必须断电并佩戴防护用品。</w:t></w:r></w:p>
        <w:tbl>
          <w:tr><w:tc><w:p><w:r><w:t>故障代码</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>含义</w:t></w:r></w:p></w:tc></w:tr>
          <w:tr><w:tc><w:p><w:r><w:t>E203</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>压力过高</w:t></w:r></w:p></w:tc></w:tr>
        </w:tbl>
        """,
    )

    document = parse_docx(
        docx_path,
        tmp_path / "extracted",
        chunk_size=800,
        chunk_overlap=0,
        app_settings=Settings(ocr_enabled=False, vision_enabled=False),
    )

    chunk_types = {chunk.chunk_type for chunk in document.chunks}
    assert "safety_chunk" in chunk_types
    assert "table_chunk" in chunk_types
    table = next(chunk for chunk in document.chunks if chunk.chunk_type == "table_chunk")
    structured = json.loads(table.structured_json)
    assert structured["canonical_markdown"].startswith("| 故障代码 | 含义 |")
    assert "表格列：故障代码，含义" in table.text
    assert "E203" in table.text


def test_docx_text_chunks_follow_heading_sections_without_split(tmp_path) -> None:
    long_body = " ".join(f"第{index}条内容保持在同一标题下" for index in range(1, 180))
    docx_path = tmp_path / "heading-sections.docx"
    _write_docx(
        docx_path,
        body=f"""
        <w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:r><w:t>第一章 操作说明</w:t></w:r></w:p>
        <w:p><w:r><w:t>{long_body}</w:t></w:r></w:p>
        <w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:r><w:t>第二章 参数说明</w:t></w:r></w:p>
        <w:p><w:r><w:t>参数 A 表示控制周期。</w:t></w:r></w:p>
        """,
    )

    document = parse_docx(
        docx_path,
        tmp_path / "extracted",
        chunk_size=200,
        chunk_overlap=0,
        app_settings=Settings(ocr_enabled=False, vision_enabled=False),
    )

    text_chunks = [chunk for chunk in document.chunks if chunk.chunk_type in {"text_chunk", "procedure_chunk"}]

    assert len(text_chunks) == 2
    assert "第一章 操作说明" in text_chunks[0].full_text
    assert "第179条内容保持在同一标题下" in text_chunks[0].full_text
    assert "第二章 参数说明" in text_chunks[1].full_text
    assert all("第二章 参数说明" not in chunk.full_text for chunk in text_chunks[:1])


def test_docx_infers_numbered_level_three_headings_without_styles(tmp_path) -> None:
    docx_path = tmp_path / "numbered-headings.docx"
    _write_docx(
        docx_path,
        body="""
        <w:p><w:r><w:t>2 软件界面</w:t></w:r></w:p>
        <w:p><w:r><w:t>2.1 工具栏</w:t></w:r></w:p>
        <w:p><w:r><w:t>2.1.1 系统工具栏</w:t></w:r></w:p>
        <w:p><w:r><w:t>系统工具栏只包含保存、打开等系统操作。</w:t></w:r></w:p>
        <w:p><w:r><w:t>2.1.2 图形工具栏</w:t></w:r></w:p>
        <w:p><w:r><w:t>图形工具栏包含线宽度、线型、填充颜色等按钮。</w:t></w:r></w:p>
        <w:p><w:r><w:t>2.1.3 绘图工具栏</w:t></w:r></w:p>
        <w:p><w:r><w:t>绘图工具栏包含直线、矩形、文字等绘图元素。</w:t></w:r></w:p>
        """,
    )

    document = parse_docx(
        docx_path,
        tmp_path / "extracted",
        chunk_size=800,
        chunk_overlap=0,
        app_settings=Settings(ocr_enabled=False, vision_enabled=False),
    )

    chunks = [chunk for chunk in document.chunks if chunk.full_text]
    graphic = next(chunk for chunk in chunks if chunk.section_title == "2.1.2 图形工具栏")

    assert graphic.heading_path == ["2 软件界面", "2.1 工具栏", "2.1.2 图形工具栏"]
    assert "线宽度" in graphic.full_text
    assert "系统工具栏只包含" not in graphic.full_text
    assert "绘图工具栏包含" not in graphic.full_text


def test_docx_table_cell_image_gets_table_context(tmp_path) -> None:
    docx_path = tmp_path / "table-image.docx"
    _write_docx(
        docx_path,
        body="""
        <w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:r><w:t>Alarm table</w:t></w:r></w:p>
        <w:tbl>
          <w:tr>
            <w:tc><w:p><w:r><w:t>Code</w:t></w:r></w:p></w:tc>
            <w:tc><w:p><w:r><w:t>Icon</w:t></w:r></w:p></w:tc>
            <w:tc><w:p><w:r><w:t>Meaning</w:t></w:r></w:p></w:tc>
          </w:tr>
          <w:tr>
            <w:tc><w:p><w:r><w:t>E203</w:t></w:r></w:p></w:tc>
            <w:tc><w:p><w:r><w:drawing><a:blip r:embed="rId1"/></w:drawing></w:r></w:p></w:tc>
            <w:tc><w:p><w:r><w:t>Pressure high</w:t></w:r></w:p></w:tc>
          </w:tr>
        </w:tbl>
        """,
        rels='<Relationship Id="rId1" Type="image" Target="media/alarm.png"/>',
        media={"word/media/alarm.png": PNG_BYTES},
    )

    document = parse_docx(
        docx_path,
        tmp_path / "extracted",
        chunk_size=800,
        chunk_overlap=0,
        app_settings=Settings(ocr_enabled=False, vision_enabled=False),
    )

    image = next(chunk for chunk in document.chunks if chunk.chunk_type == "image_chunk")
    row = next(chunk for chunk in document.chunks if chunk.chunk_type == "table_row_chunk" and "E203" in chunk.text)
    table = next(chunk for chunk in document.chunks if chunk.chunk_type == "table_chunk")
    structured = json.loads(image.structured_json)
    table_context = json.loads(table.structured_json)["table_context"]

    assert row.parent_row_id
    assert image.parent_row_id == row.parent_row_id
    assert image.parent_cell_id.endswith("_col_2")
    assert row.row_image_ids == ["image-1"]
    assert image.row_image_ids == ["image-1"]
    assert "表格内图片" in image.context_before
    assert "列标题：Icon" in image.context_before
    assert "同行文字：E203 | Pressure high" in image.context_before
    assert "表格单元格上下文" in image.text
    assert "Code: E203" in image.text
    assert "Meaning: Pressure high" in row.text
    assert structured["is_table_cell_image"] is True
    assert structured["parent_row_id"] == row.parent_row_id
    assert "row_markdown" in structured
    assert "parent_table_markdown" in structured
    assert '"parent_chunk_type": "table_chunk"' in image.structured_json
    assert table_context["columns"] == ["Code", "Icon", "Meaning"]
    assert table_context["rows"][1]["images_by_column"]["Icon"][0]["image_id"] == "image-1"
    assert "](/api/knowledge/files/" in table_context["canonical_markdown"]
    assert "图片:image-1" not in table_context["canonical_markdown"]


def test_table_embedding_text_is_capped_but_metadata_keeps_full_table(tmp_path) -> None:
    rows = "\n".join(
        f"<w:tr><w:tc><w:p><w:r><w:t>Button {index}</w:t></w:r></w:p></w:tc>"
        f"<w:tc><w:p><w:r><w:t>{'Long description ' * 30}{index}</w:t></w:r></w:p></w:tc></w:tr>"
        for index in range(1, 90)
    )
    docx_path = tmp_path / "large-table.docx"
    _write_docx(
        docx_path,
        body=f"""
        <w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:r><w:t>Large toolbar</w:t></w:r></w:p>
        <w:tbl>
          <w:tr><w:tc><w:p><w:r><w:t>Name</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Description</w:t></w:r></w:p></w:tc></w:tr>
          {rows}
        </w:tbl>
        """,
    )

    document = parse_docx(
        docx_path,
        tmp_path / "extracted",
        chunk_size=800,
        chunk_overlap=0,
        app_settings=Settings(ocr_enabled=False, vision_enabled=False),
    )

    table = next(chunk for chunk in document.chunks if chunk.chunk_type == "table_chunk")
    table_context = json.loads(table.structured_json)["table_context"]

    assert len(table.text) < 8192
    assert "完整表格见 metadata.table_context" in table.text
    assert "Button 89" in table_context["canonical_markdown"]


def test_docx_paragraph_with_inline_button_image_gets_group_chunk(tmp_path) -> None:
    docx_path = tmp_path / "toolbar.docx"
    _write_docx(
        docx_path,
        body="""
        <w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:r><w:t>图形工具栏</w:t></w:r></w:p>
        <w:p>
          <w:r><w:t>放大按钮：用于放大当前图形画面。</w:t></w:r>
          <w:r><w:drawing><a:blip r:embed="rId1"/></w:drawing></w:r>
        </w:p>
        <w:p>
          <w:r><w:t>缩小按钮：用于缩小当前图形画面。</w:t></w:r>
          <w:r><w:drawing><a:blip r:embed="rId2"/></w:drawing></w:r>
        </w:p>
        """,
        rels="""
        <Relationship Id="rId1" Type="image" Target="media/zoom-in.png"/>
        <Relationship Id="rId2" Type="image" Target="media/zoom-out.png"/>
        """,
        media={
            "word/media/zoom-in.png": PNG_BYTES,
            "word/media/zoom-out.png": PNG_BYTES,
        },
    )

    document = parse_docx(
        docx_path,
        tmp_path / "extracted",
        chunk_size=800,
        chunk_overlap=0,
        app_settings=Settings(ocr_enabled=False, vision_enabled=False),
    )

    group = next(chunk for chunk in document.chunks if chunk.chunk_type == "inline_image_group_chunk" and "放大按钮" in chunk.text)
    image = next(chunk for chunk in document.chunks if chunk.chunk_type == "image_chunk" and chunk.image_id == "image-1")
    structured = json.loads(image.structured_json)

    assert group.parent_inline_id
    assert image.parent_inline_id == group.parent_inline_id
    assert group.inline_image_ids == ["image-1"]
    assert image.inline_image_ids == ["image-1"]
    assert "放大按钮" in image.context_before
    assert "段落/列表项内嵌图片" in image.context_before
    assert structured["is_inline_image"] is True
    assert structured["parent_inline_id"] == group.parent_inline_id


def _write_docx(
    path,
    *,
    body: str,
    rels: str = "",
    media: dict[str, bytes] | None = None,
) -> None:
    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
                xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
                xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
      <w:body>{body}</w:body>
    </w:document>"""
    rels_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>"""

    with zipfile.ZipFile(path, "w") as package:
        package.writestr("word/document.xml", document_xml)
        package.writestr("word/_rels/document.xml.rels", rels_xml)
        for member, content in (media or {}).items():
            package.writestr(member, content)
