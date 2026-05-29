from __future__ import annotations

from config.settings import Settings
from services.rag.visual_analyzer import VisualAnalyzer, _parse_json_object


def test_parse_json_object_keeps_nested_structured_fields() -> None:
    payload = """```json
{
  "visual_type": "generic_image",
  "ocr_text": "RESET",
  "description": "复位按钮图标",
  "risk_level": "",
  "structured": {
    "rows": [{"name": "E203", "value": "Pressure high"}]
  }
}
```"""

    parsed = _parse_json_object(payload)

    assert parsed["visual_type"] == "generic_image"
    assert parsed["structured"]["rows"][0]["name"] == "E203"


def test_invalid_vision_json_becomes_structured_warning_not_parse_error(tmp_path) -> None:
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"not-a-real-image-but-fake-client-does-not-read")
    analyzer = VisualAnalyzer(Settings(ocr_enabled=False, vision_enabled=True, vision_api_key="test-key"))
    analyzer._ocr = _FakeOcr()
    analyzer._vision = _FakeVision("模型解释：```json\n{\"description\": \"缺少结尾\" \n```")

    result = analyzer.analyze(
        image_path,
        document_name="manual.docx",
        section="故障表",
        context_before="",
        context_after="",
        table_cell_context="列标题：Icon\n本行字段：Code: E203 | Meaning: Pressure high",
    )

    assert result.error == ""
    assert result.structured["vision_parse_warning"].startswith("Vision JSON parse skipped")
    assert "模型解释" in result.structured["vision_raw_text_preview"]
    assert "表格单元格内容" in result.description


class _FakeOcr:
    def extract(self, image_path):
        return [], ""


class _FakeVision:
    def __init__(self, content: str) -> None:
        self._content = content

    @property
    def configured(self) -> bool:
        return True

    def analyze(self, image_path, prompt: str) -> str:
        return self._content
