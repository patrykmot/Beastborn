# PLAN: Migrate the web backend from FastAPI to Flask

> Request (2026-10-08): migrate this project from FastAPI to Flask. **Just this change.**
>
> Builds on: `02_web_client_PLAN.md` (implemented, commit `2640f4e`).
> Status: **implemented** (F0–F5). 145 tests pass (142 before + 3 new). Every API response (success and error bodies, status codes, key order) was compared with the FastAPI version and is identical. The manual checklist was run in headless Chromium against `python main.py`. Nothing was committed to git.
> Deviations from the sketch: 422 bodies keep FastAPI's exact shape (`loc` prefixed with `"body"`, `ctx` included), and `send_intent` validates the body before looking up the game (exact FastAPI parity, so the 3.1 note about 404-vs-422 no longer applies). Flask 3.0.3 resolved Werkzeug 3.1.9 with no issues.
> Target version: **Flask 3.0.3** (exact pin).
> Scope: only the HTTP layer (`beastborn/ui/web/app.py`, `main.py`), dependencies, the tests that use the FastAPI test client, and the docs that name FastAPI/uvicorn.
> **No changes to** game logic, the browser page (`static/`), the JSON API contract, sessions or serializers.

---

## 0. Starting point (what touches FastAPI today)

| File | FastAPI / ASGI usage | In this plan |
|---|---|---|
| `beastborn/ui/web/app.py` | `FastAPI`, `HTTPException`, `Request`, `Response`, `status`, `FileResponse`, `StaticFiles`, `app.state.sessions`, body parsing via pydantic models | **Rewritten** as a Flask app factory. Same routes, status codes and JSON. |
| `main.py` | `uvicorn.run("beastborn.ui.web.app:app", ..., reload=, workers=1)` | **Changed** to `app.run(...)` (Werkzeug server). CLI flags stay the same. |
| `beastborn/ui/web/schemas.py` | pydantic `BaseModel` (`NewGameRequest`, `IntentRequest`) | **Unchanged.** pydantic works without FastAPI (see decision F1). |
| `beastborn/ui/web/intents.py` | takes `IntentRequest` | **Unchanged.** |
| `beastborn/ui/web/sessions.py`, `serializers.py` | none (plain Python, `threading.Lock`) | **Unchanged.** |
| `beastborn/ui/web/static/**` | JS reads errors as `responseJSON.detail` (string, or list with `.msg`) | **Unchanged.** The Flask app keeps the same `{"detail": ...}` error shape (decision F2). |
| `tests/test_web_api.py` | `fastapi.testclient.TestClient`, `response.json()` | **Changed** to Flask's `app.test_client()`, `response.json` (5 places). |
| `tests/test_architecture.py` | `WEB = ("fastapi", "starlette", "uvicorn", "pydantic")` | **Changed** to the Flask stack, plus a "no FastAPI anywhere" check. |
| `tests/test_ui.py::test_main_parses_arguments` | tests `--host/--port/--reload` | **Unchanged** (flags are kept). |
| `requirements.txt` | `fastapi`, `uvicorn[standard]`, `httpx` (TestClient only) | **Changed.** |
| `pyproject.toml` | `dependencies`, anyio `BlockingPortal` warning filter (Starlette TestClient) | **Changed.** |
| `README.md`, `docs/architecture.md`, `beastborn/ui/web/__init__.py` | mention FastAPI, uvicorn, `/docs` | **Text updates.** |
| `Prompts/02_web_client_PLAN.md` | historical plan | **Left as is** (it records what was done then). |

---

## 1. Decisions (proposed defaults)

| # | Question | Default | Alternative |
|---|---|---|---|
| F1 | Request validation | **Keep pydantic** and call `Model.model_validate(json)` by hand. `schemas.py` and `intents.py` stay byte-for-byte the same, and the 422 rules (players 2–4, size ∈ {10,12,16}, seed ≥ 0, x/y ≥ 0) stay identical. pydantic becomes an explicit dependency (it was transitive via FastAPI). | Hand-written validation in `schemas.py` and drop pydantic. More code, more risk, no benefit for this change. |
| F2 | Error body shape | **Keep `{"detail": "<text>"}`** for 404/503/422-from-`InvalidIntent`, and `{"detail": [ {loc, msg, type, ...} ]}` for pydantic errors, exactly like FastAPI. The JS `errorText()` keeps working with zero changes. | Flask's default HTML error pages. This breaks the toasts in the browser. |
| F3 | Server | **Werkzeug via `app.run(host, port, threaded=True, use_reloader=--reload)`**. One process, many threads, so in-memory sessions and per-game `threading.Lock` keep working as today. | `waitress` (`waitress-serve --threads=8 beastborn.ui.web.app:app`) if the startup "development server" warning bothers you. Also one process. **Never** a multi-process server (gunicorn with >1 worker): it would split the in-memory games. |
| F4 | Interactive API docs (`/docs`) | **Dropped.** FastAPI generated them; Flask does not. The API table in `README.md` is the documentation. | `flask-openapi3` / `apiflask`. Out of scope ("just this change"). |
| F5 | Test client | **Flask `app.test_client()`**. Responses expose `.status_code`, `.text` and `.json` (a property, not a method). `httpx` is no longer needed. | A small wrapper fixture that adds `.json()` so the test file does not change. Not worth it for 5 edits. |
| F6 | JSON key order | **`app.json.sort_keys = False`** so responses keep the same key order as today (Flask sorts keys by default). | Leave sorting on. The browser does not care, but diffs of responses would. |

