from __future__ import annotations

import json
import re
import shutil
import time
from collections.abc import Callable
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langsmith import traceable

from config.settings import Settings
from services.rag.docx_parser import CHUNK_STRATEGY, file_id_for_name
from services.rag.models import DocChunk, KnowledgeDocument
from services.rag.paths import CHROMA_COLLECTION, MANIFEST_FILE_NAME
from services.rag.query_processing import ProcessedQuery, QueryVariant, preprocess_query
from services.rag.reranker import QwenReranker
from services.rag.retrieval import RetrievalCandidate, keyword_score, merge_candidate, rule_rerank

FileSignature = tuple[str, float, int]
ProgressCallback = Callable[[int, int], None]
SOURCE_SCHEMA_VERSION = 9


class ChromaKnowledgeStore:
    """Persistence and retrieval adapter for Chroma-backed RAG chunks."""

    def __init__(self, app_settings: Settings) -> None:
        self._settings = app_settings
        self._chroma_dir = Path(app_settings.chroma_persist_dir)
        self._manifest_path = self._chroma_dir / MANIFEST_FILE_NAME
        self._store: Chroma | None = None
        self._reranker = QwenReranker(app_settings)
        self.error = ""

    @property
    def available(self) -> bool:
        """Return whether the Chroma collection is ready for search."""
        return self._store is not None

    def ensure(
        self,
        documents: dict[str, KnowledgeDocument],
        signature: tuple[FileSignature, ...],
        *,
        force: bool,
    ) -> None:
        """Open an existing Chroma index or rebuild it when source files changed."""
        self.error = ""
        if not self._settings.embedding_api_key:
            self._store = None
            self.error = "EMBEDDING_API_KEY or DASHSCOPE_API_KEY is not configured."
            return

        try:
            if force or not self.manifest_matches(signature):
                self._rebuild(documents, signature)
            else:
                self._store = self._new_chroma()
        except Exception as exc:
            self._store = None
            self.error = f"Chroma index unavailable: {exc}"

    def ensure_open(self) -> bool:
        """Open the persisted Chroma collection without rebuilding indexes."""
        self.error = ""
        if not self._settings.embedding_api_key:
            self._store = None
            self.error = "EMBEDDING_API_KEY or DASHSCOPE_API_KEY is not configured."
            return False

        try:
            if self._store is None:
                self._store = self._new_chroma()
            return True
        except Exception as exc:
            self._store = None
            self.error = f"Chroma index unavailable: {exc}"
            return False

    def reset(self) -> None:
        """Drop the in-memory Chroma handle."""
        self._store = None

    def manifest_matches(self, signature: tuple[FileSignature, ...]) -> bool:
        """Return whether the persisted Chroma index matches current files/settings."""
        expected = {file_id_for_name(item[0]): item for item in signature}
        return all(self.is_indexed(file_id, item) for file_id, item in expected.items())

    def is_indexed(self, file_id: str, signature: FileSignature) -> bool:
        """Return whether one file's current signature is present in Chroma."""
        manifest = self._read_manifest()
        if manifest.get("settings") != self._settings_manifest():
            return False
        document_info = manifest.get("documents", {}).get(file_id)
        return bool(document_info and tuple(document_info.get("signature", [])) == signature)

    def indexed_file_ids(self, signatures: dict[str, FileSignature]) -> set[str]:
        """Return file IDs whose current signatures are already vectorized."""
        return {file_id for file_id, signature in signatures.items() if self.is_indexed(file_id, signature)}

    def indexed_document_info(self) -> dict[str, dict]:
        """Return persisted per-document Chroma metadata from the manifest."""
        manifest = self._read_manifest()
        if manifest.get("settings") != self._settings_manifest():
            return {}
        documents = manifest.get("documents", {})
        return documents if isinstance(documents, dict) else {}

    @traceable(name="rag.hybrid_search", run_type="chain")
    def search(self, query: str, limit: int, *, file_ids: set[str] | None = None) -> list[dict]:
        """Run hybrid retrieval and rerank over manifest-valid files only."""
        if self._store is None and not self.ensure_open():
            return []
        processed_query = preprocess_query(query, self._settings)
        candidates: dict[str, RetrievalCandidate] = {}

        for document, score, source in self._vector_candidates(processed_query.vector_queries, file_ids):
            merge_candidate(candidates, document, score, "vector")
            chunk_id = str(document.metadata.get("chunk_id", ""))
            if chunk_id in candidates:
                candidates[chunk_id].sources.add(source)

        keyword_matches = self._keyword_candidates(processed_query, file_ids)
        for document, score in keyword_matches[: self._settings.rag_keyword_candidates]:
            merge_candidate(candidates, document, min(0.99, 0.45 + score), "keyword")

        ranked = rule_rerank(processed_query, list(candidates.values()))
        rerank_pool = ranked[: self._settings.rag_rerank_candidates]
        reranked = self._reranker.rerank(query, rerank_pool, top_n=limit)
        results = [_result_payload(candidate.document, candidate.rerank_score or candidate.score) for candidate in reranked]
        return self._attach_table_context_evidence(results, file_ids, processed_query)

    def preprocess_for_graph(self, query: str) -> dict:
        """Return serializable query features for a visible LangGraph RAG node."""
        processed_query = preprocess_query(query, self._settings)
        return {
            "original": processed_query.original,
            "normalized": processed_query.normalized,
            "variants": processed_query.variants,
            "tokens": processed_query.tokens,
            "primary_queries": [_query_variant_payload(item) for item in processed_query.primary_queries],
            "expansion_queries": [_query_variant_payload(item) for item in processed_query.expansion_queries],
            "rewritten_query": processed_query.rewritten_query,
            "sub_queries": processed_query.sub_queries,
            "hyde_document": processed_query.hyde_document,
            "strategy": processed_query.strategy,
            "llm_error": processed_query.llm_error,
        }

    def vector_recall_for_graph(self, processed_query: dict, file_ids: set[str] | None) -> list[dict]:
        """Run vector recall as its own LangGraph-visible step."""
        query = _processed_query_from_payload(processed_query)
        return [
            _candidate_payload(RetrievalCandidate(document=document, score=score, sources={"vector", source}))
            for document, score, source in self._vector_candidates(query.vector_queries, file_ids)
        ]

    def keyword_recall_for_graph(self, processed_query: dict, file_ids: set[str] | None) -> list[dict]:
        """Run heading/body keyword recall as its own LangGraph-visible step."""
        query = _processed_query_from_payload(processed_query)
        return [
            _candidate_payload(RetrievalCandidate(document=document, score=min(0.99, 0.45 + score), sources={"keyword"}))
            for document, score in self._keyword_candidates(query, file_ids)[: self._settings.rag_keyword_candidates]
        ]

    def rerank_for_graph(self, processed_query: dict, candidates: list[dict], query: str, limit: int) -> list[dict]:
        """Merge and rerank recall candidates for a visible LangGraph RAG node."""
        merged: dict[str, RetrievalCandidate] = {}
        for candidate_payload in candidates:
            candidate = _candidate_from_payload(candidate_payload)
            for source in candidate.sources or {"retrieval"}:
                merge_candidate(merged, candidate.document, candidate.score, source)

        query_features = _processed_query_from_payload(processed_query)
        rule_ranked = rule_rerank(query_features, list(merged.values()))
        rerank_pool = rule_ranked[: self._settings.rag_rerank_candidates]
        reranked = self._reranker.rerank(query, rerank_pool, top_n=limit)
        results = [_result_payload(candidate.document, candidate.rerank_score or candidate.score) for candidate in reranked]
        return self._attach_table_context_evidence(results, None, query_features)

    def index_document(
        self,
        document: KnowledgeDocument,
        signature: FileSignature,
        *,
        progress: ProgressCallback | None = None,
    ) -> None:
        """Vectorize and persist one parsed document into Chroma in visible batches."""
        self.error = ""
        if not self.ensure_open():
            raise RuntimeError(self.error)

        self.delete_document(document.id)
        chunks = document.chunks
        total = len(chunks)
        if not total:
            self._mark_indexed(document, signature)
            progress and progress(0, 0)
            return

        batch_size = max(1, self._settings.embedding_batch_size)
        for start in range(0, total, batch_size):
            batch = chunks[start : start + batch_size]
            self._store.add_documents(
                documents=[_chunk_document(chunk) for chunk in batch],
                ids=[chunk.id for chunk in batch],
            )
            progress and progress(min(start + len(batch), total), total)

        self._mark_indexed(document, signature)

    def delete_document(self, file_id: str) -> None:
        """Delete one document's Chroma chunks and manifest entry when present."""
        if self.ensure_open():
            try:
                self._store._collection.delete(where={"file_id": file_id})
            except Exception:
                # Chroma deletion is best-effort here; stale manifests are removed below.
                pass
        manifest = self._read_manifest()
        documents = manifest.setdefault("documents", {})
        if isinstance(documents, dict) and file_id in documents:
            documents.pop(file_id, None)
            self._write_manifest_data(manifest)

    def _rebuild(self, documents: dict[str, KnowledgeDocument], signature: tuple[FileSignature, ...]) -> None:
        shutil.rmtree(self._chroma_dir, ignore_errors=True)
        self._chroma_dir.mkdir(parents=True, exist_ok=True)
        store = self._new_chroma()
        chunks = [chunk for document in documents.values() for chunk in document.chunks]
        if chunks:
            store.add_documents(
                documents=[_chunk_document(chunk) for chunk in chunks],
                ids=[chunk.id for chunk in chunks],
            )
        self._write_manifest(signature, documents)
        self._store = store

    def _new_chroma(self) -> Chroma:
        return Chroma(
            collection_name=CHROMA_COLLECTION,
            embedding_function=self._new_embeddings(),
            persist_directory=str(self._chroma_dir),
        )

    def _new_embeddings(self) -> OpenAIEmbeddings:
        return OpenAIEmbeddings(
            model=self._settings.embedding_model,
            api_key=self._settings.embedding_api_key,
            base_url=self._settings.embedding_base_url,
            dimensions=self._settings.embedding_dimensions,
            chunk_size=self._settings.embedding_batch_size,
            check_embedding_ctx_length=False,
        )

    @traceable(name="rag.vector_recall", run_type="retriever")
    def _vector_candidates(
        self,
        query_variants: list[QueryVariant],
        file_ids: set[str] | None,
    ) -> list[tuple[Document, float, str]]:
        results: list[tuple[Document, float, str]] = []
        if self._store is None:
            return results

        chroma_filter = _file_filter(file_ids)
        for variant in query_variants[:8]:
            try:
                kwargs = {"k": self._settings.rag_vector_candidates}
                if chroma_filter:
                    kwargs["filter"] = chroma_filter
                matches = self._store.similarity_search_with_score(variant.text, **kwargs)
            except Exception:
                continue
            results.extend(
                (document, _distance_to_score(distance) * variant.weight, variant.role)
                for document, distance in matches
            )
        return results

    @traceable(name="rag.keyword_heading_recall", run_type="retriever")
    def _keyword_candidates(
        self,
        processed_query,
        file_ids: set[str] | None,
    ) -> list[tuple[Document, float]]:
        matches = [
            (document, score)
            for document in self._documents_for_files(file_ids)
            for score in [keyword_score(processed_query, document)]
            if score > 0
        ]
        matches.sort(key=lambda item: item[1], reverse=True)
        return matches

    def _documents_for_files(self, file_ids: set[str] | None) -> list[Document]:
        if self._store is None:
            return []
        try:
            kwargs = {"include": ["documents", "metadatas"]}
            chroma_filter = _file_filter(file_ids)
            if chroma_filter:
                kwargs["where"] = chroma_filter
            data = self._store.get(**kwargs)
        except Exception:
            return []

        documents = [
            Document(page_content=text, metadata=metadata)
            for text, metadata in zip(data.get("documents") or [], data.get("metadatas") or [], strict=False)
            if isinstance(text, str) and isinstance(metadata, dict)
        ]
        valid_documents = [
            document
            for document in documents
            if str(document.metadata.get("chunk_strategy", "")) == CHUNK_STRATEGY
        ]
        return valid_documents

    def _attach_table_context_evidence(
        self,
        results: list[dict],
        file_ids: set[str] | None,
        processed_query: ProcessedQuery | None = None,
    ) -> list[dict]:
        """Attach complete table chunks for any table-related retrieval hit."""
        expanded = []
        for item in results:
            payload = dict(item)
            # A directly retrieved table chunk is already the authority; annotate
            # it in place so callers do not need a synthetic table_context hit.
            table_context = payload.get("table_context") or {}
            if processed_query is not None and isinstance(table_context, dict) and table_context:
                payload["matched_row_ids"] = _matched_table_row_ids(table_context, processed_query)
                payload["is_table_listing"] = _query_requests_table_listing(processed_query)
            expanded.append(payload)

        table_ids = {
            str(item.get("parent_table_id") or "")
            for item in expanded
            if item.get("parent_table_id")
        }
        documents = self._documents_for_files(file_ids)
        if processed_query is not None:
            table_ids.update(_section_table_ids_for_query(documents, processed_query))
        if not table_ids:
            return expanded

        seen_chunk_ids = {str(item.get("chunk_id") or "") for item in expanded}
        for document in documents:
            metadata = document.metadata
            chunk_id = str(metadata.get("chunk_id") or "")
            chunk_type = str(metadata.get("chunk_type", metadata.get("kind", "")))
            parent_table_id = str(metadata.get("parent_table_id") or "")
            if chunk_type != "table_chunk" or parent_table_id not in table_ids or chunk_id in seen_chunk_ids:
                continue
            expanded_metadata = dict(metadata)
            expanded_metadata["retrieval"] = "table_context"
            payload = _result_payload(Document(page_content=document.page_content, metadata=expanded_metadata), 0.0)
            if processed_query is not None:
                payload["matched_row_ids"] = _matched_table_row_ids(payload.get("table_context", {}), processed_query)
                payload["is_table_listing"] = _query_requests_table_listing(processed_query)
            expanded.append(payload)
            seen_chunk_ids.add(chunk_id)
        return expanded

    def _write_manifest(self, signature: tuple[FileSignature, ...], documents: dict[str, KnowledgeDocument]) -> None:
        manifest = self._base_manifest()
        manifest["documents"] = {
            document.id: {
                "name": document.name,
                "signature": list(file_signature),
                "chunks": len(document.chunks),
                "images": len(document.images),
                **_chunk_type_counts(document),
                "indexed_at": time.time(),
                "chunk_strategy": CHUNK_STRATEGY,
            }
            for document in documents.values()
            for file_signature in signature
            if file_signature[0] == document.name
        }
        self._write_manifest_data(manifest)

    def _mark_indexed(self, document: KnowledgeDocument, signature: FileSignature) -> None:
        manifest = self._read_manifest()
        if manifest.get("settings") != self._settings_manifest():
            manifest = self._base_manifest()
        documents = manifest.setdefault("documents", {})
        documents[document.id] = {
            "name": document.name,
            "signature": list(signature),
            "chunks": len(document.chunks),
            "images": len(document.images),
            **_chunk_type_counts(document),
            "indexed_at": time.time(),
            "chunk_strategy": CHUNK_STRATEGY,
        }
        self._write_manifest_data(manifest)

    def _read_manifest(self) -> dict:
        if not self._manifest_path.exists():
            return self._base_manifest()
        try:
            manifest = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return self._base_manifest()
        if manifest.get("version") == 2:
            return manifest
        return self._legacy_manifest(manifest)

    def _write_manifest_data(self, manifest: dict) -> None:
        self._chroma_dir.mkdir(parents=True, exist_ok=True)
        self._manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    def _base_manifest(self) -> dict:
        return {
            "version": 2,
            "settings": self._settings_manifest(),
            "documents": {},
        }

    def _settings_manifest(self) -> dict:
        return {
            "embedding_model": self._settings.embedding_model,
            "embedding_base_url": self._settings.embedding_base_url,
            "embedding_dimensions": self._settings.embedding_dimensions,
            "chunk_size": self._settings.rag_chunk_size,
            "chunk_overlap": self._settings.rag_chunk_overlap,
            "collection": CHROMA_COLLECTION,
            "source_schema_version": SOURCE_SCHEMA_VERSION,
            "chunk_strategy": CHUNK_STRATEGY,
            "ocr_enabled": self._settings.ocr_enabled,
            "ocr_lang": self._settings.ocr_lang,
            "vision_enabled": self._settings.vision_enabled,
            "vision_model": self._settings.vision_model,
            "vision_min_image_bytes": self._settings.vision_min_image_bytes,
            "rag_visual_workers": self._settings.rag_visual_workers,
            "ocr_detection_model": self._settings.ocr_detection_model,
            "ocr_recognition_model": self._settings.ocr_recognition_model,
        }

    def _legacy_manifest(self, manifest: dict) -> dict:
        """Convert the old all-or-nothing manifest format to per-file records."""
        converted = self._base_manifest()
        settings_keys = set(converted["settings"])
        legacy_settings = {key: manifest.get(key) for key in settings_keys}
        if legacy_settings != converted["settings"]:
            return converted
        for item in manifest.get("files", []):
            if not isinstance(item, list) or len(item) != 3:
                continue
            file_id = file_id_for_name(str(item[0]))
            converted["documents"][file_id] = {
                "name": str(item[0]),
                "signature": item,
                "chunks": 0,
                "indexed_at": 0,
            }
        return converted


