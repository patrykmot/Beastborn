"""Commands: the only way the UI or a bot can change the game.

Commands always act on behalf of the active player. Every command is a ``Command`` with a ``kind``
(``CommandType``), so code that handles commands can dispatch on the kind instead of on the class.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar

from beastborn.domain.position import Position
from beastborn.game.events import GameEvent


class CommandType(Enum):
    MOVE = "move"
    ATTACK = "attack"
    END_TURN = "end_turn"
    WRAPPER = "wrapper"


@dataclass(frozen=True)
class Command:
    """Base class of every command. Commands are immutable values (they are compared and stored in the log)."""

    kind: ClassVar[CommandType]


@dataclass(frozen=True)
class MoveCommand(Command):
    kind: ClassVar[CommandType] = CommandType.MOVE

    unit_id: int
    destination: Position


@dataclass(frozen=True)
class AttackCommand(Command):
    """Attack ``target_id``. If it is out of range, the attacker first moves to a tile it can attack from.

    ``move_to`` = None: the Game State Machine picks that tile (see ``GameStateMachine.plan_attack``).
    ``move_to`` = a tile: move exactly there, then attack (the attacker's own tile = attack without moving).
    The command log keeps the tile that was used, so a replay never depends on how the tile was picked.
    """

    kind: ClassVar[CommandType] = CommandType.ATTACK

    attacker_id: int
    target_id: int
    move_to: Position | None = None


@dataclass(frozen=True)
class EndTurnCommand(Command):
    kind: ClassVar[CommandType] = CommandType.END_TURN


@dataclass(frozen=True)
class WrapperCommand(Command):
    """Several commands of one unit's turn, submitted in order as one command (e.g. attack, attack, move, end turn).

    Rules (checked before anything runs): at least one command, no nested ``WrapperCommand``,
    ``EndTurnCommand`` only as the last one. The commands run one by one; if one is rejected the rest is
    skipped, the ones that already ran stay done and the command log keeps a ``WrapperCommand`` of just those.
    A list is accepted and stored as a tuple.
    """

    kind: ClassVar[CommandType] = CommandType.WRAPPER

    commands: tuple[Command, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.commands, tuple):
            commands: Sequence[Command] = self.commands
            object.__setattr__(self, "commands", tuple(commands))


@dataclass(frozen=True)
class CommandResult:
    """``ok`` = the command was accepted. A rejected ``WrapperCommand`` may still carry the events of the
    commands it ran before the rejected one; any other rejected command has no events and changed nothing.
    """

    ok: bool
    events: tuple[GameEvent, ...] = field(default_factory=tuple)
    error: str | None = None

    @classmethod
    def rejected(cls, reason: str, events: tuple[GameEvent, ...] = ()) -> CommandResult:
        return cls(ok=False, events=events, error=reason)
