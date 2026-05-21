from __future__ import annotations

import ast
import operator
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langchain_core.tools import tool

from agent.tools.schemas import CalculatorInput, CurrentTimeInput


@tool(args_schema=CurrentTimeInput)
def get_current_time(timezone: str = "Asia/Shanghai") -> str:
    """Get the current date and time for a timezone."""
    try:
        now = datetime.now(ZoneInfo(timezone))
    except ZoneInfoNotFoundError:
        return f"Unknown timezone: {timezone}. Use an IANA timezone such as Asia/Shanghai or UTC."
    return now.isoformat(timespec="seconds")


@tool(args_schema=CalculatorInput)
def calculate(expression: str) -> str:
    """Evaluate a safe arithmetic expression."""
    try:
        value = _safe_eval(expression)
    except Exception as exc:
        return f"Calculation failed: {exc}"
    return str(value)


_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPERATORS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _safe_eval(expression: str) -> int | float:
    """Evaluate arithmetic using Python AST instead of eval."""
    tree = ast.parse(expression, mode="eval")
    return _eval_node(tree.body)


def _eval_node(node: ast.AST) -> int | float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 12:
            raise ValueError("power exponent is too large")
        return _BINARY_OPERATORS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        return _UNARY_OPERATORS[type(node.op)](_eval_node(node.operand))
    raise ValueError("only arithmetic expressions are allowed")
