"""Calculation Engine tests - every rule from the game design document."""
from dataclasses import replace

import pytest

from beastborn.domain import EffectKind, EffectSpec, StatusEffect, TerrainType, Tile, UnitStats
from beastborn.engine import CombatantSnapshot, RulesConfig, StandardCalculationEngine

G, M = TerrainType.GRASS, TerrainType.MUD

UNIT = UnitStats(key="u", name="Unit", code="U", hp=10, atk=2, en_atk=2, defense=3, max_en=10, reg_en=4)


@pytest.fixture
def ce():
    return StandardCalculationEngine()


def snap(stats=UNIT, tile=Tile(), defense=None, hp=None, effects=()):
    return CombatantSnapshot(
        stats=stats,
        hp=stats.hp if hp is None else hp,
        defense=stats.defense if defense is None else defense,
        effects=effects,
        tile=tile,
    )


# ---------------------------------------------------------------- movement
@pytest.mark.parametrize(
    "src, dst, expected",
    [
        (Tile(G, 0), Tile(G, 0), 2),  # EN_NT
        (Tile(G, 0), Tile(M, 0), 3),  # EN_DT
        (Tile(M, 0), Tile(G, 0), 2),  # leaving mud costs nothing extra
        (Tile(G, 0), Tile(G, 1), 4),  # 2 + 2 uphill (doc example)
        (Tile(G, 0), Tile(M, 1), 5),  # 3 + 2 uphill mud (doc example)
        (Tile(G, 0), Tile(G, 2), 6),  # two levels up (D8)
        (Tile(G, 1), Tile(G, 2), 4),
        (Tile(G, 1), Tile(G, 0), 2),  # EN_MD normal
        (Tile(G, 1), Tile(M, 0), 5),  # EN_MD difficult (D7)
        (Tile(G, 2), Tile(G, 0), 2),  # downhill cost does not depend on levels
        (Tile(G, 1), Tile(G, 1), 2),  # level ground at height
    ],
)
def test_step_cost(ce, src, dst, expected):
    assert ce.step_cost(UNIT, src, dst) == expected


# ---------------------------------------------------------------- energy
def test_regeneration_caps_at_max(ce):
    assert ce.regenerate(UNIT, 0) == 4
    assert ce.regenerate(UNIT, 4) == 8
    assert ce.regenerate(UNIT, 8) == 10  # hoarding stops at MAX_EN


def test_initial_energy_and_attack_cost(ce):
    assert ce.initial_energy(UNIT) == 0
    assert ce.attack_cost(UNIT) == 2
    custom = StandardCalculationEngine(RulesConfig(starting_energy=99))
    assert custom.initial_energy(UNIT) == UNIT.max_en


def test_attack_range(ce):
    assert not ce.in_attack_range(UNIT, 0)
    assert ce.in_attack_range(UNIT, 1)
    assert not ce.in_attack_range(UNIT, 2)
    assert ce.in_attack_range(replace(UNIT, attack_range=3), 3)


# ---------------------------------------------------------------- elevation modifier
@pytest.mark.parametrize(
    "att, dfn, flag, expected",
    [
        (1, 0, False, 2),
        (2, 0, False, 4),
        (0, 0, False, 0),
        (0, 1, False, 0),  # attacking from below: no penalty by default
        (0, 2, False, 0),
        (0, 1, True, -1),  # optional mechanic: -1 per level
        (0, 2, True, -2),
        (2, 1, True, 2),  # bonus is unaffected by the flag
    ],
)
def test_elevation_modifier(att, dfn, flag, expected):
    ce = StandardCalculationEngine(RulesConfig(uphill_attack_penalty=flag))
    assert ce.elevation_modifier(Tile(G, att), Tile(G, dfn)) == expected


# ---------------------------------------------------------------- terrain defence
@pytest.mark.parametrize(
    "defense, terrain, expected",
    [(3, G, 3), (3, M, 1), (1, M, 0), (0, M, 0), (4, M, 2)],
)
def test_current_defense(ce, defense, terrain, expected):
    assert ce.current_defense(defense, Tile(terrain, 0)) == expected


