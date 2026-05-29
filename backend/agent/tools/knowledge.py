from langchain_core.tools import tool

from agent.tools.schemas import KnowledgeSearchInput
from services.knowledge_base import search_documents


@tool(args_schema=KnowledgeSearchInput)
def search_docs(query: str, top_k: int = 5) -> str:
    """Search uploaded DOCX knowledge-base text, tables, image OCR, image descriptions, and source paths."""
    return search_documents(query, top_k=top_k)
