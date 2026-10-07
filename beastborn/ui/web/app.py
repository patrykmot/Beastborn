"""FastAPI web frontend.

Run with ``python main.py`` (or ``uvicorn beastborn.ui.web.app:app``) and open http://127.0.0.1:8000.
Each game lives in a server-side session keyed by its id; the browser only draws the
returned state and sends intents (click / end_turn / cancel).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

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


def create_app(store: SessionStore | None = None) -> FastAPI:
    app = FastAPI(title="Beastborn", version="0.2.0", description="Turn-based beast tactics - web API")
    if store is None:  # note: an empty store is falsy (it has __len__), so no "store or ..."
        store = SessionStore(
            ttl_seconds=float(os.environ.get("BEASTBORN_SESSION_TTL", DEFAULT_TTL_SECONDS)),
            max_sessions=int(os.environ.get("BEASTBORN_MAX_GAMES", DEFAULT_MAX_SESSIONS)),
        )
    app.state.sessions = store
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    def sessions(request: Request) -> SessionStore:
        return request.app.state.sessions

    def get_session(request: Request, game_id: str) -> GameSession:
        session = sessions(request).get(game_id)
        if session is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Game not found or expired")
        return session

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.post("/api/games", status_code=status.HTTP_201_CREATED)
    def create_game(body: NewGameRequest, request: Request) -> dict[str, Any]:
        gsm = new_game(body.players, seed=body.seed, config=GameConfig(width=body.size, height=body.size))
        try:
            session = sessions(request).create(gsm)
        except TooManySessions as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
        with session.lock:
            return render(session.id, session.interaction)

    @app.get("/api/games/{game_id}")
    def get_game(game_id: str, request: Request) -> dict[str, Any]:
        session = get_session(request, game_id)
        with session.lock:
            return render(session.id, session.interaction)

    @app.post("/api/games/{game_id}/intents")
    def send_intent(game_id: str, body: IntentRequest, request: Request) -> dict[str, Any]:
        session = get_session(request, game_id)
        with session.lock:
            ui = session.interaction
            board = ui.view.board
            try:
                intent = to_intent(body, board.width, board.height)
            except InvalidIntent as exc:
                raise HTTPException(422, str(exc)) from exc
            ui.handle(intent)
            run_bots(ui)
            return render(session.id, ui)

    @app.delete("/api/games/{game_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_game(game_id: str, request: Request) -> Response:
        if not sessions(request).delete(game_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Game not found or expired")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return app


def run_bots(ui: Interaction) -> None:
    """Let non-human controllers play until a human is to move (no bots are configured yet)."""
    for _ in range(MAX_BOT_STEPS):
        if ui.view.phase is Phase.GAME_OVER or ui.human_turn:
            return
        ui.tick()


app = create_app()
