from __future__ import annotations

import re
import shutil
import threading
import time
from collections.abc import Callable
from uuid import uuid4
from pathlib import Path
from langsmith import traceable

from config.settings import Settings, settings
from services.rag.docx_parser import file_id_for_name, parse_docx
from services.rag.formatting import format_search_results
from services.rag.models import KnowledgeDocument
from services.rag.paths import STORAGE_DIR
from services.rag.vector_store import ChromaKnowledgeStore
from services.graphrag import graph_rag


class KnowledgeBaseService:
    """DOCX knowledge base where only Chroma-indexed files participate in RAG."""

    def __init__(self, storage_dir: Path = STORAGE_DIR, app_settings: Settings = settings) -> None:
        self._settings = app_settings
        self._storage_dir = storage_dir
        self._extracted_dir = storage_dir / ".extracted"
        self._documents: dict[str, KnowledgeDocument] = {}
        self._parsed_signatures: dict[str, tuple[str, float, int]] = {}
        self._parsed_visual_modes: dict[str, bool] = {}
        self._signature: tuple[tuple[str, float, int], ...] = ()
        self._vector_store = ChromaKnowledgeStore(app_settings)
        self._jobs: dict[str, dict] = {}
        self._job_lock = threading.Lock()

    def list_documents(self) -> list[dict]:
        """Return stored DOCX summaries without parsing every document."""
        paths = self._docx_files()
        self._prune_document_cache(paths)
        signatures = self._file_signatures(paths)
        indexed_ids = self._vector_store.indexed_file_ids(signatures)
        indexed_info = self._vector_store.indexed_document_info()
        return [
            self._file_summary(
                path,
                vectorized=file_id_for_name(path.name) in indexed_ids,
                vector_info=(
                    indexed_info.get(file_id_for_name(path.name), {})
                    if file_id_for_name(path.name) in indexed_ids
                    else {}
                ),
            )
            for path in paths
        ]

    def stats(self) -> dict:
        """Return aggregate numbers for the UI chart without slow indexing."""
        paths = self._docx_files()
        self._prune_document_cache(paths)
        documents = [self._documents[file_id_for_name(path.name)] for path in paths if file_id_for_name(path.name) in self._documents]
        signatures = self._file_signatures(paths)
        indexed_ids = self._vector_store.indexed_file_ids(signatures)
        indexed_info = self._vector_store.indexed_document_info()
        return {
            "files": len(paths),
            "chunks": sum(int(indexed_info.get(file_id, {}).get("chunks", 0) or 0) for file_id in indexed_ids),
            "images": sum(int(indexed_info.get(file_id, {}).get("images", 0) or 0) for file_id in indexed_ids)
            or sum(len(document.images) for document in documents),
            "image_chunks": sum(int(indexed_info.get(file_id, {}).get("image_chunks", 0) or 0) for file_id in indexed_ids),
            "table_chunks": sum(int(indexed_info.get(file_id, {}).get("table_chunks", 0) or 0) for file_id in indexed_ids),
            "safety_chunks": sum(int(indexed_info.get(file_id, {}).get("safety_chunks", 0) or 0) for file_id in indexed_ids),
            "fault_chunks": sum(int(indexed_info.get(file_id, {}).get("fault_chunks", 0) or 0) for file_id in indexed_ids),
            "size": sum(path.stat().st_size for path in paths),
            "vectorized_files": len(indexed_ids),
            "pending_files": max(len(paths) - len(indexed_ids), 0),
            "vector_store": "chroma" if indexed_ids else "none",
            "embedding_model": self._settings.embedding_model,
            "vector_error": self._vector_store.error,
            **graph_rag.stats(),
        }

    def preview_document(self, file_id: str) -> dict:
        """Return a text preview and extracted image metadata for one document."""
        document = self._ensure_document(file_id, analyze_visuals=False)
        preview_chunks = _preview_chunks(document.chunks)
        return {
            **self._document_summary(document),
            "preview": [
                {
                    "id": chunk.id,
                    "kind": chunk.kind,
                    "index": chunk.index,
                    "text": chunk.text,
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                    "line_start": chunk.line_start,
                    "line_end": chunk.line_end,
                    "block_index": chunk.block_index,
                    "block_start": chunk.block_start,
                    "block_end": chunk.block_end,
                    "heading_path": chunk.heading_path,
                    "section_title": chunk.section_title,
                    "heading_level": chunk.heading_level,
                    "chunk_strategy": chunk.chunk_strategy,
                    "images": chunk.images,
                    "chunk_type": chunk.chunk_type,
                    "visual_type": chunk.visual_type,
                    "image_id": chunk.image_id,
                    "image_path": chunk.image_path,
                    "context_before": chunk.context_before,
                    "context_after": chunk.context_after,
                    "ocr_text": chunk.ocr_text,
                    "description": chunk.description,
                    "risk_level": chunk.risk_level,
                    "structured_json": chunk.structured_json,
                    "parse_error": chunk.parse_error,
                    "parent_table_id": chunk.parent_table_id,
                    "parent_row_id": chunk.parent_row_id,
                    "parent_cell_id": chunk.parent_cell_id,
                    "row_image_ids": chunk.row_image_ids,
                    "row_text": chunk.row_text,
                    "table_cell_context": chunk.table_cell_context,
                    "parent_inline_id": chunk.parent_inline_id,
                    "inline_image_ids": chunk.inline_image_ids,
                    "inline_text": chunk.inline_text,
                }
                for chunk in preview_chunks
            ],
            "images": [
                {
                    "id": image.id,
                    "filename": image.filename,
                    "size": image.size,
                    "url": f"/api/knowledge/files/{document.id}/images/{image.id}",
                }
                for image in document.images
            ],
        }

    def graph_stats(self) -> dict:
        """Return Neo4j GraphRAG status for API callers."""
        return graph_rag.stats()

    def graph_visualization(self, limit: int = 120) -> dict:
        """Return Neo4j GraphRAG data for visualization."""
        return graph_rag.visualization(limit=limit)

    def initialize_graph_corpus(self) -> dict:
        """Initialize Neo4j from the configured industrial GraphRAG corpus."""
        return graph_rag.initialize_corpus()

    def reindex_graph_document(self, file_id: str) -> dict:
        """Rebuild Neo4j graph data for one uploaded DOCX."""
        document = self._ensure_document(file_id, analyze_visuals=False)
        graph_rag.delete_document(file_id)
        result = graph_rag.index_document(document)
        return {"file_id": file_id, **result, "stats": graph_rag.stats()}

    def image_path(self, file_id: str, image_id: str) -> Path:
        """Resolve an extracted image path for FastAPI FileResponse."""
        try:
            document = self._ensure_document(file_id, analyze_visuals=False)
            for image in document.images:
                if image.id == image_id:
                    return image.path
        except FileNotFoundError:
            pass
        # GraphRAG seed-corpus evidence uses stable IDs such as
        # DOC-P203 / IMG-DOC-P203-SUCP, which are not DOCX extraction IDs.
        # Fall back to the corpus assets so the existing image URL shape still
        # renders in chat answers and knowledge previews.
        return graph_rag.image_path(file_id, image_id)

    def save_upload(self, filename: str, source_path: Path) -> dict:
        """Copy a validated DOCX upload into storage and reindex."""
        safe_name = self._safe_docx_name(filename)
        target_path = self._storage_dir / safe_name
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target_path)
        self._invalidate_document(file_id_for_name(target_path.name))
        self._delete_vectorized_document(file_id_for_name(target_path.name))
        return self._file_summary(target_path, vectorized=False)

    def delete_document(self, file_id: str) -> None:
        """Delete one stored DOCX and its extracted assets."""
        path = self._path_for_file_id(file_id)
        path.unlink(missing_ok=True)
        shutil.rmtree(self._extracted_dir / file_id, ignore_errors=True)
        self._invalidate_document(file_id)
        self._delete_vectorized_document(file_id)

    def start_vectorization(self, file_id: str) -> dict:
        """Start a background vectorization task for one uploaded DOCX."""
        path = self._path_for_file_id(file_id)
        existing = self._running_job_for_file(file_id)
        if existing:
            existing["new"] = False
            return existing

        job_id = uuid4().hex
        job = {
            "id": job_id,
            "new": True,
            "file_id": file_id,
            "file_name": path.name,
            "status": "queued",
            "stage": "等待处理",
            "progress": 0,
            "processed": 0,
            "total": 0,
            "error": "",
            "started_at": time.time(),
            "finished_at": None,
        }
        with self._job_lock:
            self._jobs[job_id] = job
        return job.copy()

    def run_vectorization_job(self, job_id: str) -> None:
        """Parse, embed, and persist one document for a queued task."""
        job = self.vectorization_job(job_id)
        file_id = job["file_id"]
        try:
            self._update_job(job_id, status="running", stage="解析 DOCX / OCR / 图像描述", progress=5)
            path = self._path_for_file_id(file_id)

            def parse_progress(stage: str, processed: int, total_items: int) -> None:
                percent = 5 + int((processed / max(total_items, 1)) * 45)
                self._update_job(
                    job_id,
                    stage=stage,
                    processed=processed,
                    total=total_items,
                    progress=min(max(percent, 5), 50),
                )
                print(f"[RAG vectorize] {job['file_name']} - {stage} ({processed}/{total_items})", flush=True)

            document = self._ensure_document(
                file_id,
                path,
                analyze_visuals=True,
                force=True,
                progress=parse_progress,
            )
            signature = self._file_signature(path)

            total = len(document.chunks)
            self._update_job(job_id, stage="写入 Chroma", total=total, processed=0, progress=55)
            print(f"[RAG vectorize] {job['file_name']} - 写入 Chroma ({total} chunks)", flush=True)

            def update_progress(processed: int, total_chunks: int) -> None:
                percent = 55 + int((processed / max(total_chunks, 1)) * 43)
                self._update_job(job_id, processed=processed, total=total_chunks, progress=min(percent, 98))
                print(f"[RAG vectorize] {job['file_name']} - Chroma {processed}/{total_chunks}", flush=True)

            self._vector_store.index_document(document, signature, progress=update_progress)
            self._update_job(job_id, stage="鍐欏叆 Neo4j GraphRAG", progress=98)
            try:
                graph_rag.index_document(document)
            except Exception as graph_exc:
                # GraphRAG is an enhancement over Chroma RAG. Keep the file
                # vectorized even when the external Neo4j service is down.
                graph_rag.set_error(f"Neo4j graph index failed: {graph_exc}")
            self._update_job(
                job_id,
                status="completed",
                stage="已完成",
                progress=100,
                processed=total,
                total=total,
                finished_at=time.time(),
            )
        except Exception as exc:
            self._update_job(
                job_id,
                status="failed",
                stage="处理失败",
                error=str(exc),
                finished_at=time.time(),
            )

    def vectorization_job(self, job_id: str) -> dict:
        """Return one vectorization job status for frontend polling."""
        with self._job_lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise FileNotFoundError(job_id)
            return job.copy()

    @traceable(name="rag.pipeline", run_type="chain")
    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """Return top Chroma-ranked chunks; uploaded-only files are not RAG."""
        if not query.strip():
            return []

        limit = max(1, min(top_k, 10))
        paths = self._docx_files()
        indexed_ids = self._vector_store.indexed_file_ids(self._file_signatures(paths))
        if not indexed_ids:
            self._vector_store.error = "No files have been vectorized into Chroma."
            return []

        self._ensure_vector_index()
        try:
            return self._vector_store.search(query, limit, file_ids=indexed_ids)
        except Exception as exc:
            self._vector_store.error = f"Chroma search failed: {exc}"
            return []

    def prepare_graph_query(self, query: str, top_k: int = 5) -> dict:
        """Prepare RAG inputs as a dedicated LangGraph Studio node."""
        limit = max(1, min(top_k, 10))
        paths = self._docx_files()
        indexed_ids = self._vector_store.indexed_file_ids(self._file_signatures(paths))
        if not query.strip():
            return {"query": query, "limit": limit, "file_ids": [], "processed_query": {}, "error": "Empty query."}
        if not indexed_ids:
            self._vector_store.error = "No files have been vectorized into Chroma."
            return {
                "query": query,
                "limit": limit,
                "file_ids": [],
                "processed_query": self._vector_store.preprocess_for_graph(query),
                "error": self._vector_store.error,
            }

        self._ensure_vector_index()
        return {
            "query": query,
            "limit": limit,
            "file_ids": sorted(indexed_ids),
            "processed_query": self._vector_store.preprocess_for_graph(query),
            "error": self._vector_store.error,
        }

    def vector_recall_for_graph(self, processed_query: dict, file_ids: list[str]) -> list[dict]:
        """Run the vector recall stage for the LangGraph-visible RAG flow."""
        if not file_ids or self._vector_store.error:
            return []
        return self._vector_store.vector_recall_for_graph(processed_query, set(file_ids))

    def graph_recall_for_graph(self, query: str, top_k: int = 5) -> list[dict]:
        """Run Neo4j graph recall for the LangGraph-visible RAG flow."""
        try:
            return graph_rag.search(query, top_k=top_k)
        except Exception as exc:
            graph_rag.set_error(f"Neo4j graph recall failed: {exc}")
            return []

    def keyword_recall_for_graph(self, processed_query: dict, file_ids: list[str]) -> list[dict]:
        """Run heading and keyword recall for the LangGraph-visible RAG flow."""
        if not file_ids or self._vector_store.error:
            return []
        return self._vector_store.keyword_recall_for_graph(processed_query, set(file_ids))

    def rerank_for_graph(
        self,
        query: str,
        limit: int,
        processed_query: dict,
        vector_candidates: list[dict],
        keyword_candidates: list[dict],
        graph_candidates: list[dict] | None = None,
    ) -> list[dict]:
        """Rerank merged recall candidates for the LangGraph-visible RAG flow."""
        rag_results = []
        if not self._vector_store.error:
            rag_results = self._vector_store.rerank_for_graph(
                processed_query,
                [*vector_candidates, *keyword_candidates],
                query,
                limit,
            )
        return _merge_graph_and_rag_results(graph_candidates or [], rag_results, limit)

    def format_graph_sources(self, results: list[dict]) -> str:
        """Format final RAG sources for the ToolMessage consumed by the agent."""
        return format_search_results(results)

    def rebuild(self, *, force_vector: bool = True) -> None:
        """Force a full document parse and Chroma index refresh."""
        self._documents = {}
        self._parsed_signatures = {}
        self._parsed_visual_modes = {}
        self._ensure_all_documents()

        self._vector_store.reset()
        if self._documents:
            self._vector_store.ensure(self._documents, self._signature, force=force_vector)

    def _ensure_all_documents(self) -> None:
        """Parse all DOCX files only for retrieval, not for UI listing."""
        paths = self._docx_files()
        self._prune_document_cache(paths)
        for path in paths:
            self._ensure_document(file_id_for_name(path.name), path, analyze_visuals=True, force=True)
        self._signature = self._current_signature()

    def _ensure_vector_index(self) -> None:
        """Open the persisted Chroma index without auto-vectorizing pending files."""
        self._vector_store.ensure_open()

    def _ensure_document(
        self,
        file_id: str,
        path: Path | None = None,
        *,
        analyze_visuals: bool = False,
        force: bool = False,
        progress: Callable[[str, int, int], None] | None = None,
    ) -> KnowledgeDocument:
        """Parse one DOCX on demand and reuse the cached parse while unchanged."""
        path = path or self._path_for_file_id(file_id)
        signature = self._file_signature(path)
        cached = self._documents.get(file_id)
        cached_is_current = cached is not None and self._parsed_signatures.get(file_id) == signature
        cached_has_visuals = self._parsed_visual_modes.get(file_id, False)
        if cached_is_current and not force and (not analyze_visuals or cached_has_visuals):
            return cached

        document = parse_docx(
            path,
            self._extracted_dir,
            chunk_size=self._settings.rag_chunk_size,
            chunk_overlap=self._settings.rag_chunk_overlap,
            app_settings=self._settings,
            analyze_visuals=analyze_visuals,
            progress=progress,
        )
        self._documents[file_id] = document
        self._parsed_signatures[file_id] = signature
        self._parsed_visual_modes[file_id] = analyze_visuals
        return document

    def _current_signature(self) -> tuple[tuple[str, float, int], ...]:
        return tuple((path.name, path.stat().st_mtime, path.stat().st_size) for path in self._docx_files())

    def _file_signatures(self, paths: list[Path]) -> dict[str, tuple[str, float, int]]:
        return {file_id_for_name(path.name): self._file_signature(path) for path in paths}

    def _docx_files(self) -> list[Path]:
        if not self._storage_dir.exists():
            return []
        return sorted(
            path
            for path in self._storage_dir.glob("*.docx")
            if not path.name.startswith("~$") and path.is_file()
        )

    def _path_for_file_id(self, file_id: str) -> Path:
        for path in self._docx_files():
            if file_id_for_name(path.name) == file_id:
                return path
        raise FileNotFoundError(file_id)

    def _prune_document_cache(self, paths: list[Path]) -> None:
        current_ids = {file_id_for_name(path.name) for path in paths}
        for file_id in list(self._documents):
            if file_id not in current_ids:
                self._invalidate_document(file_id)

    def _invalidate_document(self, file_id: str) -> None:
        self._documents.pop(file_id, None)
        self._parsed_signatures.pop(file_id, None)
        self._parsed_visual_modes.pop(file_id, None)
        self._signature = ()

    def _delete_vectorized_document(self, file_id: str) -> None:
        self._vector_store.delete_document(file_id)
        self._vector_store.reset()
        graph_rag.delete_document(file_id)

    def _running_job_for_file(self, file_id: str) -> dict | None:
        with self._job_lock:
            for job in self._jobs.values():
                if job["file_id"] == file_id and job["status"] in {"queued", "running"}:
                    return job.copy()
        return None

    def _update_job(self, job_id: str, **changes: object) -> None:
        with self._job_lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(changes)

    @staticmethod
    def _document_summary(document: KnowledgeDocument) -> dict:
        return {
            "id": document.id,
            "name": document.name,
            "size": document.size,
            "modified_at": document.modified_at,
            "chunks": len(document.chunks),
            "images": len(document.images),
            "image_chunks": sum(1 for chunk in document.chunks if chunk.chunk_type == "image_chunk"),
            "table_chunks": sum(1 for chunk in document.chunks if chunk.chunk_type == "table_chunk"),
            "safety_chunks": sum(1 for chunk in document.chunks if chunk.chunk_type == "safety_chunk"),
            "fault_chunks": sum(1 for chunk in document.chunks if chunk.chunk_type == "fault_chunk"),
        }

    @staticmethod
    def _file_summary(path: Path, *, vectorized: bool = False, vector_info: dict | None = None) -> dict:
        stat = path.stat()
        vector_info = vector_info or {}
        return {
            "id": file_id_for_name(path.name),
            "name": path.name,
            "size": stat.st_size,
            "modified_at": stat.st_mtime,
            "chunks": int(vector_info.get("chunks", 0) or 0),
            "images": int(vector_info.get("images", 0) or 0),
            "image_chunks": int(vector_info.get("image_chunks", 0) or 0),
            "table_chunks": int(vector_info.get("table_chunks", 0) or 0),
            "safety_chunks": int(vector_info.get("safety_chunks", 0) or 0),
            "fault_chunks": int(vector_info.get("fault_chunks", 0) or 0),
            "vectorized": vectorized,
            "indexed_at": vector_info.get("indexed_at"),
        }

    @staticmethod
    def _file_signature(path: Path) -> tuple[str, float, int]:
        stat = path.stat()
        return (path.name, stat.st_mtime, stat.st_size)

    @staticmethod
    def _safe_docx_name(filename: str) -> str:
        name = Path(filename).name.strip()
        if not name.lower().endswith(".docx"):
            raise ValueError("Only .docx files are supported.")
        if name.startswith("~$"):
            raise ValueError("Temporary Word lock files are not supported.")
        return re.sub(r'[<>:"/\\|?*]', "_", name)


