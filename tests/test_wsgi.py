"""PythonAnywhere WSGI entry point (gercio_eu_pythonanywhere_com_wsgi.py)."""
import importlib.util
import shutil
import sys
from pathlib import Path

from flask import Flask

ROOT = Path(__file__).resolve().parent.parent
WSGI_FILE = ROOT / "gercio_eu_pythonanywhere_com_wsgi.py"


def load(path: Path, monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))  # undo the module's sys.path change afterwards
    spec = importlib.util.spec_from_file_location(f"wsgi_under_test_{abs(hash(path))}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exposes_wsgi_application_that_serves_the_game(monkeypatch):
    module = load(WSGI_FILE, monkeypatch)
    assert isinstance(module.application, Flask)
    assert module.project_home == str(ROOT)
    client = module.application.test_client()
    assert client.get("/").status_code == 200
    assert client.post("/api/games", json={"players": 2, "seed": 1}).status_code == 201


def test_uses_server_folder_when_file_lives_outside_the_project(tmp_path, monkeypatch):
    """On PythonAnywhere the file sits in /var/www, not next to beastborn/."""
    copy = tmp_path / WSGI_FILE.name
    shutil.copy(WSGI_FILE, copy)
    module = load(copy, monkeypatch)
    assert module.project_home == module.SERVER_PROJECT_HOME == "/home/gercio/mysite"
    assert module.project_home in sys.path
