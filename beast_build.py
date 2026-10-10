"""Pack Beastborn for PythonAnywhere: python beast_build.py  ->  beast.zip

The zip holds only what the server needs (game package, entry points, requirements,
the installer). Upload beast.zip (and, the first time, beast_install.py) to
/home/gercio on PythonAnywhere, then run there:  python3.10 beast_install.py
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import zipfile
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

# Shared with the installer, so the zip always contains what beast_install.py checks for.
from beast_install import BUILD_INFO, INSTALLER_NAME, REQUIRED, REQUIREMENTS, WSGI_NAME, ZIP_NAME

ROOT: Final[Path] = Path(__file__).resolve().parent
DEFAULT_OUTPUT: Final[Path] = ROOT / ZIP_NAME

# Single files at the project root.
FILES: Final[tuple[str, ...]] = ("main.py", WSGI_NAME, REQUIREMENTS, INSTALLER_NAME, "README.md", "LICENSE")
# Folders copied recursively (minus SKIP_DIRS / SKIP_SUFFIXES).
DIRS: Final[tuple[str, ...]] = ("beastborn",)
SKIP_DIRS: Final[frozenset[str]] = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache"})
SKIP_SUFFIXES: Final[frozenset[str]] = frozenset({".pyc", ".pyo"})


def collect() -> list[Path]:
    files: list[Path] = []
    for name in FILES:
        path: Path = ROOT / name
        if not path.is_file():
            sys.exit(f"ERROR: missing {name}")
        files.append(path)
    for name in DIRS:
        files += [path for path in sorted((ROOT / name).rglob("*")) if _packable(path)]
    return files


def _packable(path: Path) -> bool:
    return (
        path.is_file()
        and not SKIP_DIRS.intersection(path.relative_to(ROOT).parts)
        and path.suffix not in SKIP_SUFFIXES
    )


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def git_info() -> str:
    try:
        commit: str = _git("rev-parse", "--short", "HEAD")
        dirty: str = _git("status", "--porcelain")
        return commit + (" (+ uncommitted changes)" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown (git not available)"


def build(output: Path) -> Path:
    files: list[Path] = collect()
    names: list[str] = [p.relative_to(ROOT).as_posix() for p in files]
    missing: list[str] = [r for r in REQUIRED if r not in names]
    if missing:
        sys.exit(f"ERROR: required files missing: {missing}")

    info: str = (
        f"Beastborn build\n"
        f"built:  {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
        f"commit: {git_info()}\n"
        f"files:  {len(files)}\n"
    )
    tmp: Path = output.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, name in zip(files, names):
            zf.write(path, name)
        zf.writestr(BUILD_INFO, info)
    tmp.replace(output)  # never leave a half-written beast.zip behind

    with zipfile.ZipFile(output) as zf:  # read back to be sure it is valid
        bad: str | None = zf.testzip()
        if bad:
            sys.exit(f"ERROR: corrupt entry in zip: {bad}")
    size_kb: float = output.stat().st_size / 1024
    print(info.rstrip())
    print(f"wrote:  {output} ({size_kb:.0f} KB)")
    print(f"next:   upload it to /home/gercio on PythonAnywhere and run  python3.10 {INSTALLER_NAME}")
    return output


def main(argv: Sequence[str] | None = None) -> None:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description=f"Build {ZIP_NAME} for PythonAnywhere")
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT, help=f"zip file to write (default: {ZIP_NAME})")
    args: argparse.Namespace = parser.parse_args(argv)
    build(args.output.resolve())


if __name__ == "__main__":
    main()
