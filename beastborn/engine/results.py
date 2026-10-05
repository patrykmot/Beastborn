"""Value objects passed between the Calculation Engine and the Game State Machine."""
from __future__ import annotations

from dataclasses import dataclass

from beastborn.domain.effects import EffectKind, StatusEffect
from beastborn.domain.terrain import Tile
from beastborn.domain.unit import UnitStats


@dataclass(frozen=True)
class CombatantSnapshot:
    """Everything the CE needs to know about one side of a fight."""

    stats: UnitStats
    hp: int
    defense: int  # current base DEF (after Acid), before terrain
    effects: tuple[StatusEffect, ...]
    tile: Tile


@dataclass(frozen=True)
class AttackResult:
    base_damage: int  # ATK * EN_ATK
    elevation_modifier: int  # EM
    current_defense: int  # DEF after terrain penalty
    raw_damage: int  # base + EM - current DEF (before RL_1)
    damage: int  # HP the defender loses
    blocked: bool
    defense_change: int  # <= 0, e.g. -1 from Acid (already clamped so DEF stays >= 0)
    defender_effects_after: tuple[StatusEffect, ...]

    def formula(self) -> str:
        text = f"{self.base_damage} {self.elevation_modifier:+d} - {self.current_defense} = {self.raw_damage}"
        if self.blocked:
            return text + " -> blocked"
        if self.damage != self.raw_damage:
            return text + f" -> {self.damage} (min)"
        return text


@dataclass(frozen=True)
class EffectTick:
    kind: EffectKind
    amount: int


@dataclass(frozen=True)
class TickResult:
    hp_change: int  # <= 0
    defense_change: int  # <= 0
    remaining_effects: tuple[StatusEffect, ...]
    ticks: tuple[EffectTick, ...]
