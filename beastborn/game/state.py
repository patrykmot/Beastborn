"""Complete mutable game state. Owned and mutated only by GameStateMachine."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from beastborn.domain.board import Board
from beastborn.domain.player import Player
from beastborn.domain.position import Position
from beastborn.domain.unit import UnitState


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
