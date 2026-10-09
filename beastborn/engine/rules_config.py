"""All numeric rules used by the standard Calculation Engine.

Movement names follow the game design document (EN_NT, EN_DT, ...).
Combat follows docs/game_mechanics.md (``resolve_attack`` calculation rules).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

from beastborn.domain.terrain import TerrainType


@dataclass(frozen=True)
class RulesConfig:
    # --- Movement (energy per tile; a unit's move_penalty is added to every step) ---
    EN_NT: int = 2  # normal terrain
    EN_DT: int = 3  # difficult terrain
    EN_MU: int = 2  # extra per elevation level gained (uphill)
    EN_MD_NORMAL: int = 2  # moving downhill into normal terrain
    EN_MD_DIFFICULT: int = 5  # moving downhill into difficult terrain (2 + 3 penalty)

    difficult_terrains: frozenset[TerrainType] = frozenset({TerrainType.MUD})

    # --- Combat (docs/game_mechanics.md) ---
    # 2. Momentum = ATK + momentum_per_level * ATK * Elevation_Modifier
    momentum_per_level: Fraction = Fraction(1, 2)
    # 3. Raw RP by distance, only for units with attack_range > 1. Farther = cannot attack.
    range_dissipation: dict[int, int] = field(default_factory=lambda: {1: 2, 2: 1, 3: 2, 4: 4})
    # 4. Effective DEF = floor(DEF / divisor) on these terrains
    terrain_defense_divisor: dict[TerrainType, int] = field(default_factory=lambda: {TerrainType.MUD: 2})
    # 5. Final damage = max(min_damage, floor(Base_Damage - Effective_DEF))
    min_damage: int = 0
    allow_block: bool = False  # if True, a hit that deals 0 damage applies no on-hit effects
    # 6. On-hit effects
    venom_duration: int = 5  # rounds; re-applying refreshes, never stacks
    venom_atk_divisor: int = 4  # Venom damage per round = max(1, floor(ATK / 4))
    acid_atk_divisor: int = 10  # Acid DEF shred = max(1, floor(0.1 * ATK)) = max(1, ATK // 10)

    # --- Energy ---
    starting_energy: int = 0  # D6: units start empty and regenerate at turn start

    def __hash__(self) -> int:  # dict fields make the default hash fail
        return id(self)
