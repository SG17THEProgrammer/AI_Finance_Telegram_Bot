"""
A restricted arithmetic evaluator. LLMs are unreliable at mental math even
when careful - this forces any calculation (e.g. summing multiple balance
sheet line items into a total) through actual deterministic code instead of
model-generated arithmetic, which can silently be off by a small amount even
when every individual figure it used was correct.

Deliberately NOT using eval() directly on user/model-supplied strings - that
would allow arbitrary code execution. This only permits numbers and the
operators +, -, *, /, and parentheses, via Python's ast module.
"""

import ast
import operator

_ALLOWED_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval_node(node):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("Only numeric constants are allowed")
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPERATORS:
        return _ALLOWED_OPERATORS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPERATORS:
        return _ALLOWED_OPERATORS[type(node.op)](_eval_node(node.operand))
    raise ValueError(f"Unsupported expression element: {type(node).__name__}")


def safe_calculate(expression: str) -> dict:
    """
    Evaluates a simple arithmetic expression like "1998 + 10954 + 95088".
    Returns {"expression": ..., "result": ...} on success, or {"error": ...}
    on anything that isn't valid/safe arithmetic - never raises.
    """
    try:
        # Strip common formatting the model might include, like commas or a
        # leading currency symbol, so "1,998 + 10,954" still parses cleanly.
        cleaned = expression.replace(",", "").replace("$", "").strip()
        tree = ast.parse(cleaned, mode="eval")
        result = _eval_node(tree.body)
        return {"expression": expression, "result": result}
    except Exception as exc:
        return {"error": f"Could not evaluate '{expression}': {exc}"}