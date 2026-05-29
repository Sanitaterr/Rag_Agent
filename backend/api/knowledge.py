from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from services.knowledge_base import knowledge_base


router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.get("/files")
async def list_files() -> dict:
    """List uploaded docx files and aggregate knowledge-base stats."""
    return {
        "files": knowledge_base.list_documents(),
        "stats": knowledge_base.stats(),
    }


@router.get("/graph/stats")
async def graph_stats() -> dict:
    """Return Neo4j GraphRAG status and counts."""
    return knowledge_base.graph_stats()


@router.get("/graph/visualization")
async def graph_visualization(limit: int = 120) -> dict:
    """Return Neo4j GraphRAG nodes and links for frontend visualization."""
    return knowledge_base.graph_visualization(limit=limit)


@router.post("/graph/initialize")
async def initialize_graph() -> dict:
    """Initialize Neo4j GraphRAG from the configured seed corpus."""
    try:
        return knowledge_base.initialize_graph_corpus()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/files")
async def upload_file(file: UploadFile = File(...)) -> dict:
    """Upload one docx file into the local knowledge base."""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as temp_file:
            temp_path = Path(temp_file.name)
            while chunk := await file.read(1024 * 1024):
                temp_file.write(chunk)
        document = knowledge_base.save_upload(file.filename or "document.docx", temp_path)
        return {"file": document, "stats": knowledge_base.stats()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if "temp_path" in locals():
            temp_path.unlink(missing_ok=True)


@router.delete("/files/{file_id}")
async def delete_file(file_id: str) -> dict:
    """Delete one docx file from the local knowledge base."""
    try:
        knowledge_base.delete_document(file_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="File not found.") from exc
    return {"files": knowledge_base.list_documents(), "stats": knowledge_base.stats()}


@router.post("/files/{file_id}/vectorize")
async def vectorize_file(file_id: str, background_tasks: BackgroundTasks) -> dict:
    """Start vectorizing one uploaded DOCX into Chroma."""
    try:
        job = knowledge_base.start_vectorization(file_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="File not found.") from exc

    if job.get("new"):
        background_tasks.add_task(knowledge_base.run_vectorization_job, job["id"])
    return {"job": job}


@router.post("/files/{file_id}/graph-index")
async def graph_index_file(file_id: str) -> dict:
    """Rebuild Neo4j graph data for one uploaded DOCX."""
    try:
        return knowledge_base.reindex_graph_document(file_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="File not found.") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/vectorize/jobs/{job_id}")
async def vectorize_job(job_id: str) -> dict:
    """Return vectorization progress for frontend polling."""
    try:
        return {"job": knowledge_base.vectorization_job(job_id)}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Job not found.") from exc


@router.get("/files/{file_id}/preview")
async def preview_file(file_id: str) -> dict:
    """Return text and image metadata for a docx preview."""
    try:
        return knowledge_base.preview_document(file_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="File not found.") from exc


@router.get("/files/{file_id}/images/{image_id}")
async def get_image(file_id: str, image_id: str) -> FileResponse:
    """Serve an extracted image from a docx file."""
    try:
        path = knowledge_base.image_path(file_id, image_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Image not found.") from exc
    return FileResponse(path)
