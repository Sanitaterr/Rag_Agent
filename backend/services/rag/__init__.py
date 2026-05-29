"""RAG service package for local DOCX knowledge-base retrieval."""

from services.rag.service import KnowledgeBaseService, knowledge_base, search_documents

__all__ = ["KnowledgeBaseService", "knowledge_base", "search_documents"]
