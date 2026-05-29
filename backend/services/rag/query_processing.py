from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langsmith import traceable

from config.settings import Settings


@dataclass(frozen=True)
class QueryVariant:
    """One retrieval query with its role and conservative score weight."""

    text: str
    role: str
    weight: float = 1.0


@dataclass(frozen=True)
class ProcessedQuery:
    """Structured query features used by hybrid retrieval and reranking."""

    original: str
    normalized: str
    variants: list[str]
    tokens: list[str]
    primary_queries: list[QueryVariant] = field(default_factory=list)
    expansion_queries: list[QueryVariant] = field(default_factory=list)
    rewritten_query: str = ""
    sub_queries: list[str] = field(default_factory=list)
    hyde_document: str = ""
    strategy: str = "light"
    llm_error: str = ""

    @property
    def vector_queries(self) -> list[QueryVariant]:
        """Return vector queries with original-question queries first."""
        return [*self.primary_queries, *self.expansion_queries]


@traceable(name="rag.query_preprocess", run_type="chain")
def preprocess_query(query: str, app_settings: Settings | None = None) -> ProcessedQuery:
    """Create retrieval-ready query variants while preserving the original query."""
    normalized = _normalize(query)
    primary_queries = _primary_queries(query, normalized)
    expansion = _build_expansion(query, normalized, app_settings)

    expansion_queries = _expansion_queries(
        expansion,
        expansion_weight=_setting_float(app_settings, "rag_expansion_vector_weight", 0.92),
        hyde_weight=_setting_float(app_settings, "rag_hyde_vector_weight", 0.86),
    )
    variants = _unique(
        [
            *(variant.text for variant in primary_queries),
            expansion.rewritten_query,
            *expansion.search_queries,
            *expansion.sub_queries,
        ]
    )
    token_text = " ".join([*variants, expansion.hyde_document])
    tokens = _tokens(token_text)
    strategy = "expanded" if expansion_queries else "light"

    return ProcessedQuery(
        original=query,
        normalized=normalized,
        variants=variants,
        tokens=tokens,
        primary_queries=primary_queries,
        expansion_queries=expansion_queries,
        rewritten_query=expansion.rewritten_query,
        sub_queries=expansion.sub_queries,
        hyde_document=expansion.hyde_document,
        strategy=strategy,
        llm_error=expansion.error,
    )


@dataclass(frozen=True)
class _Expansion:
    rewritten_query: str = ""
    search_queries: list[str] = field(default_factory=list)
    sub_queries: list[str] = field(default_factory=list)
    hyde_document: str = ""
    error: str = ""


def _build_expansion(query: str, normalized: str, app_settings: Settings | None) -> _Expansion:
    """Use LLM expansion when useful; fall back to deterministic industrial hints."""
    if not query.strip() or not _needs_expansion(query, normalized):
        return _Expansion()

    llm_expansion = _llm_expansion(query, app_settings)
    if llm_expansion.search_queries or llm_expansion.hyde_document:
        return llm_expansion

    fallback = _rule_based_expansion(query, normalized)
    return _Expansion(
        rewritten_query=fallback.rewritten_query,
        search_queries=fallback.search_queries,
        sub_queries=fallback.sub_queries,
        hyde_document=fallback.hyde_document,
        error=llm_expansion.error,
    )


def _llm_expansion(query: str, app_settings: Settings | None) -> _Expansion:
    """Generate Query Rewrite, Multi-Query, Decomposition, and HyDE text."""
    if app_settings is None or not app_settings.rag_query_expansion_enabled:
        return _Expansion()
    if not app_settings.deepseek_api_key or not app_settings.deepseek_tool_model:
        return _Expansion(error="DeepSeek query expansion skipped: model is not configured.")

    try:
        from agent.llm import DeepSeekChatOpenAI

        llm = DeepSeekChatOpenAI(
            model=app_settings.deepseek_tool_model,
            api_key=app_settings.deepseek_api_key,
            base_url=app_settings.deepseek_base_url,
            temperature=0,
            streaming=False,
            timeout=app_settings.rag_query_expansion_timeout_seconds,
            extra_body={"thinking": {"type": "disabled"}},
        )
        response = llm.invoke(
            [
                SystemMessage(content=_QUERY_EXPANSION_SYSTEM_PROMPT),
                HumanMessage(content=f"用户问题：{query}"),
            ]
        )
        payload = _parse_json_object(str(response.content))
        return _expansion_from_payload(payload)
    except Exception as exc:
        return _Expansion(error=f"DeepSeek query expansion failed: {exc}")


