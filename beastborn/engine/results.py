"""Value objects passed between the Calculation Engine and the Game State Machine."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from beastborn.domain.effects import EffectKind, StatusEffect
from beastborn.domain.position import Position
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
    position: Position


def fmt(value: Fraction | int) -> str:
    """3 -> '3', 9/2 -> '4.5', 7/3 -> '2.33'."""
    if Fraction(value).denominator == 1:
        return str(int(value))
    return f"{float(value):.2f}".rstrip("0").rstrip(".")


@dataclass(frozen=True)
class AttackResult:
    """Every step of docs/game_mechanics.md, so frontends can show the whole calculation."""

    atk: int  # attacker ATK
    elevation_modifier: int  # EM = attacker elevation - defender elevation (levels)
    momentum: Fraction  # ATK + 0.5 * ATK * EM
    distance: int  # Manhattan distance to the target
    ranged: bool  # attacker has attack_range > 1, so Range Dissipation applies
    range_divisor: int  # Effective RP (1 for melee units)
    base_damage: Fraction  # momentum / Effective RP
    current_defense: int  # Effective DEF (after terrain)
    raw_damage: Fraction  # base_damage - Effective DEF, before rounding
    damage: int  # HP the defender loses
    blocked: bool  # the hit has no effect at all (out of range, or allow_block and 0 damage)
    defense_change: int  # <= 0, e.g. -1 from Acid (already clamped so DEF stays >= 0)
    defender_effects_after: tuple[StatusEffect, ...]
    out_of_range: bool = False

    def formula(self) -> str:
        """E.g. '6 - 2 = 4', '6 x1.5 - 2 = 7', '7 x1.5 /3 - 2 = 1.5 -> 1'."""
        if self.out_of_range:
            return "out of range"
        text = str(self.atk)
        if self.elevation_modifier and self.atk:
            text += f" x{fmt(self.momentum / self.atk)}"  # height bonus or penalty
        if self.ranged:
            text += f" /{self.range_divisor}"
        text += f" - {self.current_defense} = {fmt(self.raw_damage)}"
        if self.blocked:
            return text + " -> blocked"
        if self.damage != self.raw_damage:
            return text + f" -> {self.damage}"
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
