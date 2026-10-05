"""The initial Calculation Engine: the rules from the game design document."""
from __future__ import annotations

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

    # ---------- Movement ----------
    def step_cost(self, unit: UnitStats, src: Tile, dst: Tile) -> int:
        c = self.config
        difficult = self._is_difficult(dst)
        if dst.elevation < src.elevation:  # downhill
            return c.EN_MD_DIFFICULT if difficult else c.EN_MD_NORMAL
        base = c.EN_DT if difficult else c.EN_NT
        levels_up = dst.elevation - src.elevation  # 0 on level ground
        return base + c.EN_MU * levels_up

    # ---------- Energy ----------
    def initial_energy(self, unit: UnitStats) -> int:
        return min(unit.max_en, self.config.starting_energy)

    def regenerate(self, unit: UnitStats, current_energy: int) -> int:
        return min(unit.max_en, current_energy + unit.reg_en)

    def attack_cost(self, attacker: UnitStats) -> int:
        return attacker.en_atk

    def in_attack_range(self, attacker: UnitStats, distance: int) -> bool:
        return 1 <= distance <= attacker.attack_range

    # ---------- Combat ----------
    def elevation_modifier(self, attacker_tile: Tile, defender_tile: Tile) -> int:
        diff = attacker_tile.elevation - defender_tile.elevation
        if diff > 0:
            return diff * self.config.elevation_attack_bonus
        if diff < 0 and self.config.uphill_attack_penalty:
            return diff * self.config.uphill_penalty_per_level  # diff is negative
        return 0

    def current_defense(self, defense: int, tile: Tile) -> int:
        penalty = self.config.terrain_defense_penalty.get(tile.terrain, 0)
        return max(0, defense - penalty)

    def resolve_attack(self, attacker: CombatantSnapshot, defender: CombatantSnapshot) -> AttackResult:
        base = attacker.stats.atk * attacker.stats.en_atk
        em = self.elevation_modifier(attacker.tile, defender.tile)
        current_def = self.current_defense(defender.defense, defender.tile)
        raw = base + em - current_def

        if raw <= 0 and self.config.allow_block:
            return AttackResult(base, em, current_def, raw, 0, True, 0, defender.effects)

        damage = max(self.config.min_damage, raw)
        defense_change, effects_after = self._apply_on_hit(attacker.stats, defender)
        return AttackResult(base, em, current_def, raw, damage, False, defense_change, effects_after)

    def _apply_on_hit(
        self, attacker: UnitStats, defender: CombatantSnapshot
    ) -> tuple[int, tuple[StatusEffect, ...]]:
        defense_change = 0
        effects = list(defender.effects)
        for spec in attacker.on_hit:
            if spec.kind is EffectKind.ACID:
                remaining_def = defender.defense + defense_change
                defense_change -= min(spec.magnitude, remaining_def)
            elif spec.kind is EffectKind.VENOM:
                # Re-applying refreshes the effect instead of stacking (D13).
                effects = [e for e in effects if e.kind is not EffectKind.VENOM]
                effects.append(StatusEffect(EffectKind.VENOM, spec.magnitude, spec.duration))
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
