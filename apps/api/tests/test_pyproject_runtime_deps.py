"""Pin runtime vs. dev dependency placement (feat/triage-export-xlsx).

Railway only installs ``[project] dependencies`` — packages in
``[dependency-groups] dev`` are skipped on production. The xlsx export
endpoint (``GET /postings/export.xlsx``) imports openpyxl at request
time, so a dev-only listing would 500 in production while passing every
local test. This module pins openpyxl into the runtime list so the
mistake fails CI instead of failing prod.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _load_pyproject() -> dict[str, object]:
    with _PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)


def _runtime_dep_names() -> set[str]:
    data = _load_pyproject()
    deps = data["project"]["dependencies"]  # type: ignore[index]
    assert isinstance(deps, list)
    return {str(dep).split()[0].split(">=")[0].split("==")[0] for dep in deps}


def _dev_dep_names() -> set[str]:
    data = _load_pyproject()
    groups = data.get("dependency-groups") or {}
    assert isinstance(groups, dict)
    dev = groups.get("dev") or []
    assert isinstance(dev, list)
    return {str(dep).split()[0].split(">=")[0].split("==")[0] for dep in dev}


def test_openpyxl_is_a_runtime_dependency() -> None:
    """The /postings/export.xlsx endpoint imports openpyxl at request time."""
    assert "openpyxl" in _runtime_dep_names()


def test_openpyxl_is_not_in_dev_only_group() -> None:
    """Belt-and-braces: a stray listing in [dev] would still install
    locally and hide the missing-runtime-dep bug."""
    assert "openpyxl" not in _dev_dep_names()


def test_openpyxl_importable_at_runtime() -> None:
    """If the package isn't actually installed in the active env, the
    endpoint would fail with ImportError on first request — guard here."""
    import openpyxl  # noqa: F401
