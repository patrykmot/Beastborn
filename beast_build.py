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
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "beast.zip"

# Single files at the project root.
FILES = [
    "main.py",
    "gercio_eu_pythonanywhere_com_wsgi.py",
    "requirements.txt",
    "beast_install.py",
    "README.md",
    "LICENSE",
]
# Folders copied recursively (minus SKIP_DIRS / SKIP_SUFFIXES).
DIRS = ["beastborn"]
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
# Must be in the zip, or the build fails (beast_install.py checks the same list).
REQUIRED = [
    "main.py",
    "gercio_eu_pythonanywhere_com_wsgi.py",
    "requirements.txt",
    "beast_install.py",
    "beastborn/__init__.py",
    "beastborn/ui/web/app.py",
    "beastborn/ui/web/static/index.html",
    "beastborn/data/units.json",
]
BUILD_INFO = "BUILD_INFO.txt"


def collect() -> list[Path]:
    files = []
    for name in FILES:
        path = ROOT / name
        if not path.is_file():
            sys.exit(f"ERROR: missing {name}")
        files.append(path)
    for name in DIRS:
        for path in sorted((ROOT / name).rglob("*")):
            rel = path.relative_to(ROOT)
            if path.is_file() and not SKIP_DIRS.intersection(rel.parts) and path.suffix not in SKIP_SUFFIXES:
                files.append(path)
    return files


def git_info() -> str:
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        return commit + (" (+ uncommitted changes)" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown (git not available)"


def build(output: Path) -> Path:
    files = collect()
    names = [p.relative_to(ROOT).as_posix() for p in files]
    missing = [r for r in REQUIRED if r not in names]
    if missing:
        sys.exit(f"ERROR: required files missing: {missing}")

    info = (
        f"Beastborn build\n"
        f"built:  {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
        f"commit: {git_info()}\n"
        f"files:  {len(files)}\n"
    )
    tmp = output.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, name in zip(files, names):
            zf.write(path, name)
        zf.writestr(BUILD_INFO, info)
    tmp.replace(output)  # never leave a half-written beast.zip behind

    with zipfile.ZipFile(output) as zf:  # read back to be sure it is valid
        bad = zf.testzip()
        if bad:
            sys.exit(f"ERROR: corrupt entry in zip: {bad}")
    size_kb = output.stat().st_size / 1024
    print(info.rstrip())
    print(f"wrote:  {output} ({size_kb:.0f} KB)")
    print("next:   upload it to /home/gercio on PythonAnywhere and run  python3.10 beast_install.py")
    return output


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Build beast.zip for PythonAnywhere")
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT, help="zip file to write (default: beast.zip)")
    args = parser.parse_args(argv)
    build(args.output.resolve())


if __name__ == "__main__":
    main()
