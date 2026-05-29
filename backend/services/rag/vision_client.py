from __future__ import annotations

import base64
from pathlib import Path

import httpx

from config.settings import Settings


class VisionClient:
    """Small OpenAI-compatible chat completions client for image understanding."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def configured(self) -> bool:
        """Return whether a remote vision model can be called."""
        return bool(self._settings.vision_api_key and self._settings.vision_model)

    def analyze(self, image_path: Path, prompt: str) -> str:
        """Send one image and prompt to an OpenAI-compatible vision endpoint."""
        if not self.configured:
            raise RuntimeError("Vision model is not configured.")

        url = f"{_compatible_base_url(self._settings.vision_base_url)}/chat/completions"
        image_data = _data_url(image_path)
        payload = {
            "model": self._settings.vision_model,
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_data}},
                    ],
                }
            ],
        }
        headers = {
            "Authorization": f"Bearer {self._settings.vision_api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self._settings.vision_timeout_seconds) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        return str(data.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()


def _data_url(image_path: Path) -> str:
    suffix = image_path.suffix.lower().lstrip(".") or "png"
    mime = "jpeg" if suffix in {"jpg", "jpeg"} else suffix
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:image/{mime};base64,{encoded}"


def _compatible_base_url(base_url: str) -> str:
    """Accept DashScope native base URLs and route them to compatible mode."""
    normalized = base_url.rstrip("/")
    if normalized == "https://dashscope.aliyuncs.com/api/v1":
        return "https://dashscope.aliyuncs.com/compatible-mode/v1"
    if normalized.endswith("/api/v1"):
        return normalized[: -len("/api/v1")] + "/compatible-mode/v1"
    return normalized
