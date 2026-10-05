"""Complete mutable game state. Owned and mutated only by GameStateMachine."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from beastborn.domain.board import Board
from beastborn.domain.player import Player
from beastborn.domain.position import Position
from beastborn.domain.unit import UnitState
from beastborn.game.config import GameConfig


class Phase(Enum):
    SETUP = "setup"
    AWAITING_COMMAND = "awaiting_command"
    GAME_OVER = "game_over"


@dataclass
class GameState:
    board: Board
    players: list[Player]
    units: dict[int, UnitState]
    config: GameConfig = field(default_factory=GameConfig)
    seed: int | None = None
    phase: Phase = Phase.SETUP
    active_player: int = 0
    round: int = 0
    winner: int | None = None
    attacks_this_turn: dict[int, int] = field(default_factory=dict)

    def unit_at(self, pos: Position) -> UnitState | None:
        for unit in self.units.values():
            if unit.position == pos:
                return unit
        return None

    def occupied(self) -> set[Position]:
        return {u.position for u in self.units.values()}

    def units_of(self, player: int) -> list[UnitState]:
        return sorted((u for u in self.units.values() if u.owner == player), key=lambda u: u.id)

    def alive_players(self) -> list[Player]:
        return [p for p in self.players if not p.eliminated]
