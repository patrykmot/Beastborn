"""Read-only snapshots of the game for frontends and bots."""
from __future__ import annotations

from dataclasses import dataclass

from beastborn.domain.board import Board
from beastborn.domain.effects import StatusEffect
from beastborn.domain.position import Position
from beastborn.domain.unit import UnitStats
from beastborn.game.state import Phase


@dataclass(frozen=True)
class UnitView:
    id: int
    owner: int
    stats: UnitStats
    position: Position
    hp: int
    energy: int
    defense: int  # base DEF after Acid, before terrain
    current_defense: int  # DEF on the tile the unit stands on
    effects: tuple[StatusEffect, ...]

    @property
    def name(self) -> str:
        return self.stats.name

    @property
    def is_boss(self) -> bool:
        return self.stats.is_boss


@dataclass(frozen=True)
class PlayerView:
    index: int
    name: str
    eliminated: bool


@dataclass(frozen=True)
class GameView:
    board: Board  # immutable after generation
    units: tuple[UnitView, ...]
    players: tuple[PlayerView, ...]
    active_player: int
    round: int
    phase: Phase
    winner: int | None
    seed: int | None

    def unit(self, unit_id: int) -> UnitView | None:
        return next((u for u in self.units if u.id == unit_id), None)

    def unit_at(self, pos: Position) -> UnitView | None:
        return next((u for u in self.units if u.position == pos), None)

    def units_of(self, player: int) -> list[UnitView]:
        return [u for u in self.units if u.owner == player]
