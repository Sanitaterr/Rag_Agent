from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import threading
from typing import Any


@dataclass(frozen=True)
class OcrLine:
    """One OCR text line with optional geometry from the OCR engine."""

    text: str
    confidence: float = 0.0
    bbox: list[list[float]] | None = None


_OCR_LOCK = threading.Lock()


class PaddleOcrAdapter:
    """Lazy PaddleOCR adapter; missing local OCR never blocks indexing."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        lang: str = "ch",
        detection_model: str = "PP-OCRv5_mobile_det",
        recognition_model: str = "PP-OCRv5_mobile_rec",
    ) -> None:
        self._enabled = enabled
        self._lang = lang
        self._detection_model = detection_model
        self._recognition_model = recognition_model

    def extract(self, image_path: Path) -> tuple[list[OcrLine], str]:
        """Return OCR lines and a non-empty error string when OCR is unavailable."""
        if not self._enabled:
            return [], "OCR disabled by configuration."
        try:
            engine = _paddle_engine(self._lang, self._detection_model, self._recognition_model)
            # PaddleOCR/Paddle predictors share native runtime state; keep OCR
            # inference serialized while allowing remote vision calls to overlap.
            with _OCR_LOCK:
                raw_result = _run_ocr(engine, image_path)
            return _flatten_result(raw_result), ""
        except Exception as exc:
            return [], f"OCR failed: {exc}"


@lru_cache(maxsize=2)
def _paddle_engine(lang: str, detection_model: str, recognition_model: str) -> Any:
    """Import PaddleOCR only when the first image is processed."""
    from paddleocr import PaddleOCR

    configs = [
        {
            "lang": lang,
            "text_detection_model_name": detection_model,
            "text_recognition_model_name": recognition_model,
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
        },
        {"use_angle_cls": True, "lang": lang, "show_log": False},
        {"use_angle_cls": True, "lang": lang},
        {"use_textline_orientation": True, "lang": lang},
        {"lang": lang},
    ]
    last_error: Exception | None = None
    for config in configs:
        try:
            return PaddleOCR(**config)
        except Exception as exc:
            last_error = exc
            if "Unknown argument" not in str(exc):
                break
    raise last_error or RuntimeError("PaddleOCR initialization failed.")


def _run_ocr(engine: Any, image_path: Path) -> Any:
    """Run OCR across PaddleOCR 2.x and 3.x call signatures."""
    path_text = str(image_path)
    attempts = [
        lambda: engine.ocr(path_text, cls=True),
        lambda: engine.ocr(path_text),
        lambda: engine.predict(path_text),
    ]
    last_error: Exception | None = None
    for attempt in attempts:
        try:
            return attempt()
        except AttributeError as exc:
            last_error = exc
        except TypeError as exc:
            last_error = exc
            if "unexpected keyword argument" not in str(exc):
                break
    raise last_error or RuntimeError("PaddleOCR call failed.")


def _flatten_result(raw_result: Any) -> list[OcrLine]:
    """Normalize PaddleOCR's nested result variants into flat lines."""
    lines: list[OcrLine] = []
    pages = raw_result if isinstance(raw_result, list) else [raw_result]
    for page in pages:
        if isinstance(page, dict):
            lines.extend(_parse_result_dict(page))
            continue
        if hasattr(page, "json"):
            try:
                lines.extend(_parse_result_dict(page.json))
                continue
            except Exception:
                pass
        if not isinstance(page, list):
            continue
        for item in page:
            parsed = _parse_line(item)
            if parsed is not None:
                lines.append(parsed)
    return lines


def _parse_result_dict(item: dict[str, Any]) -> list[OcrLine]:
    """Parse PaddleOCR 3.x result dictionaries when available."""
    payload = item.get("res") if isinstance(item.get("res"), dict) else item
    texts = _first_present(payload, "rec_texts", "texts")
    scores = _first_present(payload, "rec_scores", "scores")
    boxes = _first_present(payload, "rec_boxes", "dt_polys")
    lines: list[OcrLine] = []
    for index, text in enumerate(texts):
        clean = str(text or "").strip()
        if not clean:
            continue
        lines.append(
            OcrLine(
                text=clean,
                confidence=_safe_float(scores[index] if index < len(scores) else 0.0),
                bbox=_box_to_list(boxes[index]) if index < len(boxes) else None,
            )
        )
    return lines


def _first_present(payload: dict[str, Any], *keys: str) -> list[Any]:
    """Return a sequence-like OCR field without boolean-testing numpy arrays."""
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        if hasattr(value, "tolist"):
            value = value.tolist()
        if isinstance(value, list):
            return value
    return []


def _box_to_list(value: Any) -> list[list[float]] | None:
    """Convert PaddleOCR bbox arrays into JSON-safe lists."""
    if value is None:
        return None
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, list):
        return None
    return value


def _parse_line(item: Any) -> OcrLine | None:
    if not isinstance(item, (list, tuple)) or len(item) < 2:
        return None
    bbox = item[0] if isinstance(item[0], list) else None
    payload = item[1]
    if isinstance(payload, (list, tuple)) and payload:
        text = str(payload[0] or "").strip()
        confidence = _safe_float(payload[1] if len(payload) > 1 else 0.0)
    else:
        text = str(payload or "").strip()
        confidence = 0.0
    return OcrLine(text=text, confidence=confidence, bbox=bbox) if text else None


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
