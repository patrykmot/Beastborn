r"""Install beast.zip on PythonAnywhere.

Upload beast.zip and this file to /home/gercio, then in a Bash console run:

    python3.10 beast_install.py            # install / update (use your web app's Python version)
    python3.10 beast_install.py --rollback # switch back to the previous version

Trial run on your own computer (Windows too), into test folders instead of the server paths:

    python beast_install.py --target D:\tmp\mysite --www-dir D:\tmp\www

What it does:
  1. checks beast.zip and unpacks it to <project>_new
     (<project> = SERVER_PROJECT_HOME from the WSGI file in the zip, i.e. /home/gercio/mysite)
  2. pip install --user -r requirements.txt   (with the Python that runs this script)
  3. smoke-tests the new version (page + new game) before touching the live site
  4. moves the old version to <project>_backup (only the last one is kept) and the new one in place
  5. copies gercio_eu_pythonanywhere_com_wsgi.py to /var/www/ and reloads the web app
If anything fails before step 4, the live site is left untouched.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Final, NoReturn

# Deployment constants. They stay in this file (not in beastborn/constance.py) because this script
# runs on the server on its own, before the beastborn package is unpacked. beast_build.py imports them.
USERNAME: Final[str] = "gercio"
DOMAIN: Final[str] = "gercio.eu.pythonanywhere.com"
API_HOST: Final[str] = "https://eu.pythonanywhere.com"
API_TOKEN_ENV: Final[str] = "API_TOKEN"
API_TIMEOUT_SECONDS: Final[int] = 30
WSGI_NAME: Final[str] = "gercio_eu_pythonanywhere_com_wsgi.py"
WWW_DIR: Final[Path] = Path("/var/www")
DEFAULT_SERVER_HOME: Final[str] = f"/home/{USERNAME}/mysite"  # same as SERVER_PROJECT_HOME in the WSGI file
LIVE_WSGI_COPY: Final[str] = "_live_wsgi_backup.py"  # the /var/www file that was live with a backed-up version
ZIP_NAME: Final[str] = "beast.zip"
BUILD_INFO: Final[str] = "BUILD_INFO.txt"
REQUIREMENTS: Final[str] = "requirements.txt"
INSTALLER_NAME: Final[str] = "beast_install.py"
STAGING_SUFFIX: Final[str] = "_new"
BACKUP_SUFFIX: Final[str] = "_backup"
SWAP_SUFFIX: Final[str] = "_swap"
MIN_PYTHON: Final[tuple[int, int]] = (3, 10)
# Must be in the zip (beast_build.py refuses to build without them, this script refuses to install).
REQUIRED: Final[tuple[str, ...]] = (
    "main.py",
    WSGI_NAME,
    REQUIREMENTS,
    INSTALLER_NAME,
    "beastborn/__init__.py",
    "beastborn/ui/web/app.py",
    "beastborn/ui/web/static/index.html",
    "beastborn/data/units.json",
)
NO_WWW_PERMISSION: Final[str] = "no permission to write {path}. Create the web app on the Web tab first, then retry."
SMOKE_TEST: Final[str] = """
import sys
sys.path.insert(0, sys.argv[1])
from beastborn.ui.web.app import create_app
client = create_app().test_client()
assert client.get("/").status_code == 200, "index page"
assert client.post("/api/games", json={"players": 2, "seed": 1}).status_code == 201, "new game"
print("smoke test passed")
"""


def step(text: str) -> None:
    print(f"\n==> {text}", flush=True)


def python_version() -> str:
    """E.g. '3.10' (the form PythonAnywhere uses for python3.10 and on the Web tab)."""
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def sibling(folder: Path, suffix: str) -> Path:
    """/home/gercio/mysite + '_backup' -> /home/gercio/mysite_backup"""
    return folder.with_name(folder.name + suffix)


def fail(text: str) -> NoReturn:
    sys.exit(f"\nERROR: {text}")


# ------------------------------------------------------------------ checks
def check_zip(zip_path: Path) -> str:
    """Validate beast.zip; return the server project folder named in its WSGI file (a Linux path)."""
    if not zip_path.is_file():
        fail(f"{zip_path} not found. Upload beast.zip next to this script (or pass its path).")
    try:
        zf: zipfile.ZipFile = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        fail(f"{zip_path} is not a valid zip file (upload it again).")
    with zf:
        bad: str | None = zf.testzip()
        if bad:
            fail(f"corrupt file in zip: {bad} (upload it again).")
        names: list[str] = zf.namelist()
        for name in names:  # no absolute paths or ".." (zip slip)
            p: PurePosixPath = PurePosixPath(name)
            if p.is_absolute() or ".." in p.parts or "\\" in name:
                fail(f"unsafe path in zip: {name}")
        missing: list[str] = [r for r in REQUIRED if r not in names]
        if missing:
            fail(f"zip is missing {missing}. Rebuild it with beast_build.py.")
        if BUILD_INFO in names:
            print(zf.read(BUILD_INFO).decode("utf-8").rstrip())
        wsgi: str = zf.read(WSGI_NAME).decode("utf-8")
    match: re.Match[str] | None = re.search(r'^SERVER_PROJECT_HOME\s*=\s*["\']([^"\']+)["\']', wsgi, re.MULTILINE)
    if not match:
        fail(f"SERVER_PROJECT_HOME not found in {WSGI_NAME}.")
    server_home: str = match.group(1)
    if not PurePosixPath(server_home).is_absolute():  # it is a path on the PythonAnywhere (Linux) server
        fail(f"SERVER_PROJECT_HOME must be an absolute Linux path like {DEFAULT_SERVER_HOME}, got {server_home}.")
    return server_home


def resolve_target(server_home: str, target_override: Path | None) -> Path:
    """Where to install: --target if given, else SERVER_PROJECT_HOME (only possible on the Linux server)."""
    if target_override is not None:
        print(f"trial run: installing into {target_override} instead of {server_home}")
        return target_override
    if platform.system() != "Linux":
        fail(
            f"this installs into {server_home} on PythonAnywhere (Linux), but this computer runs {platform.system()}.\n"
            "  Run it in a PythonAnywhere Bash console. For a trial run here, pass test folders:\n"
            "  python beast_install.py --target D:\\tmp\\mysite --www-dir D:\\tmp\\www"
        )
    return Path(server_home)


def check_python() -> None:
    if sys.version_info < MIN_PYTHON:
        fail(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ needed, this is {sys.version.split()[0]}. On PythonAnywhere run e.g.  python3.10 beast_install.py")


# ------------------------------------------------------------------ steps
def unpack(zip_path: Path, staging: Path) -> None:
    if staging.exists():
        shutil.rmtree(staging)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(staging)


def pip_install(requirements: Path) -> None:
    in_venv: bool = sys.prefix != sys.base_prefix
    cmd: list[str] = [sys.executable, "-m", "pip", "install", "-r", str(requirements)]
    if not in_venv:
        cmd.insert(4, "--user")
    print(" ".join(cmd), flush=True)
    if subprocess.run(cmd).returncode != 0:
        fail("pip install failed (see output above). The live site was not changed.")


def smoke_test(folder: Path) -> None:
    result: subprocess.CompletedProcess[bytes] = subprocess.run([sys.executable, "-c", SMOKE_TEST, str(folder)], cwd=str(folder))
    if result.returncode != 0:
        fail("the new version does not start (see output above). The live site was not changed.")


def save_live_wsgi(folder: Path, wsgi_dest: Path) -> None:
    """Remember which /var/www file was live together with this folder (used by --rollback)."""
    if wsgi_dest.is_file() and folder.is_dir():
        shutil.copyfile(wsgi_dest, folder / LIVE_WSGI_COPY)


def check_www(wsgi_dest: Path) -> None:
    """Fail early, before anything is changed, if the WSGI file cannot be written."""
    if not wsgi_dest.parent.is_dir():
        fail(f"{wsgi_dest.parent} does not exist. Is this PythonAnywhere? (use --www-dir to test elsewhere)")
    writable: bool = os.access(wsgi_dest, os.W_OK) if wsgi_dest.exists() else os.access(wsgi_dest.parent, os.W_OK)
    if not writable:
        fail(NO_WWW_PERMISSION.format(path=wsgi_dest))


def install_wsgi(source: Path, wsgi_dest: Path) -> None:
    check_www(wsgi_dest)
    try:
        shutil.copyfile(source, wsgi_dest)
        os.utime(wsgi_dest)  # a changed WSGI file makes PythonAnywhere reload the web app
    except PermissionError:
        fail(NO_WWW_PERMISSION.format(path=wsgi_dest))
    print(f"{source} -> {wsgi_dest}")


def api_call(method: str, path: str) -> dict[str, Any] | None:
    token: str | None = os.environ.get(API_TOKEN_ENV)
    if not token:
        return None
    request: urllib.request.Request = urllib.request.Request(
        f"{API_HOST}/api/v0/user/{USERNAME}{path}", method=method, headers={"Authorization": f"Token {token}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=API_TIMEOUT_SECONDS) as response:
            body: bytes = response.read()
            return json.loads(body) if body else {}
    except Exception as exc:  # the API is optional; touching the WSGI file already reloads
        print(f"(PythonAnywhere API {method} {path} skipped: {exc})")
        return None


def check_webapp_python(use_api: bool) -> None:
    mine: str = python_version()
    info: dict[str, Any] | None = api_call("GET", f"/webapps/{DOMAIN}/") if use_api else None
    web: str | None = info.get("python_version") if info else None
    if web is None:
        print(f"Packages go to Python {mine}. Make sure the Web tab shows Python {mine} too.")
    elif web != mine:
        fail(f"web app runs Python {web}, but this script runs {mine}. Run:  python{web} {Path(__file__).name}")
    else:
        print(f"web app Python {web} matches")


def reload_webapp(use_api: bool) -> None:
    if use_api and api_call("POST", f"/webapps/{DOMAIN}/reload/") is not None:
        print("web app reloaded through the API")
    else:
        print("web app reloads by itself within a minute (WSGI file changed); or press Reload on the Web tab")


def update_self(new_copy: Path) -> None:
    me: Path = Path(__file__).resolve()
    if new_copy.is_file() and new_copy.resolve() != me and new_copy.read_bytes() != me.read_bytes():
        shutil.copyfile(new_copy, me)
        print(f"updated {me} from the new version")


# ------------------------------------------------------------------ commands
def install(zip_path: Path, wsgi_dest: Path, use_api: bool, skip_pip: bool, target_override: Path | None) -> None:
    check_python()
    step(f"Checking {zip_path}")
    target: Path = resolve_target(check_zip(zip_path), target_override)
    check_www(wsgi_dest)
    staging: Path = sibling(target, STAGING_SUFFIX)
    backup: Path = sibling(target, BACKUP_SUFFIX)
    check_webapp_python(use_api)

    step(f"Unpacking to {staging}")
    unpack(zip_path, staging)

    step("Installing dependencies")
    if skip_pip:
        print("skipped (--skip-pip)")
    else:
        pip_install(staging / REQUIREMENTS)

    step("Smoke test of the new version")
    smoke_test(staging)

    step(f"Switching {target} to the new version")
    if backup.exists():
        shutil.rmtree(backup)
    if target.exists():
        save_live_wsgi(target, wsgi_dest)
        target.rename(backup)
        print(f"previous version -> {backup}")
    staging.rename(target)
    print(f"new version      -> {target}")

    step(f"Installing {WSGI_NAME}")
    install_wsgi(target / WSGI_NAME, wsgi_dest)
    reload_webapp(use_api)
    if target_override is None:  # on a trial run, never overwrite the script in your project
        update_self(target / INSTALLER_NAME)
        print(f"\nDone. Open https://{DOMAIN}")
    else:
        print(f"\nTrial install done in {target}. The real site was not touched.")
    if backup.exists():
        again: str = " ".join(sys.argv[1:]) if target_override is not None else ""
        print(f"Something wrong? Run:  python{python_version()} {Path(__file__).name} {again} --rollback".replace("  --", " --"))


def rollback(zip_path: Path, wsgi_dest: Path, use_api: bool, target_override: Path | None) -> None:
    step("Rolling back")
    server_home: str = check_zip(zip_path) if zip_path.is_file() else DEFAULT_SERVER_HOME
    target: Path = resolve_target(server_home, target_override)
    backup: Path = sibling(target, BACKUP_SUFFIX)
    swap: Path = sibling(target, SWAP_SUFFIX)
    if not backup.is_dir():
        fail(f"no backup at {backup}, nothing to roll back to.")
    check_www(wsgi_dest)
    if target.exists():
        save_live_wsgi(target, wsgi_dest)
        target.rename(swap)
    backup.rename(target)
    if swap.exists():
        swap.rename(backup)
    print(f"{target} <-> {backup} swapped (run --rollback again to undo)")
    source: Path = target / LIVE_WSGI_COPY
    if not source.is_file():
        source = target / WSGI_NAME
    if source.is_file():
        install_wsgi(source, wsgi_dest)
    else:
        print(f"no WSGI file stored with the old version; {wsgi_dest} left as is")
    reload_webapp(use_api)
    print("\nDone.")


def main(argv: Sequence[str] | None = None) -> None:
    here: Path = Path(__file__).resolve().parent
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description="Install beast.zip on PythonAnywhere")
    parser.add_argument("zip", nargs="?", type=Path, default=here / ZIP_NAME, help=f"default: {ZIP_NAME} next to this script")
    parser.add_argument("--rollback", action="store_true", help="swap back to the previous version")
    parser.add_argument("--skip-pip", action="store_true", help="do not run pip (dependencies unchanged)")
    parser.add_argument("--no-api", action="store_true", help="do not use the PythonAnywhere API even if API_TOKEN is set")
    parser.add_argument("--target", type=Path, default=None, help="trial run: install into this folder instead of SERVER_PROJECT_HOME")
    parser.add_argument("--www-dir", type=Path, default=WWW_DIR, help="trial run: folder that stands in for /var/www")
    args: argparse.Namespace = parser.parse_args(argv)
    wsgi_dest: Path = args.www_dir / WSGI_NAME
    target: Path | None = args.target.resolve() if args.target else None
    trial: bool = target is not None or args.www_dir != WWW_DIR
    use_api: bool = not args.no_api and not trial  # never reload the real site from a trial run
    if trial and args.www_dir != WWW_DIR:
        args.www_dir.mkdir(parents=True, exist_ok=True)
    if args.rollback:
        rollback(args.zip.resolve(), wsgi_dest, use_api, target)
    else:
        install(args.zip.resolve(), wsgi_dest, use_api, args.skip_pip, target)


if __name__ == "__main__":
    main()
