"""Flask web frontend.

Run with ``python main.py`` (or ``flask --app beastborn.ui.web.app run``) and open http://127.0.0.1:8000.
Each game lives in a server-side session keyed by its id; the browser only draws the
returned state and sends intents (click / end_turn). While a bot plays (WatchState) the browser
asks for one bot unit turn at a time (``POST /api/games/<id>/bot-step``) and shows it.
"""
from __future__ import annotations

import json
import os
import random
from collections.abc import Callable
from typing import Any, NoReturn, TypeVar

from flask import Flask, Response, abort, jsonify, render_template, request
from pydantic import BaseModel, ValidationError
from werkzeug.exceptions import HTTPException

from beastborn import constant as C
from beastborn.control.bot import BotPlayerController
from beastborn.control.controller import PlayerController
from beastborn.domain.board import Board
from beastborn.game.setup import new_game
from beastborn.game.state_machine import GameStateMachine
from beastborn.ui.interaction import Interaction
from beastborn.ui.interface import UIIntent
from beastborn.ui.web.intents import InvalidIntent, to_intent
from beastborn.ui.web.schemas import IntentRequest, NewGameRequest
from beastborn.ui.web.serializers import JSON, render
from beastborn.ui.web.sessions import GameSession, SessionStore, TooManySessions

M = TypeVar("M", bound=BaseModel)
JsonResponse = tuple[Response, int]
BotFactory = Callable[[random.Random], PlayerController]  # gets the bot's rng, seeded with the game seed


class InvalidBody(HTTPException):
    """422 whose ``detail`` is pydantic's error list (same body shape the browser already reads)."""

    code = 422

    def __init__(self, detail: list[dict[str, Any]]):
        super().__init__()
        self.detail: list[dict[str, Any]] = detail


def default_store() -> SessionStore:
    """Session limits from the environment (see gercio_eu_pythonanywhere_com_wsgi.py), else the defaults."""
    return SessionStore(
        ttl_seconds=float(os.environ.get(C.ENV_SESSION_TTL, C.DEFAULT_TTL_SECONDS)),
        max_sessions=int(os.environ.get(C.ENV_MAX_GAMES, C.DEFAULT_MAX_SESSIONS)),
    )


def parse_body(model: type[M]) -> M:
    """Validate the request's JSON body; errors become a 422 with pydantic's error list."""
    data: Any = request.get_json(silent=True)
    if data is None:
        raise InvalidBody([{"type": "missing", "loc": ["body"], "msg": "Field required", "input": None}])
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        errors: list[dict[str, Any]] = json.loads(exc.json(include_url=False))
        for error in errors:
            error["loc"] = ["body", *error["loc"]]
        raise InvalidBody(errors) from exc


def game_not_found() -> NoReturn:
    abort(404, description=C.GAME_NOT_FOUND)


def player_names(players: int, bots: list[int]) -> list[str]:
    """``Player 1``, ``Player 2 (Bot)``..."""
    return [
        (C.BOT_NAME_TEMPLATE if index in bots else C.PLAYER_NAME_TEMPLATE).format(number=index + 1)
        for index in range(players)
    ]


def default_bot(rng: random.Random) -> PlayerController:
    return BotPlayerController(rng=rng)


def create_app(store: SessionStore | None = None, bot_factory: BotFactory = default_bot) -> Flask:
    """``bot_factory(rng)`` makes the controller of each bot seat (tests pass a quicker bot).

    The bot's ``rng`` is seeded with the game seed, so the same seed replays the same bot moves.
    """
    app: Flask = Flask(
        __name__,
        static_folder=str(C.STATIC_DIR),
        static_url_path=C.STATIC_URL_PATH,
        template_folder=str(C.STATIC_DIR),  # index.html gets the name and version filled in
    )
    app.json.sort_keys = False  # type: ignore[attr-defined]  # keep the key order of render()
    if store is None:  # note: an empty store is falsy (it has __len__), so no "store or ..."
        store = default_store()
    sessions: SessionStore = store
    app.extensions[C.SESSIONS_KEY] = sessions

    @app.errorhandler(HTTPException)
    def json_error(exc: HTTPException) -> JsonResponse:
        detail: Any = getattr(exc, "detail", None) or exc.description
        return jsonify(detail=detail), exc.code or 500

    def get_session(game_id: str) -> GameSession:
        session: GameSession | None = sessions.get(game_id)
        if session is None:
            game_not_found()
        return session

    @app.get("/")
    def index() -> str:
        return render_template(
            C.INDEX_FILE, game_name=C.GAME_NAME, game_version=C.GAME_VERSION, game_title=C.GAME_TITLE
        )

    @app.post("/api/games")
    def create_game() -> tuple[JSON, int]:
        body: NewGameRequest = parse_body(NewGameRequest)
        gsm: GameStateMachine = new_game(
            body.players, seed=body.seed, width=body.size, height=body.size,
            player_names=player_names(body.players, body.bots),
        )
        seed: int | None = gsm.view().seed  # new_game() picks one when the request has none
        controllers: dict[int, PlayerController] = {seat: bot_factory(random.Random(seed)) for seat in body.bots}
        try:
            session: GameSession = sessions.create(gsm, controllers)
        except TooManySessions as exc:
            abort(503, description=str(exc))
        with session.lock:
            return render(session.id, session.interaction), 201

    @app.get("/api/games/<game_id>")
    def get_game(game_id: str) -> JSON:
        session: GameSession = get_session(game_id)
        with session.lock:
            return render(session.id, session.interaction)

    @app.post("/api/games/<game_id>/intents")
    def send_intent(game_id: str) -> JSON:
        body: IntentRequest = parse_body(IntentRequest)  # validate the body first: a bad body is 422 even for an unknown game
        session: GameSession = get_session(game_id)
        with session.lock:
            ui: Interaction = session.interaction
            board: Board = ui.view.board
            try:
                intent: UIIntent = to_intent(body, board.width, board.height)
            except InvalidIntent as exc:
                abort(422, description=str(exc))
            ui.handle(intent)  # ignored while a bot plays (WatchState)
            return render(session.id, ui)

    @app.post("/api/games/<game_id>/bot-step")
    def bot_step(game_id: str) -> JSON:
        """WatchState: the bot whose turn it is plays one unit turn. Does nothing on a human's turn or after the game."""
        session: GameSession = get_session(game_id)
        with session.lock:
            session.interaction.tick()
            return render(session.id, session.interaction)

    @app.delete("/api/games/<game_id>")
    def delete_game(game_id: str) -> tuple[str, int]:
        if not sessions.delete(game_id):
            game_not_found()
        return "", 204

    return app


app: Flask = create_app()
