"""WSGI entry point for PythonAnywhere (https://gercio.eu.pythonanywhere.com).

On PythonAnywhere the web app's WSGI file (/var/www/gercio_eu_pythonanywhere_com_wsgi.py,
"WSGI configuration file" on the Web tab) must contain this code. PythonAnywhere imports it
and serves the WSGI callable named ``application``; you do not start it yourself.
After any change: Web tab -> Reload.

You can also run it directly to smoke-test the production setup with Flask's own server:

    python gercio_eu_pythonanywhere_com_wsgi.py [--host 0.0.0.0] [--port 8000]

For everyday local development keep using ``python main.py``.
"""
import os  # noqa: F401  (used by the optional settings below)
import sys
from pathlib import Path

# Folder that contains main.py and the beastborn/ package on the server.
# Change it if you upload the project somewhere else.
SERVER_PROJECT_HOME = "/home/gercio/mysite"


def _project_home() -> str:
    """This file's own folder when it sits in the project (local run), else the server folder."""
    here = Path(__file__).resolve().parent
    if (here / "beastborn" / "__init__.py").is_file():
        return str(here)
    return SERVER_PROJECT_HOME


project_home = _project_home()
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# Game limits (seconds a game may stay idle, max games kept in memory).
# Defaults come from beastborn/ui/web/sessions.py; uncomment to override on the server.
# os.environ.setdefault("BEASTBORN_SESSION_TTL", "1800")
# os.environ.setdefault("BEASTBORN_MAX_GAMES", "100")

# PythonAnywhere looks for a WSGI callable called "application".
from beastborn.ui.web.app import app as application  # noqa: E402

if __name__ == "__main__":
    from main import main

    main()