_QUERY_EXPANSION_SYSTEM_PROMPT = """你是工业知识库 RAG 检索前的问题预处理器。
输出严格 JSON，不要 Markdown。字段：
{
  "rewritten_query": "一个更适合检索的中文查询",
  "search_queries": ["2 到 4 个互补检索查询"],
  "sub_queries": ["复杂问题拆解出的 0 到 3 个子问题"],
  "hyde_document": "一段 80 到 180 字的假想工业手册片段"
}
要求：
1. 保留用户明确给出的设备位号、测点名、报警名、系统名和参数名。
2. 不要编造具体设备编号或测点编号；用户没给 F101 就不能写 F101。
3. 面向检索，补充异常现象、原因排查、处置流程、操作手册等工业文档关键词。
4. 如果问题已经很清晰，只返回少量同义检索表达，不能改变原意。
5. hyde_document 写成可能出现在手册里的概括性段落，避免给最终操作结论。"""


def _expansion_from_payload(payload: dict[str, Any]) -> _Expansion:
    rewritten_query = _clean_variant(payload.get("rewritten_query"))
    search_queries = _unique(_clean_variant(item) for item in _as_list(payload.get("search_queries")))
    sub_queries = _unique(_clean_variant(item) for item in _as_list(payload.get("sub_queries")))
    hyde_document = _clean_hyde(payload.get("hyde_document"))
    return _Expansion(
        rewritten_query=rewritten_query,
        search_queries=search_queries[:4],
        sub_queries=sub_queries[:3],
        hyde_document=hyde_document,
    )


def _rule_based_expansion(query: str, normalized: str) -> _Expansion:
    """Deterministic fallback for colloquial industrial troubleshooting queries."""
    terms = _important_terms(query, normalized)
    subject = " ".join(terms[:8]) or query.strip()
    intent_terms = _intent_terms(normalized)

    rewritten = _clean_variant(" ".join([subject, *intent_terms]))
    search_queries = [
        rewritten,
        _clean_variant(f"{subject} 异常 原因 排查"),
        _clean_variant(f"{subject} 报警 操作手册 处置流程"),
    ]
    sub_queries = [
        _clean_variant(f"{subject} 当前异常现象和关联工艺参数是什么"),
        _clean_variant(f"{subject} 可能原因与检查项"),
        _clean_variant(f"{subject} 安全处置步骤和恢复条件"),
    ]
    hyde = _clean_hyde(
        f"{subject} 出现异常时，应结合趋势、报警记录和相关工艺参数进行确认。"
        f"排查通常包括现场仪表状态、控制阀开度、上下游压力流量、联锁条件、"
        f"操作模式以及近期操作变更。处置流程应优先参考设备操作规程、报警说明"
        f"和异常工况处理章节。"
    )
    return _Expansion(
        rewritten_query=rewritten,
        search_queries=_unique(search_queries),
        sub_queries=_unique(sub_queries),
        hyde_document=hyde,
    )


def _primary_queries(original: str, normalized: str) -> list[QueryVariant]:
    """Keep exact and lightly normalized queries as the highest-priority path."""
    spaced = re.sub(r"([a-zA-Z0-9_./-]+)([\u4e00-\u9fff])", r"\1 \2", original)
    spaced = re.sub(r"([\u4e00-\u9fff])([a-zA-Z0-9_./-]+)", r"\1 \2", spaced)
    compact = normalized.replace(" ", "")
    return [
        QueryVariant(text=item, role=role, weight=1.0)
        for item, role in _unique_pairs(
            [
                (original.strip(), "original"),
                (normalized, "normalized"),
                (spaced.strip(), "spaced"),
                (compact, "compact"),
            ]
        )
        if item
    ]


def _expansion_queries(expansion: _Expansion, *, expansion_weight: float, hyde_weight: float) -> list[QueryVariant]:
    queries: list[QueryVariant] = []
    if expansion.rewritten_query:
        queries.append(QueryVariant(expansion.rewritten_query, "rewrite", expansion_weight))
    queries.extend(QueryVariant(item, "multi_query", expansion_weight) for item in expansion.search_queries)
    queries.extend(QueryVariant(item, "sub_query", max(expansion_weight - 0.03, 0.75)) for item in expansion.sub_queries)
    if expansion.hyde_document:
        queries.append(QueryVariant(expansion.hyde_document, "hyde", hyde_weight))
    return _dedupe_query_variants(queries)


def _needs_expansion(query: str, normalized: str) -> bool:
    """Return whether rewrite/multi-query/HyDE is likely to improve recall."""
    if not normalized:
        return False
    has_identifier = bool(re.search(r"[a-zA-Z]+[-_./]?\d+|\d+[-_.]?\d*|[A-Z_]{2,}", query))
    has_colloquial = any(term in normalized for term in _COLLOQUIAL_TERMS)
    has_troubleshooting = any(term in normalized for term in _TROUBLESHOOTING_TERMS)
    has_multiple_clauses = len(re.split(r"[，,；;。.!？?]|以及|并且|然后|同时", query)) >= 3
    asks_question = any(term in normalized for term in {"怎么", "如何", "为什么", "原因", "怎么办", "咋办", "哪些", "是否"})
    token_count = len(_tokens(query))
    if has_colloquial or has_troubleshooting or has_multiple_clauses:
        return True
    if asks_question and token_count > 14:
        return True
    return not has_identifier and asks_question and token_count > 18


