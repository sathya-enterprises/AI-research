import ast
import json
from pathlib import Path
from typing import Any


class _FakeJson:
    def __init__(self, adapted):
        self.adapted = adapted


def _load_pure_sanitizers():
    source = Path(__file__).with_name("research_store.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    wanted = {"_clean_pg_text", "_clean_json_value", "_json"}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    namespace = {"Any": Any, "Json": _FakeJson, "json": json}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "research_store_sanitizers", "exec"), namespace)
    return namespace


def test_postgres_text_sanitizer_removes_nul_characters():
    helpers = _load_pure_sanitizers()
    assert helpers["_clean_pg_text"]("a\x00b") == "ab"


def test_json_sanitizer_removes_nested_nul_characters():
    helpers = _load_pure_sanitizers()
    payload = {"title\x00key": "a\x00b", "nested": ["x\x00y", {"value": "ok\x00"}]}
    adapted = helpers["_json"](payload).adapted
    serialized = json.dumps(adapted)
    assert "\\u0000" not in serialized
    assert "\x00" not in serialized
    assert adapted == {"titlekey": "ab", "nested": ["xy", {"value": "ok"}]}
