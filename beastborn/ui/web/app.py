"""Flask web frontend.

Run with ``python main.py`` (or ``flask --app beastborn.ui.web.app run``) and open http://127.0.0.1:8000.
Each game lives in a server-side session keyed by its id; the browser only draws the
returned state and sends intents (click / end_turn / cancel).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, TypeVar

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
    DEFAULT_MAX_SESSIONS,
    DEFAULT_TTL_SECONDS,
    GameSession,
    SessionStore,
    TooManySessions,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_BOT_STEPS = 10_000
SESSIONS_KEY = "beastborn.sessions"

M = TypeVar("M", bound=BaseModel)


class InvalidBody(HTTPException):
    """422 whose ``detail`` is pydantic's error list (same body shape the browser already reads)."""

    code = 422

    def __init__(self, detail: list[dict[str, Any]]):
        super().__init__()
        self.detail = detail


def create_app(store: SessionStore | None = None) -> Flask:
    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
    app.json.sort_keys = False  # keep the key order of render()
    if store is None:  # note: an empty store is falsy (it has __len__), so no "store or ..."
        store = SessionStore(
            ttl_seconds=float(os.environ.get("BEASTBORN_SESSION_TTL", DEFAULT_TTL_SECONDS)),
            max_sessions=int(os.environ.get("BEASTBORN_MAX_GAMES", DEFAULT_MAX_SESSIONS)),
        )
    app.extensions[SESSIONS_KEY] = store

    @app.errorhandler(HTTPException)
    def json_error(exc: HTTPException):
        detail = getattr(exc, "detail", None) or exc.description
        return jsonify(detail=detail), exc.code

    def parse(model: type[M]) -> M:
        data = request.get_json(silent=True)
        if data is None:
            raise InvalidBody([{"type": "missing", "loc": ["body"], "msg": "Field required", "input": None}])
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            errors = json.loads(exc.json(include_url=False))
            for error in errors:
                error["loc"] = ["body", *error["loc"]]
            raise InvalidBody(errors) from exc

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
        body = parse(IntentRequest)  # validate the body first: a bad body is 422 even for an unknown game
        session = get_session(game_id)
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


def run_bots(ui: Interaction) -> None:
    """Let non-human controllers play until a human is to move (no bots are configured yet)."""
    for _ in range(MAX_BOT_STEPS):
        if ui.view.phase is Phase.GAME_OVER or ui.human_turn:
            return
        ui.tick()


app = create_app()
