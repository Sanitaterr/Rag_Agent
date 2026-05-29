from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from services.rag.models import DocChunk, KnowledgeDocument


NODE_KEYS = {
    "Document": "doc_id",
    "Chunk": "chunk_id",
    "TableRow": "row_id",
    "Image": "image_id",
    "System": "name",
    "Device": "name",
    "Alarm": "code",
    "Parameter": "tag",
    "Cause": "id",
    "Action": "id",
    "Area": "name",
    "ResetCondition": "id",
}

ALARM_CODE_RE = re.compile(r"\b[A-Z]{1,8}\d{2,5}-A\d{3}\b")
TAG_RE = re.compile(r"\b[A-Z]{1,8}\d{2,5}_[A-Z0-9_]{2,}\b")


@dataclass
class GraphNode:
    """A deterministic graph node payload."""

    label: str
    key: str
    value: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphRelationship:
    """A deterministic graph relationship payload."""

    start_label: str
    start_key: str
    start_value: str
    rel_type: str
    end_label: str
    end_key: str
    end_value: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphPayload:
    """Nodes and relationships ready for Neo4j MERGE operations."""

    nodes: list[GraphNode] = field(default_factory=list)
    relationships: list[GraphRelationship] = field(default_factory=list)

    def add_node(self, label: str, value: str, **properties: Any) -> None:
        value = str(value or "").strip()
        if not value:
            return
        key = NODE_KEYS[label]
        props = {key: value, **{name: item for name, item in properties.items() if item not in (None, "")}}
        identity = (label, key, value)
        for node in self.nodes:
            if (node.label, node.key, node.value) == identity:
                node.properties.update(props)
                return
        self.nodes.append(GraphNode(label=label, key=key, value=value, properties=props))

    def add_relationship(
        self,
        start_label: str,
        start_value: str,
        rel_type: str,
        end_label: str,
        end_value: str,
        **properties: Any,
    ) -> None:
        start_value = str(start_value or "").strip()
        end_value = str(end_value or "").strip()
        if not start_value or not end_value:
            return
        rel = GraphRelationship(
            start_label=start_label,
            start_key=NODE_KEYS[start_label],
            start_value=start_value,
            rel_type=rel_type,
            end_label=end_label,
            end_key=NODE_KEYS[end_label],
            end_value=end_value,
            properties={name: item for name, item in properties.items() if item not in (None, "")},
        )
        identity = (
            rel.start_label,
            rel.start_key,
            rel.start_value,
            rel.rel_type,
            rel.end_label,
            rel.end_key,
            rel.end_value,
        )
        if any(
            (
                item.start_label,
                item.start_key,
                item.start_value,
                item.rel_type,
                item.end_label,
                item.end_key,
                item.end_value,
            )
            == identity
            for item in self.relationships
        ):
            return
        self.relationships.append(rel)

    def extend(self, other: GraphPayload) -> None:
        for node in other.nodes:
            self.add_node(node.label, node.value, **node.properties)
        for rel in other.relationships:
            self.add_relationship(
                rel.start_label,
                rel.start_value,
                rel.rel_type,
                rel.end_label,
                rel.end_value,
                **rel.properties,
            )


def corpus_payload(corpus_dir: Path) -> GraphPayload:
    """Load the seed corpus CSV, JSONL, and Markdown files into one payload."""
    payload = GraphPayload()
    metadata_dir = corpus_dir / "metadata"
    alarm_csv = metadata_dir / "alarm_catalog.csv"
    entities_jsonl = metadata_dir / "entities_relations.jsonl"
    markdown_dir = corpus_dir / "markdown"
    if alarm_csv.exists():
        payload.extend(payload_from_alarm_catalog(alarm_csv))
    if entities_jsonl.exists():
        payload.extend(payload_from_entities_jsonl(entities_jsonl))
    if markdown_dir.exists():
        for path in sorted(markdown_dir.glob("*.md")):
            payload.extend(payload_from_markdown(path))
    return payload


def payload_from_alarm_catalog(path: Path) -> GraphPayload:
    """Build graph payload from the structured alarm catalog CSV."""
    payload = GraphPayload()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            _add_alarm_row(payload, row)
    return payload


def payload_from_entities_jsonl(path: Path) -> GraphPayload:
    """Build graph payload from the provided entity/relation seed file."""
    payload = GraphPayload()
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            for entity in item.get("entities") or []:
                _add_seed_entity(payload, entity, item)
            for rel in item.get("relations") or []:
                _add_seed_relationship(payload, rel)
    return payload