---

## 2. FastAPI → Flask mapping

| FastAPI (now) | Flask (after) |
|---|---|
| `app = FastAPI(title=..., version=..., description=...)` | `app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")` |
| `app.state.sessions = store` | closure variable `store` inside `create_app()` (also `app.extensions["beastborn.sessions"] = store` for introspection) |
| `app.mount("/static", StaticFiles(directory=STATIC_DIR))` | built in via `static_folder` / `static_url_path` |
| `@app.get("/", include_in_schema=False)` + `FileResponse(index.html)` | `@app.get("/")` → `app.send_static_file("index.html")` |
| `@app.post(path, status_code=201)` | `@app.post(path)` → `return jsonify(...), 201` |
| path param `game_id: str` | `<game_id>` in the rule, function arg `game_id` |
| body `body: NewGameRequest` (auto 422) | `body = parse(NewGameRequest)` helper: `request.get_json(silent=True)` → `model_validate` → on `ValidationError` `abort` with 422 + `{"detail": exc.errors(...)}` |
| `raise HTTPException(404, "msg")` | `abort(404, description="msg")` + one JSON error handler |
| `Response(status_code=204)` | `return "", 204` |
| `return dict` (auto JSON) | `return render(...)` (Flask jsonifies dicts automatically) |
| `uvicorn.run(..., workers=1)` | `app.run(..., threaded=True)` |
| `fastapi.testclient.TestClient(app)` | `app.test_client()` |

---

## 3. Target code

### 3.1 `beastborn/ui/web/app.py` (sketch)

```python
"""Flask web frontend.

Run with ``python main.py`` (or ``flask --app beastborn.ui.web.app run``) and open http://127.0.0.1:8000.
Each game lives in a server-side session keyed by its id; the browser only draws the
returned state and sends intents (click / end_turn / cancel).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import TypeVar

from flask import Flask, abort, jsonify, request
from pydantic import BaseModel, ValidationError
from werkzeug.exceptions import HTTPException

from beastborn.game.config import GameConfig
from beastborn.game.setup import new_game
from beastborn.game.state import Phase
from beastborn.ui.interaction import Interaction
from beastborn.ui.web.intents import InvalidIntent, to_intent
from beastborn.ui.web.schemas import IntentRequest, NewGameRequest
from beastborn.ui.web.serializers import render
from beastborn.ui.web.sessions import (
    DEFAULT_MAX_SESSIONS, DEFAULT_TTL_SECONDS, GameSession, SessionStore, TooManySessions,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_BOT_STEPS = 10_000
M = TypeVar("M", bound=BaseModel)


class _Unprocessable(HTTPException):
    """422 carrying pydantic's error list, so the body matches FastAPI's."""
    code = 422

    def __init__(self, detail):
        super().__init__()
        self.detail = detail


def create_app(store: SessionStore | None = None) -> Flask:
    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
    app.json.sort_keys = False
    if store is None:  # note: an empty store is falsy (it has __len__), so no "store or ..."
        store = SessionStore(
            ttl_seconds=float(os.environ.get("BEASTBORN_SESSION_TTL", DEFAULT_TTL_SECONDS)),
            max_sessions=int(os.environ.get("BEASTBORN_MAX_GAMES", DEFAULT_MAX_SESSIONS)),
        )
    app.extensions["beastborn.sessions"] = store

    @app.errorhandler(HTTPException)
    def json_error(exc: HTTPException):
        detail = getattr(exc, "detail", None) or exc.description
        return jsonify(detail=detail), exc.code

    def parse(model: type[M]) -> M:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise _Unprocessable([{"loc": ["body"], "msg": "Body must be a JSON object", "type": "dict_type"}])
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            raise _Unprocessable(exc.errors(include_url=False, include_context=False)) from exc

    def get_session(game_id: str) -> GameSession:
        session = store.get(game_id)
        if session is None:
            abort(404, description="Game not found or expired")
        return session

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.post("/api/games")
    def create_game():
        body = parse(NewGameRequest)
        gsm = new_game(body.players, seed=body.seed, config=GameConfig(width=body.size, height=body.size))
        try:
            session = store.create(gsm)
        except TooManySessions as exc:
            abort(503, description=str(exc))
        with session.lock:
            return render(session.id, session.interaction), 201

    @app.get("/api/games/<game_id>")
    def get_game(game_id: str):
        session = get_session(game_id)
        with session.lock:
            return render(session.id, session.interaction)

    @app.post("/api/games/<game_id>/intents")
    def send_intent(game_id: str):
        session = get_session(game_id)
        body = parse(IntentRequest)
        with session.lock:
            ui = session.interaction
            board = ui.view.board
            try:
                intent = to_intent(body, board.width, board.height)
            except InvalidIntent as exc:
                abort(422, description=str(exc))
            ui.handle(intent)
            run_bots(ui)
            return render(session.id, ui)

    @app.delete("/api/games/<game_id>")
    def delete_game(game_id: str):
        if not store.delete(game_id):
            abort(404, description="Game not found or expired")
        return "", 204

    return app


def run_bots(ui: Interaction) -> None:  # unchanged
    ...


app = create_app()
```

