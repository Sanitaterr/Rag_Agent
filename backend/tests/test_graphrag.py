from __future__ import annotations

from pathlib import Path

from services.graphrag.formatting import graph_result_to_rag_payload
from services.graphrag.ingestion import GraphPayload, payload_from_alarm_catalog
from services.graphrag.retrieval import GraphRetriever
from services.graphrag.service import _corpus_image_candidates
from services.graphrag.neo4j_store import _visualization_payload
from services.rag.service import _merge_graph_and_rag_results


CORPUS_DIR = Path(__file__).resolve().parents[2] / "industrial_graphrag_corpus"


def test_alarm_catalog_builds_alarm_graph_payload() -> None:
    payload = payload_from_alarm_catalog(CORPUS_DIR / "metadata" / "alarm_catalog.csv")

    alarm = _node(payload, "Alarm", "P203-A004")
    assert alarm.properties["name"] == "泵体振动高"
    assert alarm.properties["severity"] == "高"
    assert _has_rel(payload, "Alarm", "P203-A004", "TRIGGERED_BY", "Parameter", "P203_VIB")
    assert _has_rel(payload, "TableRow", "DOC-P203-TALARM-R004", "HAS_IMAGE", "Image", "IMG-DOC-P203-VIB")
    assert _has_rel(payload, "Alarm", "P203-A004", "HAS_ACTION", "Action")


def test_graph_payload_merges_duplicate_nodes_and_relationships() -> None:
    payload = GraphPayload()

    payload.add_node("Alarm", "P203-A004", name="first")
    payload.add_node("Alarm", "P203-A004", severity="高")
    payload.add_relationship("Alarm", "P203-A004", "TRIGGERED_BY", "Parameter", "P203_VIB")
    payload.add_relationship("Alarm", "P203-A004", "TRIGGERED_BY", "Parameter", "P203_VIB")

    assert len(payload.nodes) == 1
    assert payload.nodes[0].properties["name"] == "first"
    assert payload.nodes[0].properties["severity"] == "高"
    assert len(payload.relationships) == 1


def test_graph_retriever_queries_alarm_code_before_full_question() -> None:
    store = _FakeStore()
    retriever = GraphRetriever(store)  # type: ignore[arg-type]

    results = retriever.search("P203-A004 是什么原因", limit=3)

    assert store.queries[0] == "P203-A004 是什么原因"
    assert store.queries[1] == "P203-A004"
    assert results[0]["alarm_code"] == "P203-A004"
    assert results[0]["tag"] == "P203_VIB"


def test_graph_result_payload_can_join_rag_results() -> None:
    graph_payload = graph_result_to_rag_payload(
        {
            "retrieval": "graph",
            "score": 0.95,
            "alarm_code": "P203-A004",
            "alarm_name": "泵体振动高",
            "severity": "高",
            "tag": "P203_VIB",
            "causes": ["吸入压力低引起汽蚀"],
            "actions": ["先排除 P203-A001 是否同时存在"],
            "row_ids": ["DOC-P203-TALARM-R004"],
            "image_ids": ["IMG-DOC-P203-VIB"],
            "doc_id": "DOC-P203",
            "chunk_id": "DOC-P203-CH-004",
        }
    )
    chroma_payload = {
        "retrieval": "chroma",
        "score": 0.82,
        "chunk_id": "chunk-1",
        "text": "vector evidence",
    }

    merged = _merge_graph_and_rag_results([graph_payload], [chroma_payload], 5)

    assert merged[0]["retrieval"] == "graph"
    assert "P203-A004" in merged[0]["text"]
    assert merged[0]["related_images"][0]["image_id"] == "IMG-DOC-P203-VIB"
    assert merged[1] == chroma_payload


def test_corpus_graph_image_id_falls_back_to_alarm_logic_asset() -> None:
    candidates = _corpus_image_candidates(
        CORPUS_DIR / "assets" / "images",
        "DOC-P203",
        "IMG-DOC-P203-SUCP",
    )

    assert candidates[0].name == "IMG-DOC-P203-SUCP.png"
    assert any(path.name == "DOC-P203_alarm_logic.png" for path in candidates)
    assert next(path for path in candidates if path.name == "DOC-P203_alarm_logic.png").exists()


def test_visualization_payload_contains_nodes_links_and_categories() -> None:
    payload = _visualization_payload(
        [
            {
                "a": {"code": "P203-A004", "name": "泵体振动高"},
                "device": {"name": "P203A/B 高压给水离心泵"},
                "system": {"name": "高压给水泵系统"},
                "area": {"name": "动力站二层泵区"},
                "parameter": {"tag": "P203_VIB"},
                "causes": [{"id": "cause-1", "text": "吸入压力低引起汽蚀"}],
                "actions": [{"id": "action-1", "text": "先排除 P203-A001 是否同时存在"}],
                "resets": [],
                "rows": [{"row_id": "DOC-P203-TALARM-R004"}],
                "images": [{"image_id": "IMG-DOC-P203-VIB"}],
            }
        ],
        enabled=True,
        error="",
    )

    assert {node["id"] for node in payload["nodes"]} >= {
        "Alarm:P203-A004",
        "Device:P203A/B 高压给水离心泵",
        "Parameter:P203_VIB",
    }
    assert {"source": "Alarm:P203-A004", "target": "Parameter:P203_VIB", "label": "TRIGGERED_BY"} in payload["links"]
    assert any(category["name"] == "Alarm" for category in payload["categories"])


class _FakeStore:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, query: str, limit: int = 5) -> list[dict]:
        self.queries.append(query)
        if query != "P203-A004":
            return []
        return [
            {
                "retrieval": "graph",
                "score": 0.94,
                "alarm_code": "P203-A004",
                "tag": "P203_VIB",
            }
        ]


def _node(payload: GraphPayload, label: str, value: str):
    return next(node for node in payload.nodes if node.label == label and node.value == value)


def _has_rel(
    payload: GraphPayload,
    start_label: str,
    start_value: str,
    rel_type: str,
    end_label: str,
    end_value: str | None = None,
) -> bool:
    return any(
        rel.start_label == start_label
        and rel.start_value == start_value
        and rel.rel_type == rel_type
        and rel.end_label == end_label
        and (end_value is None or rel.end_value == end_value)
        for rel in payload.relationships
    )