def _query_requests_table_listing(processed_query: ProcessedQuery) -> bool:
    """Return whether the user is asking for a table/list rather than one exact row."""
    text = " ".join([processed_query.original, processed_query.normalized, *processed_query.variants])
    return any(term in text for term in ["哪些", "列表", "一览", "所有", "全部", "具体", "按钮"])


def _section_table_ids_for_query(documents: list[Document], processed_query: ProcessedQuery) -> set[str]:
    """Find parent tables whose section title is explicitly named by the user."""
    query_text = " ".join([processed_query.original, processed_query.normalized, *processed_query.variants])
    table_ids: set[str] = set()
    for document in documents:
        metadata = document.metadata
        section_title = str(metadata.get("section_title") or "")
        parent_table_id = str(metadata.get("parent_table_id") or "")
        if not section_title or not parent_table_id:
            continue
        if section_title in query_text:
            table_ids.add(parent_table_id)
    return table_ids


def _chunk_type_counts(document: KnowledgeDocument) -> dict[str, int]:
    """Persist typed chunk counts so the UI can show indexed stats quickly."""
    return {
        "image_chunks": sum(1 for chunk in document.chunks if chunk.chunk_type == "image_chunk"),
        "table_chunks": sum(1 for chunk in document.chunks if chunk.chunk_type == "table_chunk"),
        "safety_chunks": sum(1 for chunk in document.chunks if chunk.chunk_type == "safety_chunk"),
        "fault_chunks": sum(1 for chunk in document.chunks if chunk.chunk_type == "fault_chunk"),
        "table_row_chunks": sum(1 for chunk in document.chunks if chunk.chunk_type == "table_row_chunk"),
        "inline_image_group_chunks": sum(1 for chunk in document.chunks if chunk.chunk_type == "inline_image_group_chunk"),
    }