Notes:
- **Order in `send_intent`:** today FastAPI validates the body *before* the handler runs, so an unknown game with a valid body → 404, and an invalid body → 422 even for an unknown game. The sketch looks up the session first, so `POST /api/games/nope/intents` with a bad body gives 404 instead of 422. No test or client depends on this; if exact parity is wanted, call `parse(...)` first.
- `render()` returns only plain types (str/int/bool/list/dict), checked in `serializers.py`, so Flask's JSON provider handles it without a custom encoder.
- Unknown routes and wrong methods (404/405) also come back as JSON `{"detail": ...}` through the same handler. That is fine for an API + one page.

### 3.2 `main.py`

```python
def main(argv=None) -> None:
    from beastborn.ui.web.app import create_app

    args = parse_args(argv)
    print(f"Beastborn running on http://{args.host}:{args.port}  (Ctrl+C to stop)")
    # One process on purpose: games are kept in this process's memory. Threads are fine (per-game locks).
    create_app().run(host=args.host, port=args.port, threaded=True, use_reloader=args.reload, debug=False)
```

`parse_args` does not change, so `test_main_parses_arguments` stays green. With `--reload`, Werkzeug starts a watcher process plus one serving child, so games still live in a single process (they are lost on each reload, same as uvicorn `--reload` today).

### 3.3 Dependencies

`requirements.txt`
```
flask==3.0.3
pydantic==2.13.5    # already installed today as a FastAPI dependency
pytest==7.4.2
```
Removed: `fastapi`, `uvicorn[standard]`, `httpx`.

Werkzeug is not pinned: pip picks it from Flask 3.0.3's own requirement (`Werkzeug>=3.0.0`). Everything the plan uses (`@app.get/post/delete`, `app.json.sort_keys`, `send_static_file`, `abort`, `test_client()`, `response.text` / `.json`) exists in Flask 3.0.3. If `pytest` shows a Flask/Werkzeug incompatibility, pin Werkzeug to the version `pip list` shows as working and note it here.

`pyproject.toml`
```toml
dependencies = ["flask==3.0.3", "pydantic==2.13.5"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
# removed: the anyio BlockingPortal filterwarnings line (it was for Starlette's TestClient)
```

Local venv clean-up:
```bash
pip uninstall -y fastapi starlette uvicorn httpx httptools uvloop watchfiles websockets python-dotenv
pip install -r requirements.txt
```
(Only uninstall what `pip list` actually shows; `uvloop` does not exist on Windows.)

---

## 4. Tests

| File | Change |
|---|---|
| `tests/test_web_api.py` | Docstring → "Flask test client". Remove `from fastapi.testclient import TestClient`. Fixture: `return create_app(store).test_client()`. Replace the 5 `response.json()` / `.get(...).json()` / `.post(...).json()` calls with `.json`. `.text` and `.status_code` stay the same. |
| `tests/test_architecture.py` | `WEB = ("flask", "werkzeug", "pydantic")`. Add `test_no_fastapi_anywhere()` (same shape as `test_no_pygame_anywhere`) forbidding `fastapi`, `starlette`, `uvicorn`, `httpx` in `beastborn/`. |
| everything else | unchanged |

Every existing assertion in `test_web_api.py` must pass unchanged, which proves API parity:
- 201 on create, 200 on get/intent, 204 on delete
- 404 for unknown game on GET / POST intents / DELETE
- 422 for `players` 1 or 5 or `"many"`, `size` 11, `seed` −1
- 422 for intent `type: "jump"`, click without x/y, x outside the board, x = −1
- 503 when `max_sessions` is reached
- `/` serves the page and every `/static/...` file it references returns 200

