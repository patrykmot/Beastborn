"""Download the web client's third-party files into beastborn/ui/web/static/.

The files are committed, so this is only needed to refresh or verify them:

    python tools/fetch_assets.py

Pinned versions and the file lists live in beastborn/constance.py (game-icons comes from
the @iconify-json/game-icons npm package on jsDelivr).
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # run as a script from any folder

from beastborn.constance import (  # noqa: E402
    DOWNLOAD_TIMEOUT_SECONDS,
    GAME_ICONS_DEFAULT_SIZE,
    GAME_ICONS_JSON,
    ICON_FILES,
    STATIC_DIR,
    VENDOR_FILES,
)


def download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
        return response.read()


def write(rel_path: str, data: bytes) -> None:
    target: Path = STATIC_DIR / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    print(f"  {rel_path} ({len(data):,} bytes)")


def icon_svg(name: str, author: str, body: str, size: int) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">'
        f"<!-- {name} by {author}, game-icons.net, CC BY 3.0 -->{body}</svg>\n"
    )


def main() -> None:
    print("Vendor libraries:")
    for rel_path, url in VENDOR_FILES.items():
        write(rel_path, download(url))

    print("Icons (game-icons.net, CC BY 3.0):")
    icons: dict[str, Any] = json.loads(download(GAME_ICONS_JSON))
    size: int = icons.get("width", GAME_ICONS_DEFAULT_SIZE)
    for rel_path, (name, author) in ICON_FILES.items():
        write(rel_path, icon_svg(name, author, icons["icons"][name]["body"], size).encode("utf-8"))


if __name__ == "__main__":
    main()