# ---------------------------------------------------------------- damage
def test_design_document_example(ce):
    """ATK 2, EN_ATK 2 on a hill (+1 level) vs DEF 4 standing in mud -> 4 damage."""
    attacker = snap(replace(UNIT, atk=2, en_atk=2), tile=Tile(G, 1))
    defender = snap(replace(UNIT, defense=4), tile=Tile(M, 0))
    result = ce.resolve_attack(attacker, defender)
    assert (result.base_damage, result.elevation_modifier, result.current_defense) == (4, 2, 2)
    assert result.damage == 4
    assert not result.blocked
    assert result.formula() == "4 +2 - 2 = 4"


def test_minimum_damage_is_one(ce):
    attacker = snap(replace(UNIT, atk=1, en_atk=1))
    defender = snap(replace(UNIT, defense=10))
    result = ce.resolve_attack(attacker, defender)
    assert result.raw_damage == -9
    assert result.damage == 1
    assert not result.blocked


def test_block_when_enabled():
    ce = StandardCalculationEngine(RulesConfig(allow_block=True))
    venom = (EffectSpec(EffectKind.VENOM, 1, 3),)
    attacker = snap(replace(UNIT, atk=1, en_atk=1, on_hit=venom))
    defender = snap(replace(UNIT, defense=10))
    result = ce.resolve_attack(attacker, defender)
    assert result.blocked
    assert result.damage == 0
    assert result.defender_effects_after == ()  # a blocked hit applies no effects


def test_block_enabled_but_damage_positive():
    ce = StandardCalculationEngine(RulesConfig(allow_block=True))
    result = ce.resolve_attack(snap(), snap(replace(UNIT, defense=0)))
    assert not result.blocked and result.damage == 4


def test_attack_does_not_mutate_inputs(ce):
    attacker, defender = snap(), snap()
    ce.resolve_attack(attacker, defender)
    assert defender.hp == UNIT.hp and defender.defense == UNIT.defense


# ---------------------------------------------------------------- status effects
def test_venom_applied_and_refreshed(ce):
    rat = replace(UNIT, on_hit=(EffectSpec(EffectKind.VENOM, 1, 3),))
    result = ce.resolve_attack(snap(rat), snap())
    assert result.defender_effects_after == (StatusEffect(EffectKind.VENOM, 1, 3),)

    already = (StatusEffect(EffectKind.VENOM, 1, 1),)
    result = ce.resolve_attack(snap(rat), snap(effects=already))
    assert result.defender_effects_after == (StatusEffect(EffectKind.VENOM, 1, 3),)  # no stacking


def test_venom_ticks_and_expires(ce):
    effects = (StatusEffect(EffectKind.VENOM, 2, 2),)
    tick = ce.tick_effects(effects, hp=10, defense=3)
    assert tick.hp_change == -2
    assert tick.remaining_effects == (StatusEffect(EffectKind.VENOM, 2, 1),)
    tick = ce.tick_effects(tick.remaining_effects, hp=8, defense=3)
    assert tick.hp_change == -2
    assert tick.remaining_effects == ()
    assert ce.tick_effects((), 10, 3).hp_change == 0


@pytest.mark.parametrize("defense, expected_change", [(3, -1), (1, -1), (0, 0)])
def test_acid_shreds_defense_to_minimum_zero(ce, defense, expected_change):
    acid = replace(UNIT, on_hit=(EffectSpec(EffectKind.ACID, 1),))
    result = ce.resolve_attack(snap(acid), snap(defense=defense))
    assert result.defense_change == expected_change


def test_acid_applies_after_damage(ce):
    """The shred does not reduce the DEF used for the hit that applies it."""
    acid = replace(UNIT, on_hit=(EffectSpec(EffectKind.ACID, 1),))
    plain = ce.resolve_attack(snap(UNIT), snap(defense=3))
    with_acid = ce.resolve_attack(snap(acid), snap(defense=3))
    assert plain.damage == with_acid.damage == 1
