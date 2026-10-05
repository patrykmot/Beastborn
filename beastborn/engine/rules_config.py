"""All numeric rules used by the standard Calculation Engine.

Names follow the game design document (EN_NT, EN_DT, ...).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from beastborn.domain.terrain import TerrainType


@dataclass(frozen=True)
class RulesConfig:
    # --- Movement (energy per tile) ---
    EN_NT: int = 2  # normal terrain
    EN_DT: int = 3  # difficult terrain
    EN_MU: int = 2  # extra per elevation level gained (uphill)
    EN_MD_NORMAL: int = 2  # moving downhill into normal terrain
    EN_MD_DIFFICULT: int = 5  # moving downhill into difficult terrain (2 + 3 penalty)

    difficult_terrains: frozenset[TerrainType] = frozenset({TerrainType.MUD})

    # --- Combat ---
    elevation_attack_bonus: int = 2  # EM per level when attacking from above
    uphill_attack_penalty: bool = False  # optional mechanic (D9)
    uphill_penalty_per_level: int = 1  # EM per level when attacking from below (if enabled)
    terrain_defense_penalty: dict[TerrainType, int] = field(
        default_factory=lambda: {TerrainType.MUD: 2}
    )
    min_damage: int = 1  # RL_1
    allow_block: bool = False  # RL_1: if True, DMG <= 0 is a block (0 damage, no effects)

    # --- Energy ---
    starting_energy: int = 0  # D6: units start empty and regenerate at turn start

    def __hash__(self) -> int:  # dict field makes the default hash fail
        return id(self)