def _chunk_document(chunk: DocChunk) -> Document:
    metadata = {
        "file_id": chunk.file_id,
        "file_name": chunk.file_name,
        "chunk_id": chunk.id,
        "kind": chunk.kind,
        "index": chunk.index,
        "line_start": chunk.line_start,
        "line_end": chunk.line_end,
        "block_index": chunk.block_index,
        "block_start": chunk.block_start,
        "block_end": chunk.block_end,
        "heading_path": json.dumps(chunk.heading_path, ensure_ascii=False),
        "section_title": chunk.section_title,
        "heading_level": chunk.heading_level,
        "chunk_strategy": chunk.chunk_strategy,
        "images": json.dumps(chunk.images, ensure_ascii=False),
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
        "full_text": chunk.full_text,
        "parent_table_id": chunk.parent_table_id,
        "parent_row_id": chunk.parent_row_id,
        "parent_cell_id": chunk.parent_cell_id,
        "row_image_ids": json.dumps(chunk.row_image_ids, ensure_ascii=False),
        "row_text": chunk.row_text,
        "table_cell_context": chunk.table_cell_context,
        "parent_inline_id": chunk.parent_inline_id,
        "inline_image_ids": json.dumps(chunk.inline_image_ids, ensure_ascii=False),
        "inline_text": chunk.inline_text,
        "retrieval": "chroma",
    }
    if chunk.page_start is not None:
        metadata["page_start"] = chunk.page_start
    if chunk.page_end is not None:
        metadata["page_end"] = chunk.page_end
    return Document(
        page_content=chunk.text,
        metadata={key: value for key, value in metadata.items() if value is not None},
    )


