"""Layering rules: domain <- engine <- game <- control / ui."""
import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "beastborn"

FORBIDDEN = {
    "domain": ("pygame", "beastborn.engine", "beastborn.game", "beastborn.control", "beastborn.ui"),
    "engine": ("pygame", "beastborn.game", "beastborn.control", "beastborn.ui"),
    "game": ("pygame", "beastborn.control", "beastborn.ui"),
    "control": ("pygame", "beastborn.ui"),
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


def test_only_pygame_ui_imports_pygame():
    for path in PACKAGE.rglob("*.py"):
        if "pygame_ui" in path.parts:
            continue
        assert not any(m.split(".")[0] == "pygame" for m in imported_modules(path)), path
