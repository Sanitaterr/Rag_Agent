from __future__ import annotations

from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2]
STORAGE_DIR = BACKEND_DIR / "storage"
EXTRACTED_DIR = STORAGE_DIR / ".extracted"
CHROMA_COLLECTION = "rag_docx_chunks"
MANIFEST_FILE_NAME = ".chroma_manifest.json"
