from __future__ import annotations

from dataclasses import dataclass
import json
from json import JSONDecodeError
from pathlib import Path
import re
from typing import Any

from config.settings import Settings
from services.rag.ocr import PaddleOcrAdapter
from services.rag.vision_client import VisionClient


VISUAL_TYPES = {
    "hmi_screen",
    "equipment_diagram",
    "process_flow",
    "table_screenshot",
    "safety_warning",
    "generic_image",
}


@dataclass(frozen=True)
class VisualAnalysis:
    """Structured text extracted from one image for typed RAG chunks."""

    visual_type: str
    ocr_text: str = ""
    description: str = ""
    risk_level: str = ""
    structured: dict[str, Any] | None = None
    error: str = ""


class VisualAnalyzer:
    """Combine local OCR, remote vision, and deterministic fallbacks."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._ocr = PaddleOcrAdapter(
            enabled=settings.ocr_enabled,
            lang=settings.ocr_lang,
            detection_model=settings.ocr_detection_model,
            recognition_model=settings.ocr_recognition_model,
        )
        self._vision = VisionClient(settings)

    @property
    def min_image_bytes(self) -> int:
        """Return the minimum image size worth OCR/vision processing."""
        return max(0, int(self._settings.vision_min_image_bytes or 0))

    @property
    def workers(self) -> int:
        """Return the configured visual-analysis parallelism."""
        return max(1, int(self._settings.rag_visual_workers or 1))

    def analyze(
        self,
        image_path: Path,
        *,
        document_name: str,
        section: str,
        context_before: str,
        context_after: str,
        table_cell_context: str = "",
    ) -> VisualAnalysis:
        """Return image understanding that remains useful when vision fails."""
        ocr_lines, ocr_error = self._ocr.extract(image_path)
        ocr_text = "、".join(line.text for line in ocr_lines if line.text)
        fallback_type = _rule_visual_type(ocr_text, section, table_cell_context, context_before, context_after)

        if not self._settings.vision_enabled or not self._vision.configured:
            return VisualAnalysis(
                visual_type=fallback_type,
                ocr_text=ocr_text,
                description=_fallback_description(fallback_type, ocr_text, table_cell_context, context_before, context_after),
                risk_level=_risk_level(ocr_text, section, table_cell_context, context_before, context_after),
                structured={"ocr_lines": [line.__dict__ for line in ocr_lines]},
                error=ocr_error or "Vision model is not configured.",
            )

        try:
            content = self._vision.analyze(
                image_path,
                _analysis_prompt(document_name, section, context_before, context_after, ocr_text, table_cell_context),
            )
            try:
                parsed = _parse_json_object(content)
                parse_warning = ""
            except JSONDecodeError as exc:
                parsed = {}
                parse_warning = f"Vision JSON parse skipped: {exc}"
            visual_type = str(parsed.get("visual_type") or fallback_type)
            if visual_type not in VISUAL_TYPES:
                visual_type = fallback_type
            structured = dict(parsed.get("structured") or {})
            if parse_warning:
                structured.update(
                    {
                        "vision_parse_warning": parse_warning,
                        "vision_raw_text_preview": _compact_preview(content),
                    }
                )
            return VisualAnalysis(
                visual_type=visual_type,
                ocr_text=str(parsed.get("ocr_text") or ocr_text),
                description=str(parsed.get("description") or _fallback_description(visual_type, ocr_text, table_cell_context, context_before, context_after)),
                risk_level=str(parsed.get("risk_level") or _risk_level(ocr_text, section, table_cell_context, context_before, context_after)),
                structured=structured,
                error=ocr_error,
            )
        except Exception as exc:
            return VisualAnalysis(
                visual_type=fallback_type,
                ocr_text=ocr_text,
                description=_fallback_description(fallback_type, ocr_text, table_cell_context, context_before, context_after),
                risk_level=_risk_level(ocr_text, section, table_cell_context, context_before, context_after),
                structured={"ocr_lines": [line.__dict__ for line in ocr_lines]},
                error="; ".join(item for item in [ocr_error, f"Vision failed: {exc}"] if item),
            )


def build_image_chunk_text(
    *,
    document_name: str,
    page: int | None,
    section: str,
    image_path: str,
    analysis: VisualAnalysis,
    context_before: str,
    context_after: str,
    table_cell_context: str = "",
) -> str:
    """Build the text that is embedded for one image chunk."""
    parts = [
        f"文档：{document_name}",
        f"页码：第 {page} 页" if page else "页码：未知",
        f"章节：{section or '未知'}",
        "类型：image_chunk",
        f"图片类型：{analysis.visual_type}",
        f"图片路径：{image_path}",
    ]
    if table_cell_context:
        parts.extend(["", "表格单元格上下文：", table_cell_context])
    parts.extend(
        [
            "",
            "图片前文：",
            context_before or "无",
            "",
            "OCR 内容：",
            analysis.ocr_text or "无",
            "",
            "图片描述：",
            analysis.description or "无",
        ]
    )
    if analysis.risk_level:
        parts.extend(["", f"风险等级：{analysis.risk_level}"])
    if analysis.structured:
        parts.extend(["", "结构化信息：", json.dumps(analysis.structured, ensure_ascii=False)])
    if context_after:
        parts.extend(["", "图片后文：", context_after])
    return "\n".join(parts)


def _analysis_prompt(
    document_name: str,
    section: str,
    context_before: str,
    context_after: str,
    ocr_text: str,
    table_cell_context: str = "",
) -> str:
    table_requirement = (
        f"\n表格单元格上下文：{table_cell_context}\n"
        "该图片是上述表格行中某一列的单元格值。描述时必须结合列标题、同行字段和单元格含义，说明该图在本行中代表什么。"
        if table_cell_context
        else ""
    )
    return f"""你是工业文档图像解析器。只输出 JSON，不要 Markdown。