@traceable(name="rag.format_tool_result", run_type="chain")
def search_documents(query: str, top_k: int = 5) -> str:
    """Search the local knowledge base and return compact source snippets."""
    return format_search_results(knowledge_base.search(query, top_k=top_k))


knowledge_base = KnowledgeBaseService()


def _preview_chunks(chunks: list) -> list:
    """Show early document content and representative visual chunks together."""
    selected = list(chunks[:20])
    selected_ids = {chunk.id for chunk in selected}
    for chunk in chunks:
        if chunk.id in selected_ids or chunk.chunk_type not in {"image_chunk", "table_chunk", "safety_chunk", "fault_chunk"}:
            continue
        selected.append(chunk)
        selected_ids.add(chunk.id)
        if len(selected) >= 30:
            break
    return sorted(selected, key=lambda item: item.index)


def _merge_graph_and_rag_results(graph_results: list[dict], rag_results: list[dict], limit: int) -> list[dict]:
    """Merge graph and Chroma evidence without dropping exact graph hits."""
    merged: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in [*graph_results, *rag_results]:
        retrieval = str(item.get("retrieval") or "chroma")
        key = str(item.get("chunk_id") or item.get("alarm_code") or item.get("text") or "")
        identity = (retrieval, key)
        if key and identity in seen:
            continue
        if key:
            seen.add(identity)
        merged.append(item)
    merged.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
    return merged[: max(1, min(limit, 10))]