_COLLOQUIAL_TERMS = {
    "咋办",
    "怎么办",
    "怎么回事",
    "老是",
    "一直",
    "总是",
    "不太对",
    "不正常",
    "有问题",
    "往上跑",
    "掉下来",
    "上不去",
    "下不来",
}

_TROUBLESHOOTING_TERMS = {
    "异常",
    "报警",
    "故障",
    "处置",
    "处理",
    "排查",
    "原因",
    "升高",
    "降低",
    "波动",
    "超限",
    "联锁",
    "跳车",
}


def _intent_terms(normalized: str) -> list[str]:
    terms = ["异常", "排查", "处置流程"]
    if any(term in normalized for term in {"温度", "升高", "高温", "往上跑"}):
        terms.extend(["温度持续升高", "高温报警"])
    if any(term in normalized for term in {"压力", "压差"}):
        terms.extend(["压力异常", "压力波动"])
    if any(term in normalized for term in {"流量", "进料"}):
        terms.extend(["流量异常", "进料流量"])
    if any(term in normalized for term in {"阀", "阀门"}):
        terms.extend(["阀门开度", "控制阀"])
    return _unique(terms)


def _important_terms(query: str, normalized: str) -> list[str]:
    ascii_terms = re.findall(r"[A-Za-z][A-Za-z0-9_./-]*\d*|\d+[A-Za-z0-9_./-]*", query)
    known_terms = [term for term in _INDUSTRIAL_TERMS if term in normalized]
    cleaned = normalized
    for term in [*_COLLOQUIAL_TERMS, *_STOP_TERMS, *_TROUBLESHOOTING_TERMS]:
        cleaned = cleaned.replace(term, " ")
    cjk_terms = [term for term in re.split(r"\s+", cleaned) if 2 <= len(term) <= 12]
    if not cjk_terms:
        chars = re.findall(r"[\u4e00-\u9fff]{2,}", cleaned)
        cjk_terms = [item for item in chars if item not in _STOP_TERMS]
    return _unique([*ascii_terms, *known_terms, *cjk_terms])


_INDUSTRIAL_TERMS = [
    "裂解炉",
    "加热炉",
    "反应器",
    "压缩机",
    "泵",
    "炉子",
    "出口温度",
    "入口温度",
    "炉膛温度",
    "炉膛压力",
    "温度",
    "压力",
    "压差",
    "流量",
    "液位",
    "进料",
    "燃气",
    "阀门",
    "控制阀",
    "开度",
    "报警",
    "联锁",
    "操作手册",
    "处置流程",
]


_STOP_TERMS = {
    "这个",
    "那个",
    "一下",
    "怎么",
    "怎么办",
    "咋办",
    "什么",
    "是不是",
    "有没有",
}


def _normalize(text: str) -> str:
    text = _to_halfwidth(text).strip().lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[\uFF0C\u3002\uFF01\uFF1F\uFF1B\uFF1A\u3001,.!?;:()\uFF08\uFF09\u3010\u3011\[\]\"']", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> list[str]:
    normalized = _normalize(text)
    ascii_words = re.findall(r"[a-z0-9_./-]+", normalized)
    cjk_words = re.findall(r"[\u4e00-\u9fff]{2,}", normalized)
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", normalized)
    cjk_bigrams = [a + b for a, b in zip(cjk_chars, cjk_chars[1:], strict=False)]
    cjk_trigrams = [
        a + b + c
        for a, b, c in zip(cjk_chars, cjk_chars[1:], cjk_chars[2:], strict=False)
    ]
    return _unique(ascii_words + cjk_words + cjk_trigrams + cjk_bigrams + cjk_chars)


def _to_halfwidth(text: str) -> str:
    chars: list[str] = []
    for char in text:
        code = ord(char)
        if code == 0x3000:
            chars.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            chars.append(chr(code - 0xFEE0))
        else:
            chars.append(char)
    return "".join(chars)


def _parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    parsed = json.loads(text)
    return parsed if isinstance(parsed, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean_variant(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:240]


def _clean_hyde(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:600]


def _setting_float(app_settings: Settings | None, name: str, default: float) -> float:
    value = getattr(app_settings, name, default) if app_settings is not None else default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _dedupe_query_variants(queries: list[QueryVariant]) -> list[QueryVariant]:
    deduped: list[QueryVariant] = []
    seen: set[str] = set()
    for query in queries:
        key = _normalize(query.text)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(query)
    return deduped


def _unique(items) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = str(item).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


def _unique_pairs(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for text, role in items:
        key = str(text).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append((key, role))
    return result
