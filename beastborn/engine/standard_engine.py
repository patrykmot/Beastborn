"""The standard Calculation Engine.

Movement follows the game design document. Combat follows docs/game_mechanics.md
(``resolve_attack`` calculation rules); the section numbers below refer to that file.
All maths is exact (``Fraction``); only the final damage is rounded down.
Every rule number comes from ``beastborn/constant.py`` (read on each call, so tests can patch it).
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace
from fractions import Fraction
from functools import partial

from beastborn import constant as C
from beastborn.domain.effects import EffectKind, StatusEffect
from beastborn.domain.terrain import Tile
from beastborn.domain.unit import UnitStats
from beastborn.engine.interface import BoardState, CalculationEngine
from beastborn.engine.results import AttackResult, CombatantSnapshot, EffectTick, TickResult

_ZERO: Fraction = Fraction(0)
_ONE: Fraction = Fraction(1)


def _exact(value: float | Fraction) -> Fraction:
    return Fraction(str(value))  # str: 0.3 -> exactly 3/10, 0.8 -> 4/5


class StandardCalculationEngine(CalculationEngine):
    # ---------- helpers ----------
    def _is_difficult(self, tile: Tile) -> bool:
        return tile.terrain.value in C.DIFFICULT_TERRAINS

    def _effect_magnitude(self, atk: int, divisor: int) -> int:
        """6. On-hit effects scale with ATK: max(1, ATK // divisor)."""
        return max(C.MIN_EFFECT_MAGNITUDE, atk // divisor)

    # ---------- Movement ----------
    def step_cost(self, unit: UnitStats, src: Tile, dst: Tile) -> int:
        difficult: bool = self._is_difficult(dst)
        base: int
        if dst.elevation < src.elevation:  # downhill
            base = C.EN_MD_DIFFICULT if difficult else C.EN_MD_NORMAL
        else:
            levels_up: int = dst.elevation - src.elevation  # 0 on level ground
            base = (C.EN_DT if difficult else C.EN_NT) + C.EN_MU * levels_up
        return base + unit.move_penalty

    # ---------- Energy ----------
    def initial_energy(self, unit: UnitStats) -> int:
        return min(unit.max_en, C.STARTING_ENERGY)

    def regenerate(self, unit: UnitStats, current_energy: int) -> int:
        return min(unit.max_en, current_energy + unit.reg_en)

    def attack_cost(self, attacker: UnitStats) -> int:
        return attacker.en_atk  # 1. Energy_remaining = Energy_current - en_atk

    def in_attack_range(self, attacker: UnitStats, distance: int) -> bool:
        if not 1 <= distance <= attacker.attack_range:
            return False
        # 3. "> 4 tiles": a ranged attack cannot be performed at all (and costs no energy).
        return not attacker.is_ranged or distance in C.RANGE_DISSIPATION

    # ---------- Combat ----------
    def elevation_modifier(self, attacker_tile: Tile, defender_tile: Tile) -> int:
        # 2. Elevation_Modifier = Elev_attacker - Elev_defender
        return attacker_tile.elevation - defender_tile.elevation

    def current_defense(self, defense: int, tile: Tile) -> int:
        # 4. GRASS: DEF; MUD: floor(DEF / 2)
        divisor: int = C.TERRAIN_DEFENSE_DIVISOR.get(tile.terrain.value, 1)
        return max(0, defense // divisor)

    def range_multiplier(self, attacker: UnitStats, distance: int) -> Fraction | None:
        # 3. RP multiplier by distance (ranged units only); None = cannot attack that far
        if not attacker.is_ranged:
            return _ONE
        rp: float | None = C.RANGE_DISSIPATION.get(distance)
        return None if rp is None else _exact(rp)

    def resolve_attack(self, attacker: CombatantSnapshot, defender: CombatantSnapshot) -> AttackResult:
        atk: int = attacker.stats.atk
        em: int = self.elevation_modifier(attacker.tile, defender.tile)
        distance: int = attacker.position.manhattan(defender.position)
        # 2. Momentum_Damage = ATK + ELEVATION_COEF * ATK * Elevation_Modifier (never negative)
        momentum: Fraction = max(_ZERO, atk + _exact(C.ELEVATION_COEF) * atk * em)
        current_def: int = self.current_defense(defender.defense, defender.tile)
        result: Callable[..., AttackResult] = partial(
            AttackResult,
            atk=atk,
            elevation_modifier=em,
            momentum=momentum,
            distance=distance,
            ranged=attacker.stats.is_ranged,
            current_defense=current_def,
        )

        rp: Fraction | None = self.range_multiplier(attacker.stats, distance)
        if rp is None:  # 3. "> 4 tiles": Damage = 0, the attack fails
            return result(
                range_multiplier=_ZERO, base_damage=_ZERO, raw_damage=_ZERO, damage=0, blocked=True,
                defense_change=0, defender_effects_after=defender.effects, out_of_range=True,
            )

        base: Fraction = momentum * rp  # 3. Base_Damage = Momentum_Damage * RP
        raw: Fraction = base - current_def
        if math.floor(raw) <= 0 and C.ALLOW_BLOCK:
            return result(
                range_multiplier=rp, base_damage=base, raw_damage=raw, damage=0, blocked=True,
                defense_change=0, defender_effects_after=defender.effects,
            )

        damage: int = max(C.MIN_DAMAGE, math.floor(raw))  # 5. Final_Damage, rounded down, at least 1
        defense_change, effects_after = self._apply_on_hit(attacker, defender)
        return result(
            range_multiplier=rp, base_damage=base, raw_damage=raw, damage=damage, blocked=False,
            defense_change=defense_change, defender_effects_after=effects_after,
        )

    def _apply_on_hit(
        self, attacker: CombatantSnapshot, defender: CombatantSnapshot
    ) -> tuple[int, tuple[StatusEffect, ...]]:
        """6. On-hit effects. Applied whenever the attack resolves."""
        stats: UnitStats = attacker.stats
        defense_change: int = 0
        effects: list[StatusEffect] = list(defender.effects)
        for spec in stats.on_hit:
            if spec.kind is EffectKind.ACID:
                shred: int = self._effect_magnitude(stats.atk, C.ACID_ATK_DIVISOR)
                remaining_def: int = defender.defense + defense_change
                defense_change -= min(shred, remaining_def)  # DEF never below 0
            elif spec.kind is EffectKind.VENOM:
                # One stack per attacker: the same attacker refreshes its own stack, another one adds a stack.
                venom: StatusEffect = StatusEffect(
                    EffectKind.VENOM,
                    self._effect_magnitude(stats.atk, C.VENOM_ATK_DIVISOR),
                    C.VENOM_DURATION,
                    attacker.unit_id,
                )
                own: list[int] = [
                    i for i, e in enumerate(effects) if e.kind is EffectKind.VENOM and e.source_id == attacker.unit_id
                ]
                if own:
                    effects[own[0]] = venom  # keeps the order of the stacks
                else:
                    effects.append(venom)
        return defense_change, tuple(effects)

    # ---------- Status effects ----------
    def tick_effects(self, effects: tuple[StatusEffect, ...], hp: int, defense: int) -> TickResult:
        hp_change: int = 0
        remaining: list[StatusEffect] = []
        ticks: list[EffectTick] = []
        for effect in effects:
            if effect.kind is EffectKind.VENOM:
                hp_change -= effect.magnitude  # ignores DEF
                ticks.append(EffectTick(EffectKind.VENOM, effect.magnitude))
                turns_left: int = effect.remaining_turns - 1
                if turns_left > 0:
                    remaining.append(replace(effect, remaining_turns=turns_left))
            else:
                remaining.append(effect)
        return TickResult(hp_change, 0, tuple(remaining), tuple(ticks))

    # ---------- Score ----------
    def score(self, state: BoardState, player: int) -> int:
        """Points for ``player``: HP the opponents' units have lost (Bosses count more) + height of own units.

        A fallen opponent (killed, or its Boss died) counts with all of its HP missing, plus a bonus for the kill.
        """
        points: int = 0
        for unit in state.units.values():
            if unit.owner == player:
                points += C.SCORE_PER_ELEVATION_LEVEL * state.board.tile(unit.position).elevation
            else:
                points += self._missing_hp_points(unit.stats, max(0, unit.stats.hp - unit.hp))
        for unit in state.fallen:
            if unit.owner != player:
                points += self._missing_hp_points(unit.stats, unit.stats.hp) + C.SCORE_PER_KILL
        return points

    def _missing_hp_points(self, stats: UnitStats, missing_hp: int) -> int:
        per_hp: int = C.SCORE_PER_MISSING_BOSS_HP if stats.is_boss else C.SCORE_PER_MISSING_HP
        return per_hp * missing_hp
