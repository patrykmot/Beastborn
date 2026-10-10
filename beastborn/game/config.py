"""Game-level settings (map generation and turn flow). Combat numbers live in RulesConfig."""
from __future__ import annotations

from dataclasses import dataclass

from beastborn import constance as C


@dataclass(frozen=True)
class GameConfig:
    width: int = C.DEFAULT_MAP_SIZE
    height: int = C.DEFAULT_MAP_SIZE
    units_per_player: int = C.DEFAULT_UNITS_PER_PLAYER  # random recruits next to each Boss
    max_elevation: int = C.DEFAULT_MAX_ELEVATION
    smooth_elevation: bool = C.DEFAULT_SMOOTH_ELEVATION  # neighbouring tiles differ by at most 1 level (D8)
    hills_per_100_tiles: int = C.DEFAULT_HILLS_PER_100_TILES
    mud_patches_per_100_tiles: int = C.DEFAULT_MUD_PATCHES_PER_100_TILES
    spawn_zone_size: int = C.DEFAULT_SPAWN_ZONE_SIZE  # flat grass square in each starting corner
    max_attacks_per_turn: int | None = C.DEFAULT_MAX_ATTACKS_PER_TURN  # None = limited only by energy (D3)

    def __post_init__(self) -> None:
        if self.width < 2 * self.spawn_zone_size or self.height < 2 * self.spawn_zone_size:
            raise ValueError("Map too small for the spawn zones")
        if self.units_per_player + 1 > self.spawn_zone_size**2:
            raise ValueError("Boss + units must fit in the spawn zone")