字段：
{{
  "visual_type": "hmi_screen|equipment_diagram|process_flow|table_screenshot|safety_warning|generic_image",
  "ocr_text": "修正后的图片文字",
  "description": "面向工业 RAG 的图片说明，写清按钮、部件、流程、表格或安全要求",
  "risk_level": "high|medium|low|",
  "structured": {{}}
}}
文档：{document_name}
章节：{section}
前文：{context_before}
后文：{context_after}
本地 OCR：{ocr_text}
{table_requirement}
要求：HMI/SCADA/MES 截图写清按钮和操作结果；设备图写清编号、部件和位置关系；流程图写成步骤、条件和异常路径；表格截图优先还原为 markdown_table 或 rows；安全图写清风险、必须行为和禁止行为。"""


def _parse_json_object(text: str) -> dict[str, Any]:
    text = _json_candidate_text(text)
    parsed = _loads_relaxed_json(text)
    return parsed if isinstance(parsed, dict) else {}


def _json_candidate_text(text: str) -> str:
    """Extract the first balanced JSON object from model output."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()

    start = text.find("{")
    if start < 0:
        return text

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return text[start:]


def _loads_relaxed_json(text: str) -> dict[str, Any]:
    """Parse common LLM JSON variants without hiding genuinely unusable output."""
    cleaned = text.strip().removeprefix("\ufeff")
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    try:
        parsed = json.loads(cleaned)
    except JSONDecodeError:
        parsed = json.JSONDecoder(strict=False).decode(cleaned)
    return parsed if isinstance(parsed, dict) else {}


def _compact_preview(text: str, limit: int = 600) -> str:
    """Keep enough raw model text for diagnosis without bloating metadata."""
    compact = re.sub(r"\s+", " ", text or "").strip()
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit]}..."


def _rule_visual_type(*texts: str) -> str:
    text = " ".join(item.lower() for item in texts if item)
    if any(term in text for term in ["危险", "禁止", "警告", "必须", "防护", "ppe", "高压", "高温"]):
        return "safety_warning"
    if any(term in text for term in ["启动", "停止", "复位", "确认", "手动", "自动", "hmi", "scada", "mes"]):
        return "hmi_screen"
    if any(term in text for term in ["故障代码", "参数", "点检", "代码", "含义", "处理方法"]):
        return "table_screenshot"
    if any(term in text for term in ["流程", "开始", "结束", "条件", "分支", "冷却", "加热", "保温"]):
        return "process_flow"
    if re.search(r"[A-Z]{2,}\d{2,}[A-Z0-9]*", text) or any(term in text for term in ["轴承", "电机", "阀", "泵", "传感器"]):
        return "equipment_diagram"
    return "generic_image"


def _fallback_description(visual_type: str, ocr_text: str, table_cell_context: str, context_before: str, context_after: str) -> str:
    context = " ".join(item for item in [table_cell_context, context_before, context_after] if item)
    if ocr_text:
        return f"该图片识别到文字：{ocr_text}。结合上下文可用于检索相关操作、部件、流程、表格或安全说明。"
    if table_cell_context:
        return f"该图片是表格单元格内容，所在行列上下文为：{table_cell_context[:240]}。视觉模型未配置，已保留原图路径供人工核对。"
    if context:
        return f"该图片位于相关上下文附近：{context[:240]}。视觉模型未配置，已保留原图路径供人工核对。"
    return f"该图片被归类为 {visual_type}，视觉模型未配置，已保留原图路径供人工核对。"


def _risk_level(*texts: str) -> str:
    text = " ".join(texts)
    if any(term in text for term in ["高压", "高温", "触电", "爆炸", "禁止", "危险", "急停"]):
        return "high"
    if any(term in text for term in ["警告", "注意", "防护", "佩戴", "检修"]):
        return "medium"
    return ""