def _result_payload(document: Document, score: float) -> dict:
    metadata = document.metadata
    payload = {
        "score": round(score, 3),
        "file_id": str(metadata.get("file_id", "")),
        "file_name": str(metadata.get("file_name", "")),
        "chunk_id": str(metadata.get("chunk_id", "")),
        "kind": str(metadata.get("kind", "")),
        "chunk_type": str(metadata.get("chunk_type", metadata.get("kind", ""))),
        "visual_type": str(metadata.get("visual_type", "")),
        "index": int(metadata.get("index", 0) or 0),
        "page_start": _optional_int(metadata.get("page_start")),
        "page_end": _optional_int(metadata.get("page_end")),
        "line_start": _optional_int(metadata.get("line_start")),
        "line_end": _optional_int(metadata.get("line_end")),
        "block_index": int(metadata.get("block_index", 0) or 0),
        "block_start": int(metadata.get("block_start", 0) or 0),
        "block_end": int(metadata.get("block_end", 0) or 0),
        "heading_path": _json_list(metadata.get("heading_path", "[]")),
        "section_title": str(metadata.get("section_title", "")),
        "heading_level": int(metadata.get("heading_level", 0) or 0),
        "chunk_strategy": str(metadata.get("chunk_strategy", "")),
        "text": document.page_content,
        "images": _json_list(metadata.get("images", "[]")),
        "image_id": str(metadata.get("image_id", "")),
        "image_path": str(metadata.get("image_path", "")),
        "context_before": str(metadata.get("context_before", "")),
        "context_after": str(metadata.get("context_after", "")),
        "ocr_text": str(metadata.get("ocr_text", "")),
        "description": str(metadata.get("description", "")),
        "risk_level": str(metadata.get("risk_level", "")),
        "structured_json": str(metadata.get("structured_json", "")),
        "parse_error": str(metadata.get("parse_error", "")),
        "full_text": str(metadata.get("full_text", "")),
        "parent_table_id": str(metadata.get("parent_table_id", "")),
        "parent_row_id": str(metadata.get("parent_row_id", "")),
        "parent_cell_id": str(metadata.get("parent_cell_id", "")),
        "row_image_ids": _json_list(metadata.get("row_image_ids", "[]")),
        "row_text": str(metadata.get("row_text", "")),
        "table_cell_context": str(metadata.get("table_cell_context", "")),
        "parent_inline_id": str(metadata.get("parent_inline_id", "")),
        "inline_image_ids": _json_list(metadata.get("inline_image_ids", "[]")),
        "inline_text": str(metadata.get("inline_text", "")),
        "retrieval": str(metadata.get("retrieval", "chroma")),
    }
    table_context = _table_context_payload(payload["structured_json"])
    if table_context:
        payload["table_context"] = table_context
        payload["canonical_markdown"] = str(table_context.get("canonical_markdown") or "")
    if payload["full_text"]:
        payload["text"] = payload["full_text"]
    payload["related_images"] = _related_images_payload(payload)
    return payload


