"""In-memory game sessions: one Interaction (and so one GameStateMachine) per game id.

Sessions live in the server process only: run a single worker; a restart drops all games.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from beastborn.game.state_machine import GameStateMachine
from beastborn.ui.interaction import Interaction

DEFAULT_TTL_SECONDS = 30 * 60
DEFAULT_MAX_SESSIONS = 100


class TooManySessions(RuntimeError):
    pass


@dataclass
class GameSession:
    id: str
    interaction: Interaction
    created_at: float
    last_access: float
    lock: threading.Lock = field(default_factory=threading.Lock)


class SessionStore:
    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self._clock = clock
        self._sessions: dict[str, GameSession] = {}
        self._lock = threading.Lock()

    def create(self, gsm: GameStateMachine) -> GameSession:
        with self._lock:
            self._purge_expired()
            if len(self._sessions) >= self.max_sessions:
                raise TooManySessions(f"Server already runs {self.max_sessions} games")
            now = self._clock()
            session = GameSession(uuid.uuid4().hex, Interaction(gsm), created_at=now, last_access=now)
            self._sessions[session.id] = session
            return session

    def get(self, game_id: str) -> GameSession | None:
        with self._lock:
            session = self._sessions.get(game_id)
            if session is None:
                return None
            now = self._clock()
            if now - session.last_access > self.ttl_seconds:
                del self._sessions[game_id]
                return None
            session.last_access = now
            return session

    def delete(self, game_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(game_id, None) is not None

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)

    def _purge_expired(self) -> None:
        now = self._clock()
        expired = [k for k, s in self._sessions.items() if now - s.last_access > self.ttl_seconds]
        for key in expired:
            del self._sessions[key]
