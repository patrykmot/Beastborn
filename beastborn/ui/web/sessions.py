"""In-memory game sessions: one Interaction (and so one GameStateMachine) per game id.

Sessions live in the server process only: run a single worker; a restart drops all games.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from beastborn.constance import DEFAULT_MAX_SESSIONS, DEFAULT_TTL_SECONDS
from beastborn.game.state_machine import GameStateMachine
from beastborn.ui.interaction import Interaction


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
        self.ttl_seconds: float = ttl_seconds
        self.max_sessions: int = max_sessions
        self._clock: Callable[[], float] = clock
        self._sessions: dict[str, GameSession] = {}
        self._lock: threading.Lock = threading.Lock()

    def create(self, gsm: GameStateMachine) -> GameSession:
        with self._lock:
            now: float = self._clock()
            self._purge_expired(now)
            if len(self._sessions) >= self.max_sessions:
                raise TooManySessions(f"Server already runs {self.max_sessions} games")
            session: GameSession = GameSession(uuid.uuid4().hex, Interaction(gsm), created_at=now, last_access=now)
            self._sessions[session.id] = session
            return session

    def get(self, game_id: str) -> GameSession | None:
        with self._lock:
            session: GameSession | None = self._sessions.get(game_id)
            if session is None:
                return None
            now: float = self._clock()
            if self._expired(session, now):
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

    def _expired(self, session: GameSession, now: float) -> bool:
        return now - session.last_access > self.ttl_seconds

    def _purge_expired(self, now: float) -> None:
        expired: list[str] = [key for key, s in self._sessions.items() if self._expired(s, now)]
        for key in expired:
            del self._sessions[key]