def _table_context_payload(structured_json: str) -> dict:
    """Extract complete table context from a table chunk payload."""
    if not structured_json:
        return {}
    try:
        structured = json.loads(structured_json)
    except json.JSONDecodeError:
        return {}
    table_context = structured.get("table_context") if isinstance(structured, dict) else None
    return table_context if isinstance(table_context, dict) else {}


def _matched_table_row_ids(table_context: dict, processed_query: ProcessedQuery) -> list[str]:
    """Mark rows whose structured cells overlap the query; listing requests keep all rows."""
    if not table_context:
        return []
    if _query_requests_table_listing(processed_query):
        return [str(row.get("row_id") or "") for row in table_context.get("rows") or [] if row.get("row_id")]

    matched: list[str] = []
    query_terms = [processed_query.original, processed_query.normalized, *processed_query.variants, *processed_query.tokens]
    for row in table_context.get("rows") or []:
        row_text_parts = list((row.get("cells") or {}).values())
        for images in (row.get("images_by_column") or {}).values():
            for image in images:
                row_text_parts.extend(
                    str(image.get(key) or "")
                    for key in ["alt", "ocr", "description", "caption", "row_name", "column_name", "context"]
                )
        row_text = " ".join(row_text_parts).lower()
        if any(str(term).lower().strip() and str(term).lower().strip() in row_text for term in query_terms):
            row_id = str(row.get("row_id") or "")
            if row_id:
                matched.append(row_id)
    return matched


