"""Unit definitions (static stats) and unit state (mutable, owned by the GSM)."""
from __future__ import annotations

from dataclasses import dataclass, field

from beastborn.constance import DEFAULT_ATTACK_RANGE, DEFAULT_MOVE_PENALTY, MELEE_RANGE
from beastborn.domain.effects import EffectSpec, StatusEffect
from beastborn.domain.position import Position


@dataclass(frozen=True)
class UnitStats:
    """Static definition of a unit type (loaded from data/units.json)."""

    key: str
    name: str
    code: str  # one letter used by simple renderers
    hp: int  # HP
    atk: int  # ATK
    en_atk: int  # EN_ATK - energy spent per attack
    defense: int  # DEF
    max_en: int  # MAX_EN
    reg_en: int  # REG_EN
    attack_range: int = DEFAULT_ATTACK_RANGE
    move_penalty: int = DEFAULT_MOVE_PENALTY  # extra EN per step (slow units)
    is_boss: bool = False
    on_hit: tuple[EffectSpec, ...] = ()

    @property
    def is_ranged(self) -> bool:
        """Ranged units suffer Range Dissipation (docs/game_mechanics.md, section 3)."""
        return self.attack_range > MELEE_RANGE


@dataclass
class UnitState:
    """A unit on the battlefield. Only the Game State Machine mutates this."""

    id: int
    owner: int  # player index
    stats: UnitStats
    position: Position
    hp: int
    energy: int
    defense: int  # current base DEF (can be reduced by Acid)
    effects: tuple[StatusEffect, ...] = field(default_factory=tuple)

    @property
    def is_boss(self) -> bool:
        return self.stats.is_boss

    @property
    def alive(self) -> bool:
        return self.hp > 0
