"""Layering rules: domain <- engine <- game <- control / ui."""
import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "beastborn"

WEB = ("flask", "werkzeug", "pydantic")
REMOVED_WEB = ("fastapi", "starlette", "uvicorn", "httpx")

FORBIDDEN = {
    "domain": ("pygame", *WEB, "beastborn.engine", "beastborn.game", "beastborn.control", "beastborn.ui"),
    "engine": ("pygame", *WEB, "beastborn.game", "beastborn.control", "beastborn.ui"),
    "game": ("pygame", *WEB, "beastborn.control", "beastborn.ui"),
    "control": ("pygame", *WEB, "beastborn.ui"),
}


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("layer", sorted(FORBIDDEN))
def test_layer_dependencies(layer):
    for path in (PACKAGE / layer).rglob("*.py"):
        for module in imported_modules(path):
            for forbidden in FORBIDDEN[layer]:
                assert not (module == forbidden or module.startswith(forbidden + ".")), (
                    f"{path.relative_to(PACKAGE)} imports {module}"
                )


def test_no_pygame_anywhere():
    for path in PACKAGE.rglob("*.py"):
        assert not any(m.split(".")[0] == "pygame" for m in imported_modules(path)), path


def test_web_frameworks_only_in_ui_web():
    for path in PACKAGE.rglob("*.py"):
        if "web" in path.relative_to(PACKAGE).parts:
            continue
        assert not any(m.split(".")[0] in WEB for m in imported_modules(path)), path


def test_no_fastapi_anywhere():
    for path in [*PACKAGE.rglob("*.py"), *PACKAGE.parent.joinpath("tests").rglob("*.py"), PACKAGE.parent / "main.py"]:
        assert not any(m.split(".")[0] in REMOVED_WEB for m in imported_modules(path)), path


def test_constance_is_a_leaf_module():
    """Every layer imports beastborn/constance.py, so it must not import beastborn itself (no cycles)."""
    modules = imported_modules(PACKAGE / "constance.py")
    assert not any(m.split(".")[0] == "beastborn" for m in modules), modules