def _related_images_payload(item: dict) -> list[dict]:
    image_ids: list[str] = []
    if item.get("image_id"):
        image_ids.append(str(item["image_id"]))
    chunk_type = str(item.get("chunk_type") or item.get("kind") or "")
    for image_id in [
        *(item.get("row_image_ids") or []),
        *(item.get("inline_image_ids") or []),
        *(item.get("images") or []),
        *_image_ids_from_text(str(item.get("text") or "")),
        *_image_ids_from_structured_json(
            str(item.get("structured_json") or ""),
            include_parent_table=chunk_type == "table_chunk" and not item.get("parent_row_id"),
        ),
    ]:
        image_id = str(image_id)
        if image_id and image_id not in image_ids:
            image_ids.append(image_id)
    return [
        {
            "image_id": image_id,
            "url": f"/api/knowledge/files/{item['file_id']}/images/{image_id}",
            "reason": _related_image_reason(item),
        }
        for image_id in image_ids
        if item.get("file_id")
    ]


def _related_image_reason(item: dict) -> str:
    if item.get("parent_row_id"):
        row_text = item.get("row_text") or "同一表格行"
        return f"该图片属于命中表格行 {item['parent_row_id']}：{row_text}"
    if item.get("parent_inline_id"):
        inline_text = item.get("inline_text") or "同一段落/列表项"
        return f"该图片属于命中段落/列表项 {item['parent_inline_id']}：{inline_text}"
    return "该图片属于命中的图片知识片段"


