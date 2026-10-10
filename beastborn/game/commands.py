"""Commands: the only way the UI or a bot can change the game.

Commands always act on behalf of the active player.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from beastborn.domain.position import Position
from beastborn.game.events import GameEvent


@dataclass(frozen=True)
class MoveCommand:
    unit_id: int
    destination: Position


@dataclass(frozen=True)
class AttackCommand:
    attacker_id: int
    target_id: int


@dataclass(frozen=True)
class EndTurnCommand:
    pass


Command = MoveCommand | AttackCommand | EndTurnCommand


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    events: tuple[GameEvent, ...] = field(default_factory=tuple)
    error: str | None = None

    @classmethod
    def rejected(cls, reason: str) -> CommandResult:
        return cls(ok=False, error=reason)