def payload_from_markdown(path: Path) -> GraphPayload:
    """Build lightweight document and suggested-triple nodes from Markdown."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    payload = GraphPayload()
    doc_id = _first_match(r"文档编号：([A-Za-z0-9_-]+)", text) or path.stem.split("_", 1)[0]
    title = _first_heading(text) or path.stem
    system = _first_match(r"系统：(.+)", text)
    device = _first_match(r"设备：(.+)", text)
    area = _first_match(r"区域：(.+)", text)
    payload.add_node("Document", doc_id, name=title, source_doc=path.name)
    if system:
        payload.add_node("System", system)
        payload.add_relationship("Document", doc_id, "IN_SYSTEM", "System", system)
    if device:
        payload.add_node("Device", device, doc_id=doc_id)
        payload.add_relationship("Device", device, "IN_SYSTEM", "System", system) if system else None
    if area:
        payload.add_node("Area", area)
        payload.add_relationship("Device", device, "LOCATED_IN", "Area", area) if device else None
    return payload


def payload_from_document(document: KnowledgeDocument) -> GraphPayload:
    """Build a deterministic graph payload from one parsed DOCX document."""
    payload = GraphPayload()
    payload.add_node("Document", document.id, name=document.name, file_id=document.id, source_doc=document.name)
    for image in document.images:
        payload.add_node(
            "Image",
            image.id,
            file_id=document.id,
            filename=image.filename,
            source_path=image.source_path,
            size=image.size,
        )
        payload.add_relationship("Document", document.id, "CONTAINS", "Image", image.id)
    for chunk in document.chunks:
        _add_chunk(payload, document, chunk)
    return payload


def _add_alarm_row(payload: GraphPayload, row: dict[str, str]) -> None:
    doc_id = row.get("doc_id", "")
    chunk_id = row.get("chunk_id", "")
    row_id = row.get("row_id", "")
    image_id = row.get("image_id", "")
    alarm_code = row.get("alarm_code", "")
    system = row.get("system", "")
    device = row.get("device", "")
    area = row.get("area", "")
    tag = row.get("tag", "")

    payload.add_node("Document", doc_id, doc_id=doc_id, source_doc=row.get("source_doc", ""))
    payload.add_node("System", system)
    payload.add_node("Device", device, system=system, area=area, doc_id=doc_id)
    payload.add_node("Area", area)
    payload.add_node(
        "Alarm",
        alarm_code,
        name=row.get("alarm_name", ""),
        severity=row.get("severity", ""),
        trigger=row.get("trigger", ""),
        reset_condition=row.get("reset_condition", ""),
        doc_id=doc_id,
        chunk_id=chunk_id,
        row_id=row_id,
        image_id=image_id,
    )
    payload.add_node("Parameter", tag, doc_id=doc_id)
    payload.add_node("Chunk", chunk_id, doc_id=doc_id, source_doc=row.get("source_doc", ""))
    payload.add_node("TableRow", row_id, doc_id=doc_id, table_id=row.get("table_id", ""), chunk_id=chunk_id)
    payload.add_node("Image", image_id, doc_id=doc_id, file_id=doc_id)

    payload.add_relationship("Document", doc_id, "CONTAINS", "Chunk", chunk_id)
    payload.add_relationship("Chunk", chunk_id, "MENTIONS", "Alarm", alarm_code)
    payload.add_relationship("TableRow", row_id, "MENTIONS", "Alarm", alarm_code)
    payload.add_relationship("TableRow", row_id, "HAS_IMAGE", "Image", image_id)
    payload.add_relationship("Document", doc_id, "IN_SYSTEM", "System", system)
    payload.add_relationship("Device", device, "IN_SYSTEM", "System", system)
    payload.add_relationship("Device", device, "LOCATED_IN", "Area", area)
    payload.add_relationship("Alarm", alarm_code, "BELONGS_TO", "Device", device)
    payload.add_relationship("Alarm", alarm_code, "TRIGGERED_BY", "Parameter", tag)

    for cause in _split_items(row.get("possible_causes", "")):
        cause_id = _stable_id("cause", cause)
        payload.add_node("Cause", cause_id, text=cause, doc_id=doc_id)
        payload.add_relationship("Alarm", alarm_code, "HAS_CAUSE", "Cause", cause_id)
    for action in _split_items(row.get("actions", "")):
        action_id = _stable_id("action", action)
        payload.add_node("Action", action_id, text=action, doc_id=doc_id)
        payload.add_relationship("Alarm", alarm_code, "HAS_ACTION", "Action", action_id)
    reset_condition = row.get("reset_condition", "")
    if reset_condition:
        reset_id = _stable_id("reset", f"{alarm_code}:{reset_condition}")
        payload.add_node("ResetCondition", reset_id, text=reset_condition, doc_id=doc_id)
        payload.add_relationship("Alarm", alarm_code, "HAS_RESET_CONDITION", "ResetCondition", reset_id)


def _add_seed_entity(payload: GraphPayload, entity: dict[str, Any], item: dict[str, Any]) -> None:
    label = str(entity.get("type") or "")
    if label not in NODE_KEYS:
        return
    if label == "Alarm":
        value = str(entity.get("code") or entity.get("id") or "")
    elif label == "Parameter":
        value = str(entity.get("tag") or entity.get("id") or "")
    elif label in {"Cause", "Action"}:
        text = str(entity.get("text") or entity.get("id") or "")
        value = _stable_id(label.lower(), text)
        entity = {**entity, "id": value, "text": text}
    elif label == "Image":
        value = str(entity.get("image_id") or entity.get("id") or "")
    elif label == "Document":
        value = str(entity.get("id") or item.get("doc_id") or "")
    else:
        value = str(entity.get("name") or entity.get("id") or "")
    payload.add_node(label, value, doc_id=item.get("doc_id", ""), **{k: v for k, v in entity.items() if k != "type"})


def _add_seed_relationship(payload: GraphPayload, rel: dict[str, Any]) -> None:
    start_label, start_value = _parse_endpoint(str(rel.get("start") or ""))
    end_label, end_value = _parse_endpoint(str(rel.get("end") or ""))
    rel_type = str(rel.get("type") or "")
    if not start_label or not end_label or rel_type not in _allowed_relationships():
        return
    payload.add_relationship(start_label, start_value, rel_type, end_label, end_value)


def _add_chunk(payload: GraphPayload, document: KnowledgeDocument, chunk: DocChunk) -> None:
    text = chunk.full_text or chunk.text
    payload.add_node(
        "Chunk",
        chunk.id,
        doc_id=document.id,
        file_id=document.id,
        file_name=document.name,
        chunk_type=chunk.chunk_type,
        section_title=chunk.section_title,
        text=_limit_text(text),
    )
    payload.add_relationship("Document", document.id, "CONTAINS", "Chunk", chunk.id)
    for image_id in [*chunk.images, *chunk.row_image_ids, *chunk.inline_image_ids]:
        payload.add_node("Image", image_id, doc_id=document.id, file_id=document.id)
        payload.add_relationship("Chunk", chunk.id, "HAS_IMAGE", "Image", image_id)
    if chunk.parent_row_id:
        payload.add_node("TableRow", chunk.parent_row_id, doc_id=document.id, chunk_id=chunk.id, row_text=chunk.row_text)
        payload.add_relationship("Chunk", chunk.id, "CONTAINS", "TableRow", chunk.parent_row_id)
        for image_id in chunk.row_image_ids:
            payload.add_relationship("TableRow", chunk.parent_row_id, "HAS_IMAGE", "Image", image_id)

    alarm_codes = sorted(set(ALARM_CODE_RE.findall(text)))
    tags = sorted(set(TAG_RE.findall(text)))
    for code in alarm_codes:
        payload.add_node("Alarm", code, doc_id=document.id, chunk_id=chunk.id)
        payload.add_relationship("Chunk", chunk.id, "MENTIONS", "Alarm", code)
        if chunk.parent_row_id:
            payload.add_relationship("TableRow", chunk.parent_row_id, "MENTIONS", "Alarm", code)
        for tag in tags:
            payload.add_node("Parameter", tag, doc_id=document.id)
            payload.add_relationship("Alarm", code, "TRIGGERED_BY", "Parameter", tag)
        for cause in _section_items(text, "possible_causes"):
            cause_id = _stable_id("cause", cause)
            payload.add_node("Cause", cause_id, text=cause, doc_id=document.id)
            payload.add_relationship("Alarm", code, "HAS_CAUSE", "Cause", cause_id)
        for action in _section_items(text, "actions"):
            action_id = _stable_id("action", action)
            payload.add_node("Action", action_id, text=action, doc_id=document.id)
            payload.add_relationship("Alarm", code, "HAS_ACTION", "Action", action_id)


def _parse_endpoint(value: str) -> tuple[str, str]:
    if ":" not in value:
        return "", ""
    label, raw = value.split(":", 1)
    label = label.strip()
    raw = raw.strip()
    if label in {"Cause", "Action"}:
        return label, _stable_id(label.lower(), raw)
    return (label, raw) if label in NODE_KEYS else ("", "")


def _allowed_relationships() -> set[str]:
    return {
        "CONTAINS",
        "MENTIONS",
        "HAS_IMAGE",
        "BELONGS_TO",
        "IN_SYSTEM",
        "LOCATED_IN",
        "TRIGGERED_BY",
        "HAS_CAUSE",
        "HAS_ACTION",
        "HAS_RESET_CONDITION",
    }


def _split_items(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[;；]\s*", str(value or "")) if item.strip()]


def _section_items(text: str, section: str) -> list[str]:
    labels = {
        "possible_causes": [r"可能原因[:：]?", r"\*\*可能原因[:：]?\*\*"],
        "actions": [r"标准操作步骤[:：]?", r"\*\*标准操作步骤[:：]?\*\*"],
    }
    starts = labels.get(section, [])
    for label in starts:
        match = re.search(label + r"\s*(.+?)(?:\n\s*\*\*|\n\s*恢复条件|\Z)", text, flags=re.S)
        if match:
            return [
                re.sub(r"^\s*(?:[-*]|\d+[.)、])\s*", "", line).strip()
                for line in match.group(1).splitlines()
                if re.sub(r"^\s*(?:[-*]|\d+[.)、])\s*", "", line).strip()
            ]
    return []


def _first_match(pattern: str, text: str) -> str:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else ""


def _first_heading(text: str) -> str:
    match = re.search(r"^#\s+(.+)$", text, flags=re.M)
    return match.group(1).strip() if match else ""


def _stable_id(prefix: str, text: str) -> str:
    digest = hashlib.sha1(str(text).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _limit_text(text: str, limit: int = 2000) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else f"{text[:limit]}\n..."
