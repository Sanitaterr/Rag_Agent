from __future__ import annotations

from langchain_core.messages import AIMessage

from agent.runtime.dsml_tool_calls import (
    DsmlStreamFilter,
    normalize_text_tool_calls,
    strip_dsml_from_message,
    strip_dsml_tool_calls,
)
from agent.tools.registry import tools


FULLWIDTH_BAR = "\uff5c"


def _fullwidth_dsml(body: str) -> str:
    return f"<{FULLWIDTH_BAR}{FULLWIDTH_BAR}DSML{FULLWIDTH_BAR}{FULLWIDTH_BAR}tool_calls>{body}</{FULLWIDTH_BAR}{FULLWIDTH_BAR}DSML{FULLWIDTH_BAR}{FULLWIDTH_BAR}tool_calls>"


def _fullwidth_invoke(name: str, body: str) -> str:
    return f"<{FULLWIDTH_BAR}{FULLWIDTH_BAR}DSML{FULLWIDTH_BAR}{FULLWIDTH_BAR}invoke name=\"{name}\">{body}</{FULLWIDTH_BAR}{FULLWIDTH_BAR}DSML{FULLWIDTH_BAR}{FULLWIDTH_BAR}invoke>"


def _fullwidth_parameter(name: str, value: str) -> str:
    return f"<{FULLWIDTH_BAR}{FULLWIDTH_BAR}DSML{FULLWIDTH_BAR}{FULLWIDTH_BAR}parameter name=\"{name}\" string=\"true\">{value}</{FULLWIDTH_BAR}{FULLWIDTH_BAR}DSML{FULLWIDTH_BAR}{FULLWIDTH_BAR}parameter>"


def test_fullwidth_dsml_web_search_calls_are_normalized() -> None:
    samsung_dsml = _fullwidth_dsml(
        _fullwidth_invoke("web_search", _fullwidth_parameter("query", "Samsung Galaxy S25 Edge 参数 规格 配置"))
        + _fullwidth_invoke("web_search", _fullwidth_parameter("query", "Samsung Galaxy S25 Edge 价格 开卖 时间"))
    )

    message = normalize_text_tool_calls(AIMessage(content=samsung_dsml), tools)

    assert message.content == ""
    assert len(message.tool_calls) == 2
    assert [call["name"] for call in message.tool_calls] == ["web_search", "web_search"]
    assert message.tool_calls[0]["args"] == {"query": "Samsung Galaxy S25 Edge 参数 规格 配置"}
    assert message.tool_calls[1]["args"] == {"query": "Samsung Galaxy S25 Edge 价格 开卖 时间"}


def test_unknown_dsml_tool_is_dropped_and_hidden() -> None:
    content = "before " + _fullwidth_dsml(
        _fullwidth_invoke("unknown_tool", _fullwidth_parameter("query", "should not run"))
    ) + " after"

    message = normalize_text_tool_calls(AIMessage(content=content), tools)

    assert message.tool_calls == []
    assert "DSML" not in message.content
    assert "unknown_tool" not in message.content
    assert message.content == "before  after"


def test_strip_dsml_from_final_answer_message() -> None:
    content = "开头\n" + _fullwidth_dsml(
        _fullwidth_invoke("web_search", _fullwidth_parameter("query", "Samsung"))
    ) + "\n结尾"

    message = strip_dsml_from_message(AIMessage(content=content))

    assert "DSML" not in message.content
    assert "web_search" not in message.content
    assert message.content == "开头\n\n结尾"


def test_stream_filter_removes_split_fullwidth_dsml_block() -> None:
    block = _fullwidth_dsml(_fullwidth_invoke("web_search", _fullwidth_parameter("query", "Samsung")))
    stream_filter = DsmlStreamFilter()
    output = []

    for token in ["前", block[:5], block[5:19], block[19:-3], block[-3:], "后"]:
        output.append(stream_filter.feed(token))
    output.append(stream_filter.flush())

    assert "".join(output) == "前后"


def test_halfwidth_complete_dsml_block_is_removed() -> None:
    content = 'A<||DSML||tool_calls><||DSML||invoke name="web_search"><||DSML||parameter name="query" string="true">Samsung</||DSML||parameter></||DSML||invoke></||DSML||tool_calls>B'

    assert strip_dsml_tool_calls(content) == "AB"