def _image_ids_from_structured_json(value: str, *, include_parent_table: bool) -> list[str]:
    """Extract image references from stored table markdown metadata."""
    if not value:
        return []
    try:
        structured = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(structured, dict):
        return []
    keys = ["row_markdown", "row_text", "table_cell_context"]
    if include_parent_table:
        keys.extend(["parent_table_markdown", "markdown_table"])
    return _image_ids_from_text(
        "\n".join(
            str(structured.get(key) or "")
            for key in keys
        )
    )


def _image_ids_from_text(text: str) -> list[str]:
    """Find image IDs embedded in table markdown, for example 图片:image-78."""
    seen: set[str] = set()
    image_ids: list[str] = []
    for match in re.finditer(r"(?:图片|鍥剧墖|image)\s*[:：]\s*([A-Za-z0-9_-]+)", text, flags=re.IGNORECASE):
        image_id = match.group(1)
        if image_id in seen:
            continue
        seen.add(image_id)
        image_ids.append(image_id)
    return image_ids


def _json_list(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _file_filter(file_ids: set[str] | None) -> dict | None:
    if not file_ids:
        return None
    ordered_ids = sorted(file_ids)
    if len(ordered_ids) == 1:
        return {"file_id": ordered_ids[0]}
    return {"file_id": {"$in": ordered_ids}}


def _distance_to_score(distance: float) -> float:
    return 1.0 / (1.0 + max(distance, 0.0))


def _processed_query_from_payload(payload: dict) -> ProcessedQuery:
    """Rehydrate query features stored in LangGraph state."""
    primary_queries = [_query_variant_from_payload(item, default_role="primary") for item in payload.get("primary_queries", [])]
    expansion_queries = [_query_variant_from_payload(item, default_role="expansion") for item in payload.get("expansion_queries", [])]
    if not primary_queries:
        primary_queries = [
            QueryVariant(text=str(item), role="variant", weight=1.0)
            for item in payload.get("variants", [])
            if str(item)
        ]
    return ProcessedQuery(
        original=str(payload.get("original", "")),
        normalized=str(payload.get("normalized", "")),
        variants=[str(item) for item in payload.get("variants", []) if str(item)],
        tokens=[str(item) for item in payload.get("tokens", []) if str(item)],
        primary_queries=primary_queries,
        expansion_queries=expansion_queries,
        rewritten_query=str(payload.get("rewritten_query", "")),
        sub_queries=[str(item) for item in payload.get("sub_queries", []) if str(item)],
        hyde_document=str(payload.get("hyde_document", "")),
        strategy=str(payload.get("strategy", "light")),
        llm_error=str(payload.get("llm_error", "")),
    )


def _query_variant_payload(query: QueryVariant) -> dict:
    """Serialize one weighted query variant for LangGraph checkpoints."""
    return {
        "text": query.text,
        "role": query.role,
        "weight": query.weight,
    }


def _query_variant_from_payload(payload: object, *, default_role: str) -> QueryVariant:
    """Rehydrate one weighted query variant from graph state."""
    if not isinstance(payload, dict):
        return QueryVariant(text=str(payload), role=default_role, weight=1.0)
    try:
        weight = float(payload.get("weight", 1.0) or 1.0)
    except (TypeError, ValueError):
        weight = 1.0
    return QueryVariant(
        text=str(payload.get("text", "")),
        role=str(payload.get("role", default_role)),
        weight=weight,
    )


def _candidate_payload(candidate: RetrievalCandidate) -> dict:
    """Serialize a retrieval candidate so checkpoint storage can persist it."""
    return {
        "document": {
            "page_content": candidate.document.page_content,
            "metadata": dict(candidate.document.metadata),
        },
        "score": candidate.score,
        "sources": sorted(candidate.sources),
        "rerank_score": candidate.rerank_score,
    }


def _candidate_from_payload(payload: dict) -> RetrievalCandidate:
    """Rehydrate a retrieval candidate from LangGraph state."""
    document_payload = payload.get("document") or {}
    return RetrievalCandidate(
        document=Document(
            page_content=str(document_payload.get("page_content", "")),
            metadata=dict(document_payload.get("metadata") or {}),
        ),
        score=float(payload.get("score", 0) or 0),
        sources={str(item) for item in payload.get("sources", []) if str(item)},
        rerank_score=(
            float(payload["rerank_score"])
            if payload.get("rerank_score") not in (None, "")
            else None
        ),
    )
