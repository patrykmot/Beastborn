"""Download the web client's third-party files into beastborn/ui/web/static/.

The files are committed, so this is only needed to refresh or verify them:

    python tools/fetch_assets.py

Pinned versions: jQuery 3.7.1, Bootstrap 5.3.3, Bootstrap Icons 1.11.3, game-icons 1.2.4
(via the @iconify-json/game-icons npm package on jsDelivr).
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "beastborn" / "ui" / "web" / "static"
CDN = "https://cdn.jsdelivr.net/npm"

VENDOR = {
    "vendor/jquery/jquery-3.7.1.min.js": f"{CDN}/jquery@3.7.1/dist/jquery.min.js",
    "vendor/bootstrap/bootstrap.min.css": f"{CDN}/bootstrap@5.3.3/dist/css/bootstrap.min.css",
    "vendor/bootstrap/bootstrap.bundle.min.js": f"{CDN}/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js",
    "vendor/bootstrap-icons/bootstrap-icons.min.css": f"{CDN}/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css",
    "vendor/bootstrap-icons/fonts/bootstrap-icons.woff2": f"{CDN}/bootstrap-icons@1.11.3/font/fonts/bootstrap-icons.woff2",
    "vendor/bootstrap-icons/fonts/bootstrap-icons.woff": f"{CDN}/bootstrap-icons@1.11.3/font/fonts/bootstrap-icons.woff",
}

GAME_ICONS_JSON = f"{CDN}/@iconify-json/game-icons@1.2.4/icons.json"

# target file -> (game-icons name, author)  - licence CC BY 3.0, see static/CREDITS.md
ICONS = {
    "img/units/boss.svg": ("ogre", "Delapouite"),
    "img/units/big_rat.svg": ("rat", "Delapouite"),
    "img/units/peasant.svg": ("farmer", "Delapouite"),
    "img/units/archer.svg": ("archer", "Delapouite"),
    "img/terrain/grass.svg": ("grass", "Delapouite"),
    "img/terrain/swamp.svg": ("swamp", "Delapouite"),
    "img/terrain/hills.svg": ("hills", "Delapouite"),
    "img/effects/venom.svg": ("poison-bottle", "Lorc"),
    "img/effects/acid.svg": ("acid", "Sbed"),
}


def download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def write(rel_path: str, data: bytes) -> None:
    target = STATIC / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    print(f"  {rel_path} ({len(data):,} bytes)")


def main() -> None:
    print("Vendor libraries:")
    for rel_path, url in VENDOR.items():
        write(rel_path, download(url))

    print("Icons (game-icons.net, CC BY 3.0):")
    icons = json.loads(download(GAME_ICONS_JSON))
    size = icons.get("width", 512)
    for rel_path, (name, author) in ICONS.items():
        body = icons["icons"][name]["body"]
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">'
            f"<!-- {name} by {author}, game-icons.net, CC BY 3.0 -->{body}</svg>\n"
        )
        write(rel_path, svg.encode("utf-8"))


if __name__ == "__main__":
    main()
