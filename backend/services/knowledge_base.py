"""Compatibility exports for the modular RAG service package."""

from services.rag import KnowledgeBaseService, knowledge_base, search_documents

__all__ = ["KnowledgeBaseService", "knowledge_base", "search_documents"]
