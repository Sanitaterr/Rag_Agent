from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from config.settings import Settings
from services.graphrag.ingestion import GraphPayload


SAFE_LABELS = {
    "Document",
    "Chunk",
    "TableRow",
    "Image",
    "System",
    "Device",
    "Alarm",
    "Parameter",
    "Cause",
    "Action",
    "Area",
    "ResetCondition",
}
SAFE_REL_TYPES = {
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


class Neo4jGraphStore:
    """Thin Neo4j adapter with parameterized writes and read queries."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._driver = None
        self.error = ""

    @property
    def enabled(self) -> bool:
        return bool(self._settings.graph_rag_enabled)

    def available(self) -> bool:
        if not self.enabled:
            self.error = "GraphRAG is disabled."
            return False
        if not self._settings.neo4j_password:
            self.error = "NEO4J_PASSWORD is not configured."
            return False
        try:
            self._ensure_driver().verify_connectivity()
            self.error = ""
            return True
        except Exception as exc:
            self.error = f"Neo4j unavailable: {exc}"
            return False

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def initialize_schema(self) -> None:
        """Create constraints and indexes used by the GraphRAG schema."""
        if not self.available():
            raise RuntimeError(self.error)
        statements = [
            "CREATE CONSTRAINT document_id IF NOT EXISTS FOR (d:Document) REQUIRE d.doc_id IS UNIQUE",
            "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (c:Chunk) REQUIRE c.chunk_id IS UNIQUE",
            "CREATE CONSTRAINT row_id IF NOT EXISTS FOR (r:TableRow) REQUIRE r.row_id IS UNIQUE",
            "CREATE CONSTRAINT image_id IF NOT EXISTS FOR (i:Image) REQUIRE i.image_id IS UNIQUE",
            "CREATE CONSTRAINT alarm_code IF NOT EXISTS FOR (a:Alarm) REQUIRE a.code IS UNIQUE",
            "CREATE CONSTRAINT parameter_tag IF NOT EXISTS FOR (p:Parameter) REQUIRE p.tag IS UNIQUE",
            "CREATE INDEX system_name IF NOT EXISTS FOR (s:System) ON (s.name)",
            "CREATE INDEX device_name IF NOT EXISTS FOR (d:Device) ON (d.name)",
            "CREATE INDEX area_name IF NOT EXISTS FOR (a:Area) ON (a.name)",
        ]
        with self._session() as session:
            for statement in statements:
                session.run(statement)

    def upsert_payload(self, payload: GraphPayload) -> dict[str, int]:
        """MERGE graph nodes and relationships into Neo4j."""
        if not payload.nodes and not payload.relationships:
            return {"nodes": 0, "relationships": 0}
        self.initialize_schema()
        with self._session() as session:
            for node in payload.nodes:
                label = _safe_label(node.label)
                query = f"MERGE (n:{label} {{{node.key}: $value}}) SET n += $props"
                session.run(query, value=node.value, props=node.properties)
            for rel in payload.relationships:
                start_label = _safe_label(rel.start_label)
                end_label = _safe_label(rel.end_label)
                rel_type = _safe_rel_type(rel.rel_type)
                query = (
                    f"MATCH (a:{start_label} {{{rel.start_key}: $start_value}}) "
                    f"MATCH (b:{end_label} {{{rel.end_key}: $end_value}}) "
                    f"MERGE (a)-[r:{rel_type}]->(b) SET r += $props"
                )
                session.run(query, start_value=rel.start_value, end_value=rel.end_value, props=rel.properties)
        return {"nodes": len(payload.nodes), "relationships": len(payload.relationships)}

    def delete_document(self, doc_id: str) -> None:
        """Delete document-scoped graph data while preserving shared vocabulary nodes."""
        if not self.available():
            return
        with self._session() as session:
            session.run(
                """
                MATCH (n)
                WHERE n.doc_id = $doc_id OR n.file_id = $doc_id
                DETACH DELETE n
                """,
                doc_id=doc_id,
            )

    def stats(self) -> dict[str, Any]:
        """Return compact graph store statistics."""
        if not self.available():
            return {
                "graph_enabled": self.enabled,
                "graph_indexed_files": 0,
                "graph_nodes": 0,
                "graph_relationships": 0,
                "graph_error": self.error,
            }
        with self._session() as session:
            counts = session.run(
                """
                MATCH (n)
                WITH count(n) AS nodes
                OPTIONAL MATCH ()-[r]->()
                WITH nodes, count(r) AS relationships
                OPTIONAL MATCH (d:Document)
                RETURN nodes, relationships, count(DISTINCT d.doc_id) AS indexed_files
                """
            ).single()
        return {
            "graph_enabled": self.enabled,
            "graph_indexed_files": int(counts["indexed_files"] if counts else 0),
            "graph_nodes": int(counts["nodes"] if counts else 0),
            "graph_relationships": int(counts["relationships"] if counts else 0),
            "graph_error": "",
        }

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Read graph evidence for alarms, devices, parameters, and severity text."""
        if not query.strip() or not self.available():
            return []
        with self._session() as session:
            records = session.run(_search_cypher(), q=query, limit=max(1, min(limit, 10))).data()
        return [_record_payload(record) for record in records]

    def visualization(self, limit: int = 120) -> dict[str, Any]:
        """Return graph nodes and links for frontend visualization."""
        safe_limit = max(10, min(limit, 300))
        if not self.available():
            return _empty_visualization(self.error, self.enabled)
        with self._session() as session:
            rows = session.run(_visualization_cypher(), limit=safe_limit).data()
        return _visualization_payload(rows, self.enabled, self.error)

    def _ensure_driver(self):
        if self._driver is None:
            from neo4j import GraphDatabase

            self._driver = GraphDatabase.driver(
                self._settings.neo4j_uri,
                auth=(self._settings.neo4j_user, self._settings.neo4j_password),
            )
        return self._driver

    @contextmanager
    def _session(self) -> Iterator[Any]:
        session = self._ensure_driver().session(database=self._settings.neo4j_database)
        try:
            yield session
        finally:
            session.close()


def _search_cypher() -> str:
    return """
    WITH toLower($q) AS q
    MATCH (a:Alarm)
    WHERE toLower(a.code) CONTAINS q
       OR q CONTAINS toLower(a.code)
       OR toLower(coalesce(a.name, '')) CONTAINS q
       OR q CONTAINS toLower(coalesce(a.name, ''))
       OR toLower(coalesce(a.severity, '')) CONTAINS q
       OR (size(coalesce(a.severity, '')) > 1 AND q CONTAINS toLower(a.severity))
       OR EXISTS {
          MATCH (a)-[:BELONGS_TO]->(device:Device)
          WHERE toLower(device.name) CONTAINS q OR q CONTAINS toLower(device.name)
       }
       OR EXISTS {
          MATCH (a)-[:BELONGS_TO]->(:Device)-[:IN_SYSTEM]->(system:System)
          WHERE toLower(system.name) CONTAINS q OR q CONTAINS toLower(system.name)
       }
       OR EXISTS {
          MATCH (a)-[:TRIGGERED_BY]->(parameter:Parameter)
          WHERE toLower(parameter.tag) CONTAINS q OR q CONTAINS toLower(parameter.tag)
       }
    OPTIONAL MATCH (a)-[:BELONGS_TO]->(device:Device)
    OPTIONAL MATCH (device)-[:IN_SYSTEM]->(system:System)
    OPTIONAL MATCH (device)-[:LOCATED_IN]->(area:Area)
    OPTIONAL MATCH (a)-[:TRIGGERED_BY]->(parameter:Parameter)
    OPTIONAL MATCH (a)-[:HAS_CAUSE]->(cause:Cause)
    OPTIONAL MATCH (a)-[:HAS_ACTION]->(action:Action)
    OPTIONAL MATCH (a)-[:HAS_RESET_CONDITION]->(reset:ResetCondition)
    OPTIONAL MATCH (row:TableRow)-[:MENTIONS]->(a)
    OPTIONAL MATCH (row)-[:HAS_IMAGE]->(image:Image)
    OPTIONAL MATCH (chunk:Chunk)-[:MENTIONS]->(a)
    OPTIONAL MATCH (doc:Document)-[:CONTAINS]->(chunk)
    WITH a, device, system, area, parameter, reset,
         collect(DISTINCT cause.text) AS causes,
         collect(DISTINCT action.text) AS actions,
         collect(DISTINCT row.row_id) AS row_ids,
         collect(DISTINCT image.image_id) AS image_ids,
         collect(DISTINCT chunk.chunk_id) AS chunk_ids,
         collect(DISTINCT doc.doc_id) AS doc_ids,
         CASE
            WHEN toLower(a.code) = q THEN 1.0
            WHEN toLower(a.code) CONTAINS q THEN 0.94
            WHEN parameter IS NOT NULL AND toLower(parameter.tag) CONTAINS q THEN 0.88
            WHEN device IS NOT NULL AND toLower(device.name) CONTAINS q THEN 0.82
            WHEN size(coalesce(a.severity, '')) > 1 AND q CONTAINS toLower(a.severity) THEN 0.78
            ELSE 0.68
         END AS score
    RETURN a.code AS alarm_code,
           a.name AS alarm_name,
           a.severity AS severity,
           a.trigger AS trigger,
           a.doc_id AS doc_id,
           a.chunk_id AS chunk_id,
           device.name AS device,
           system.name AS system,
           area.name AS area,
           parameter.tag AS tag,
           reset.text AS reset_condition,
           causes,
           actions,
           row_ids,
           image_ids,
           chunk_ids,
           doc_ids,
           score
    ORDER BY score DESC, alarm_code ASC
    LIMIT $limit
    """


def _visualization_cypher() -> str:
    return """
    MATCH (a:Alarm)
    WITH a
    ORDER BY coalesce(a.doc_id, ''), a.code
    LIMIT $limit
    OPTIONAL MATCH (a)-[:BELONGS_TO]->(device:Device)
    OPTIONAL MATCH (device)-[:IN_SYSTEM]->(system:System)
    OPTIONAL MATCH (device)-[:LOCATED_IN]->(area:Area)
    OPTIONAL MATCH (a)-[:TRIGGERED_BY]->(parameter:Parameter)
    OPTIONAL MATCH (a)-[:HAS_CAUSE]->(cause:Cause)
    OPTIONAL MATCH (a)-[:HAS_ACTION]->(action:Action)
    OPTIONAL MATCH (a)-[:HAS_RESET_CONDITION]->(reset:ResetCondition)
    OPTIONAL MATCH (row:TableRow)-[:MENTIONS]->(a)
    OPTIONAL MATCH (row)-[:HAS_IMAGE]->(image:Image)
    RETURN a, device, system, area, parameter,
           collect(DISTINCT cause) AS causes,
           collect(DISTINCT action) AS actions,
           collect(DISTINCT reset) AS resets,
           collect(DISTINCT row) AS rows,
           collect(DISTINCT image) AS images
    """


def _record_payload(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "retrieval": "graph",
        "score": round(float(record.get("score") or 0), 3),
        "alarm_code": record.get("alarm_code") or "",
        "alarm_name": record.get("alarm_name") or "",
        "severity": record.get("severity") or "",
        "trigger": record.get("trigger") or "",
        "doc_id": record.get("doc_id") or _first(record.get("doc_ids")),
        "chunk_id": record.get("chunk_id") or _first(record.get("chunk_ids")),
        "device": record.get("device") or "",
        "system": record.get("system") or "",
        "area": record.get("area") or "",
        "tag": record.get("tag") or "",
        "causes": _clean_list(record.get("causes")),
        "actions": _clean_list(record.get("actions")),
        "reset_condition": record.get("reset_condition") or "",
        "row_ids": _clean_list(record.get("row_ids")),
        "image_ids": _clean_list(record.get("image_ids")),
    }


def _visualization_payload(rows: list[dict[str, Any]], enabled: bool, error: str) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    links: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in rows:
        alarm = row.get("a")
        alarm_id = _add_visual_node(nodes, "Alarm", _node_key(alarm, "Alarm"), _node_name(alarm, "Alarm"))
        for label, field, rel_type in [
            ("Device", "device", "BELONGS_TO"),
            ("Parameter", "parameter", "TRIGGERED_BY"),
        ]:
            target_id = _add_visual_node(nodes, label, _node_key(row.get(field), label), _node_name(row.get(field), label))
            _add_visual_link(links, alarm_id, target_id, rel_type)

        device_id = _visual_id("Device", _node_key(row.get("device"), "Device"))
        for label, field, rel_type in [
            ("System", "system", "IN_SYSTEM"),
            ("Area", "area", "LOCATED_IN"),
        ]:
            target_id = _add_visual_node(nodes, label, _node_key(row.get(field), label), _node_name(row.get(field), label))
            _add_visual_link(links, device_id, target_id, rel_type)

        for label, field, rel_type in [
            ("Cause", "causes", "HAS_CAUSE"),
            ("Action", "actions", "HAS_ACTION"),
            ("ResetCondition", "resets", "HAS_RESET_CONDITION"),
        ]:
            for item in row.get(field) or []:
                target_id = _add_visual_node(nodes, label, _node_key(item, label), _node_name(item, label))
                _add_visual_link(links, alarm_id, target_id, rel_type)
        for item in row.get("rows") or []:
            target_id = _add_visual_node(nodes, "TableRow", _node_key(item, "TableRow"), _node_name(item, "TableRow"))
            _add_visual_link(links, target_id, alarm_id, "MENTIONS")
        for item in row.get("images") or []:
            target_id = _add_visual_node(nodes, "Image", _node_key(item, "Image"), _node_name(item, "Image"))
            _add_visual_link(links, _first_row_id(row), target_id, "HAS_IMAGE")

    return {
        "enabled": enabled,
        "error": error,
        "categories": [{"name": label} for label in _visual_categories()],
        "nodes": [node for node in nodes.values() if node["id"]],
        "links": list(links.values()),
    }


def _empty_visualization(error: str, enabled: bool) -> dict[str, Any]:
    return {"enabled": enabled, "error": error, "categories": [{"name": label} for label in _visual_categories()], "nodes": [], "links": []}


def _visual_categories() -> list[str]:
    return ["Alarm", "Device", "Parameter", "System", "Area", "Cause", "Action", "ResetCondition", "TableRow", "Image"]


def _add_visual_node(nodes: dict[str, dict[str, Any]], label: str, key: str, name: str) -> str:
    if not key:
        return ""
    node_id = _visual_id(label, key)
    if node_id not in nodes:
        nodes[node_id] = {
            "id": node_id,
            "name": name or key,
            "category": label,
            "symbolSize": _symbol_size(label),
            "value": key,
        }
    return node_id


def _add_visual_link(links: dict[tuple[str, str, str], dict[str, str]], source: str, target: str, rel_type: str) -> None:
    if not source or not target:
        return
    links.setdefault((source, target, rel_type), {"source": source, "target": target, "label": rel_type})


def _node_key(node: Any, label: str) -> str:
    if not node:
        return ""
    props = dict(node)
    keys = {
        "Alarm": ["code"],
        "Device": ["name"],
        "Parameter": ["tag"],
        "System": ["name"],
        "Area": ["name"],
        "Cause": ["id", "text"],
        "Action": ["id", "text"],
        "ResetCondition": ["id", "text"],
        "TableRow": ["row_id"],
        "Image": ["image_id"],
    }.get(label, ["id", "name"])
    for key in keys:
        if props.get(key):
            return str(props[key])
    return ""


def _node_name(node: Any, label: str) -> str:
    if not node:
        return ""
    props = dict(node)
    for key in ["name", "code", "tag", "text", "row_id", "image_id"]:
        if props.get(key):
            value = str(props[key])
            return value if len(value) <= 28 else f"{value[:26]}..."
    return _node_key(node, label)


def _first_row_id(row: dict[str, Any]) -> str:
    for item in row.get("rows") or []:
        key = _node_key(item, "TableRow")
        if key:
            return _visual_id("TableRow", key)
    return ""


def _visual_id(label: str, key: str) -> str:
    return f"{label}:{key}" if key else ""


def _symbol_size(label: str) -> int:
    return {
        "Alarm": 42,
        "Device": 38,
        "Parameter": 30,
        "System": 34,
        "Area": 28,
        "Cause": 24,
        "Action": 24,
        "ResetCondition": 24,
        "TableRow": 24,
        "Image": 24,
    }.get(label, 24)


def _safe_label(label: str) -> str:
    if label not in SAFE_LABELS:
        raise ValueError(f"Unsupported Neo4j label: {label}")
    return label


def _safe_rel_type(rel_type: str) -> str:
    if rel_type not in SAFE_REL_TYPES:
        raise ValueError(f"Unsupported Neo4j relationship type: {rel_type}")
    return rel_type


def _clean_list(value: Any) -> list[str]:
    return [str(item) for item in value or [] if item not in (None, "")]


def _first(value: Any) -> str:
    items = _clean_list(value)
    return items[0] if items else ""
