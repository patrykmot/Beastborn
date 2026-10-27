"""Complete mutable game state. Owned and mutated only by GameStateMachine."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TypeVar

from beastborn.domain.board import Board
from beastborn.domain.player import Player
from beastborn.domain.position import Position
from beastborn.domain.unit import UnitState


_T = TypeVar("_T")


def shallow_copy(obj: _T) -> _T:
    """The same as ``copy.copy`` for these plain dataclasses (no ``__slots__``), several times faster.

    ``copy.copy`` goes through ``__reduce_ex__``; a GSM copies its state for every ``submit``, and bots submit a lot.
    """
    clone: _T = object.__new__(type(obj))  # type: ignore[call-overload]
    clone.__dict__.update(obj.__dict__)
    return clone


class Phase(Enum):
    SETUP = "setup"
    AWAITING_COMMAND = "awaiting_command"
    GAME_OVER = "game_over"


@dataclass
class GameState:
    board: Board
    players: list[Player]
    units: dict[int, UnitState]
    seed: int | None = None
    phase: Phase = Phase.SETUP
    active_player: int = 0  # owner of the active unit
    active_unit: int | None = None  # the unit whose turn it is (the Game State Machine picks it)
    round: int = 0
    winner: int | None = None
    acted_this_round: set[int] = field(default_factory=set)  # units that had (or have) their turn this round
    attacks_this_turn: int = 0  # attacks made by the active unit in its current turn
    actions_this_turn: int = 0  # moves and attacks made by the active unit in its current turn
    fallen: list[UnitState] = field(default_factory=list)  # units removed from the board (killed, or their Boss died)

    def unit_at(self, pos: Position) -> UnitState | None:
        for unit in self.units.values():
            if unit.position == pos:
                return unit
        return None

    def occupied(self) -> set[Position]:
        return {u.position for u in self.units.values()}

    def sorted_units(self) -> list[UnitState]:
        """All units, ordered by id (the order units act and are shown in)."""
        return sorted(self.units.values(), key=lambda u: u.id)

    def units_of(self, player: int) -> list[UnitState]:
        return [u for u in self.sorted_units() if u.owner == player]

    def alive_players(self) -> list[Player]:
        return [p for p in self.players if not p.eliminated]

    def copy(self) -> GameState:
        """An independent copy: changing one never changes the other. The board never changes, so it is shared.

        A new mutable field must be copied here too.
        """
        clone: GameState = shallow_copy(self)
        clone.players = [shallow_copy(p) for p in self.players]
        clone.units = {unit_id: shallow_copy(u) for unit_id, u in self.units.items()}
        clone.fallen = [shallow_copy(u) for u in self.fallen]
        clone.acted_this_round = set(self.acted_this_round)
        return clone