New tests (small):
- `test_error_bodies_are_json`: 404 body is `{"detail": "Game not found or expired"}`; a pydantic 422 body has `detail` as a non-empty list whose first item has `msg` (this is what `beastborn.js::errorText` reads).
- `test_non_json_body_is_422`: `POST /api/games` with `data="x"` and no JSON content type → 422 (FastAPI did the same).

---

## 5. Milestones

| # | Step | Done when |
|---|---|---|
| F0 | Dependencies: update `requirements.txt` and `pyproject.toml` (Flask **3.0.3**), clean and reinstall the venv. | `pip show flask` reports 3.0.3; `pip list` has no FastAPI/Starlette/uvicorn/httpx. |
| F1 | Rewrite `beastborn/ui/web/app.py` (section 3.1). | `python -c "from beastborn.ui.web.app import app"` works. |
| F2 | Update `main.py` (section 3.2). | `python main.py` serves http://127.0.0.1:8000; `--port 9000 --reload` works. |
| F3 | Update tests (section 4). | `python -m pytest` fully green (same count as before + the new tests). |
| F4 | Text updates (section 6). | `git grep -i -E "fastapi|uvicorn|starlette|httpx"` only matches `Prompts/02_web_client_PLAN.md` and this plan. |
| F5 | Manual check in the browser (section 7). | All items pass. |

---

## 6. Text / docs updates

- `README.md`
  - "Requires Python 3.10+, FastAPI and uvicorn" → "Requires Python 3.10+ and Flask".
  - Layout: `web/ FastAPI backend (...)` → `web/ Flask backend (...)`.
  - "Web API" section: remove the line "Interactive docs: `http://127.0.0.1:8000/docs`." (decision F4). Keep the table.
- `docs/architecture.md`
  - Diagram label `FastAPI  beastborn/ui/web` → `Flask  beastborn/ui/web`.
  - "FastAPI, Starlette, uvicorn and pydantic are only allowed in `ui/web`" → "Flask, Werkzeug and pydantic …".
  - "run a single uvicorn worker" → "run a single server process (threads are fine)".
  - "`create_app(store=None)` returns the FastAPI app" → "… returns the Flask app".
- `beastborn/ui/web/__init__.py` docstring: "FastAPI backend" → "Flask backend".
- `beastborn/ui/web/app.py` / `main.py` docstrings as in section 3.

---

## 7. Manual checklist (F5)

1. `python main.py` → open the page, choose 3 players / 16×16 / seed 7, press **Start**.
2. URL shows `/?game=<id>`; refreshing keeps the game.
3. Select a unit, hover shows path + cost, move works, energy drops.
4. Attack an adjacent enemy: hover shows exact damage, HP drops, log updates.
5. Click a far enemy: red toast with the "range" message (state unchanged).
6. **E** / Space ends the turn.
7. Open `/?game=nope` → "That game has expired or does not exist." toast, start screen shown.
8. Open a second tab, start another game; both games play independently.
9. Stop the server while the page is open, click a tile → "Cannot reach the server" toast.

---

## 8. Definition of Done

- `python -m pytest` is green, including the unchanged API assertions.
- `git diff --stat` touches **only**: `beastborn/ui/web/app.py`, `beastborn/ui/web/__init__.py`, `main.py`, `requirements.txt`, `pyproject.toml`, `tests/test_web_api.py`, `tests/test_architecture.py`, `README.md`, `docs/architecture.md` and this plan.
- **Not touched:** `beastborn/domain`, `engine`, `game`, `control`, `ui/interface.py`, `ui/interaction.py`, `ui/text.py`, `ui/web/schemas.py`, `ui/web/intents.py`, `ui/web/sessions.py`, `ui/web/serializers.py`, `ui/web/static/**`.
- No import of `fastapi`, `starlette`, `uvicorn` or `httpx` anywhere in the repo code or tests.
- Manual checklist passes.

---

## 9. Risks and notes

- **"Development server" warning.** Werkzeug prints it on start. For a local hot-seat game this is fine. If the game is ever exposed on a network, switch to `waitress` (single process, threaded), not a multi-worker server.
- **Threading.** Flask's server handles each request on its own thread, like FastAPI did for sync `def` routes (thread pool). `SessionStore._lock` and `GameSession.lock` already make this safe; nothing changes.
- **Sync only.** The current routes are all plain `def`, so nothing async is lost.
- **Small behaviour differences** (none used by the client or tests): `/api/games/` with a trailing slash returns 404 instead of a redirect; validation of an intent body for an unknown game returns 404 instead of 422 (see 3.1 notes); `/docs` and `/openapi.json` disappear.
- **Uncommitted work.** `beastborn/ui/web/static/index.html` currently has local changes. Commit or stash them separately so this migration's diff stays clean.
