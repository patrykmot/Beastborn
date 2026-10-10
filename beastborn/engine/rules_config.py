"""All numeric rules used by the standard Calculation Engine.

Movement names follow the game design document (EN_NT, EN_DT, ...).
Combat follows docs/game_mechanics.md (``resolve_attack`` calculation rules).
Default values live in ``beastborn.constance``; override them per game by passing fields here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

from beastborn import constance as C
from beastborn.domain.terrain import TerrainType


@dataclass(frozen=True)
class RulesConfig:
    # --- Movement (energy per tile; a unit's move_penalty is added to every step) ---
    EN_NT: int = C.EN_NT  # normal terrain
    EN_DT: int = C.EN_DT  # difficult terrain
    EN_MU: int = C.EN_MU  # extra per elevation level gained (uphill)
    EN_MD_NORMAL: int = C.EN_MD_NORMAL  # moving downhill into normal terrain
    EN_MD_DIFFICULT: int = C.EN_MD_DIFFICULT  # moving downhill into difficult terrain

    difficult_terrains: frozenset[TerrainType] = frozenset({TerrainType.MUD})

    # --- Combat (docs/game_mechanics.md) ---
    # 2. Momentum = ATK + momentum_per_level * ATK * Elevation_Modifier
    momentum_per_level: Fraction = Fraction(C.MOMENTUM_PER_LEVEL_NUMERATOR, C.MOMENTUM_PER_LEVEL_DENOMINATOR)
    # 3. RP damage multiplier by distance, only for units with attack_range > 1. Farther = cannot attack.
    range_dissipation: dict[int, float] = field(default_factory=lambda: dict(C.RANGE_DISSIPATION))
    # 4. Effective DEF = floor(DEF / divisor) on these terrains
    terrain_defense_divisor: dict[TerrainType, int] = field(
        default_factory=lambda: {TerrainType.MUD: C.MUD_DEFENSE_DIVISOR}
    )
    # 5. Final damage = max(min_damage, floor(Base_Damage - Effective_DEF)); every hit deals at least 1
    min_damage: int = C.MIN_DAMAGE
    allow_block: bool = C.ALLOW_BLOCK  # if True, a hit with floor(Base_Damage - DEF) <= 0 deals 0, no effects
    # 6. On-hit effects
    venom_duration: int = C.VENOM_DURATION  # rounds; re-applying refreshes, never stacks
    venom_atk_divisor: int = C.VENOM_ATK_DIVISOR  # Venom damage per round = max(1, floor(ATK / 4))
    acid_atk_divisor: int = C.ACID_ATK_DIVISOR  # Acid DEF shred = max(1, floor(0.1 * ATK)) = max(1, ATK // 10)
    min_effect_magnitude: int = C.MIN_EFFECT_MAGNITUDE  # the "max(1, ...)" of both effects

    # --- Energy ---
    starting_energy: int = C.STARTING_ENERGY  # D6: units start empty and regenerate at turn start

    def __hash__(self) -> int:  # dict fields make the default hash fail
        return id(self)
