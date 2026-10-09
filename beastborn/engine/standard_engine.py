"""The standard Calculation Engine.

Movement follows the game design document. Combat follows docs/game_mechanics.md
(``resolve_attack`` calculation rules); the section numbers below refer to that file.
All maths is exact (``Fraction``); only the final damage is rounded down.
"""
from __future__ import annotations

import math
from fractions import Fraction

from beastborn.domain.effects import EffectKind, StatusEffect
from beastborn.domain.terrain import Tile
from beastborn.domain.unit import UnitStats
from beastborn.engine.interface import CalculationEngine
from beastborn.engine.results import AttackResult, CombatantSnapshot, EffectTick, TickResult
from beastborn.engine.rules_config import RulesConfig


class StandardCalculationEngine(CalculationEngine):
    def __init__(self, config: RulesConfig | None = None):
        self.config = config or RulesConfig()

    # ---------- helpers ----------
    def _is_difficult(self, tile: Tile) -> bool:
        return tile.terrain in self.config.difficult_terrains

    @staticmethod
    def _is_ranged(unit: UnitStats) -> bool:
        return unit.attack_range > 1

    # ---------- Movement ----------
    def step_cost(self, unit: UnitStats, src: Tile, dst: Tile) -> int:
        c = self.config
        difficult = self._is_difficult(dst)
        if dst.elevation < src.elevation:  # downhill
            base = c.EN_MD_DIFFICULT if difficult else c.EN_MD_NORMAL
        else:
            levels_up = dst.elevation - src.elevation  # 0 on level ground
            base = (c.EN_DT if difficult else c.EN_NT) + c.EN_MU * levels_up
        return base + unit.move_penalty

    # ---------- Energy ----------
    def initial_energy(self, unit: UnitStats) -> int:
        return min(unit.max_en, self.config.starting_energy)

    def regenerate(self, unit: UnitStats, current_energy: int) -> int:
        return min(unit.max_en, current_energy + unit.reg_en)

    def attack_cost(self, attacker: UnitStats) -> int:
        return attacker.en_atk  # 1. Energy_remaining = Energy_current - en_atk

    def in_attack_range(self, attacker: UnitStats, distance: int) -> bool:
        if not 1 <= distance <= attacker.attack_range:
            return False
        # 3. "> 4 tiles": a ranged attack cannot be performed at all (and costs no energy).
        return not self._is_ranged(attacker) or distance in self.config.range_dissipation

    # ---------- Combat ----------
    def elevation_modifier(self, attacker_tile: Tile, defender_tile: Tile) -> int:
        # 2. Elevation_Modifier = Elev_attacker - Elev_defender
        return attacker_tile.elevation - defender_tile.elevation

    def current_defense(self, defense: int, tile: Tile) -> int:
        # 4. GRASS: DEF; MUD: floor(DEF / 2)
        divisor = self.config.terrain_defense_divisor.get(tile.terrain, 1)
        return max(0, defense // divisor)

    def range_multiplier(self, attacker: UnitStats, distance: int) -> Fraction | None:
        # 3. RP multiplier by distance (ranged units only); None = cannot attack that far
        if not self._is_ranged(attacker):
            return Fraction(1)
        rp = self.config.range_dissipation.get(distance)
        return None if rp is None else Fraction(str(rp))  # str: 0.8 -> exactly 4/5

    def resolve_attack(self, attacker: CombatantSnapshot, defender: CombatantSnapshot) -> AttackResult:
        c = self.config
        atk = attacker.stats.atk
        em = self.elevation_modifier(attacker.tile, defender.tile)
        distance = attacker.position.manhattan(defender.position)
        ranged = self._is_ranged(attacker.stats)
        # 2. Momentum_Damage = ATK + 0.5 * ATK * Elevation_Modifier (never negative)
        momentum = max(Fraction(0), atk + Fraction(c.momentum_per_level) * atk * em)
        current_def = self.current_defense(defender.defense, defender.tile)

        rp = self.range_multiplier(attacker.stats, distance)
        if rp is None:  # 3. "> 4 tiles": Damage = 0, the attack fails
            return AttackResult(atk, em, momentum, distance, ranged, Fraction(0), Fraction(0), current_def,
                                Fraction(0), 0, True, 0, defender.effects, out_of_range=True)

        base = momentum * rp  # 3. Base_Damage = Momentum_Damage * RP
        raw = base - current_def
        damage = max(c.min_damage, math.floor(raw))  # 5. Final_Damage, rounded down, at least 1

        if math.floor(raw) <= 0 and c.allow_block:
            return AttackResult(atk, em, momentum, distance, ranged, rp, base, current_def, raw,
                                0, True, 0, defender.effects)

        defense_change, effects_after = self._apply_on_hit(attacker.stats, defender)
        return AttackResult(atk, em, momentum, distance, ranged, rp, base, current_def, raw,
                            damage, False, defense_change, effects_after)

    def _apply_on_hit(
        self, attacker: UnitStats, defender: CombatantSnapshot
    ) -> tuple[int, tuple[StatusEffect, ...]]:
        """6. On-hit effects. Applied whenever the attack resolves."""
        c = self.config
        defense_change = 0
        effects = list(defender.effects)
        for spec in attacker.on_hit:
            if spec.kind is EffectKind.ACID:
                shred = max(1, attacker.atk // c.acid_atk_divisor)
                remaining_def = defender.defense + defense_change
                defense_change -= min(shred, remaining_def)  # DEF never below 0
            elif spec.kind is EffectKind.VENOM:
                # Does not stack: re-applying refreshes the duration.
                damage = max(1, attacker.atk // c.venom_atk_divisor)
                effects = [e for e in effects if e.kind is not EffectKind.VENOM]
                effects.append(StatusEffect(EffectKind.VENOM, damage, c.venom_duration))
        return defense_change, tuple(effects)

    # ---------- Status effects ----------
    def tick_effects(self, effects: tuple[StatusEffect, ...], hp: int, defense: int) -> TickResult:
        hp_change = 0
        remaining: list[StatusEffect] = []
        ticks: list[EffectTick] = []
        for effect in effects:
            if effect.kind is EffectKind.VENOM:
                hp_change -= effect.magnitude  # ignores DEF
                ticks.append(EffectTick(EffectKind.VENOM, effect.magnitude))
                left = effect.remaining_turns - 1
                if left > 0:
                    remaining.append(StatusEffect(effect.kind, effect.magnitude, left))
            else:
                remaining.append(effect)
        return TickResult(hp_change, 0, tuple(remaining), tuple(ticks))
