"""Game-level settings (map generation and turn flow). Combat numbers live in RulesConfig."""
from __future__ import annotations

from dataclasses import dataclass

MIN_PLAYERS = 2
MAX_PLAYERS = 4


@dataclass(frozen=True)
class GameConfig:
    width: int = 12
    height: int = 12
    units_per_player: int = 4  # random recruits next to each Boss
    max_elevation: int = 2
    smooth_elevation: bool = True  # neighbouring tiles differ by at most 1 level (D8)
    hills_per_100_tiles: int = 4
    mud_patches_per_100_tiles: int = 4
    spawn_zone_size: int = 3  # flat grass square in each starting corner
    max_attacks_per_turn: int | None = None  # None = limited only by energy (D3)

    def __post_init__(self):
        if self.width < 2 * self.spawn_zone_size or self.height < 2 * self.spawn_zone_size:
            raise ValueError("Map too small for the spawn zones")
        if self.units_per_player + 1 > self.spawn_zone_size**2:
            raise ValueError("Boss + units must fit in the spawn zone")
