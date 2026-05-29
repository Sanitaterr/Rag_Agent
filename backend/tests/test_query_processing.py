from __future__ import annotations

from services.rag.query_processing import preprocess_query


def test_clear_structured_query_keeps_light_strategy() -> None:
    query = "MACS V6.5系统组成"

    processed = preprocess_query(query)

    assert processed.strategy == "light"
    assert processed.expansion_queries == []
    assert processed.vector_queries[0].text == query
    assert all(item.weight == 1.0 for item in processed.primary_queries)


def test_colloquial_troubleshooting_query_gets_safe_expansion() -> None:
    query = "这个炉子温度老往上跑咋办？"

    processed = preprocess_query(query)

    assert processed.strategy == "expanded"
    assert processed.vector_queries[0].text == query
    assert any(item.role == "rewrite" for item in processed.expansion_queries)
    assert any(item.role == "hyde" for item in processed.expansion_queries)
    assert all(item.weight <= 1.0 for item in processed.expansion_queries)
    assert any("排查" in item.text or "处置" in item.text for item in processed.expansion_queries)
