"""MANDATORY isolation invariant for the A4 eval (Phase 1).

OpenAI is an offline-eval-only dependency. These tests statically assert that:

  1. No production module (everything under ``src/job_assist`` EXCEPT
     ``job_assist/eval/``) imports ``openai`` — so OpenAI can never be reached
     from any cron/sweep/ingest/scoring/route/hot path.
  2. No production module imports ``job_assist.eval`` — so the eval package is
     never wired into a route or cron.

Static AST scan (no imports executed), so the guard holds even if openai isn't
installed in the running environment.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src" / "job_assist"
_EVAL = _SRC / "eval"


def _production_py_files() -> list[Path]:
    return [p for p in _SRC.rglob("*.py") if _EVAL not in p.parents and p != _EVAL]


def _imported_names(path: Path) -> set[str]:
    """Top-level dotted names introduced by import statements in *path*."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_no_production_module_imports_openai() -> None:
    offenders: list[str] = []
    for path in _production_py_files():
        for name in _imported_names(path):
            if name == "openai" or name.startswith("openai."):
                offenders.append(str(path.relative_to(_SRC)))
                break
    assert not offenders, (
        "openai must be imported ONLY under job_assist/eval/. Offending "
        f"production modules: {offenders}"
    )


def test_no_production_module_imports_eval_package() -> None:
    offenders: list[str] = []
    for path in _production_py_files():
        for name in _imported_names(path):
            if name == "job_assist.eval" or name.startswith("job_assist.eval."):
                offenders.append(str(path.relative_to(_SRC)))
                break
    assert not offenders, (
        "job_assist.eval is offline-only and must not be imported by production "
        f"code (routes/crons). Offending modules: {offenders}"
    )


def test_eval_package_exists_and_is_the_openai_home() -> None:
    """Sanity: the eval package exists and openai_labeler is the openai importer."""
    labeler = _EVAL / "openai_labeler.py"
    assert labeler.exists()
    assert "openai" in "".join(
        n for n in _imported_names(labeler)
    ) or "from openai" in labeler.read_text(encoding="utf-8")
