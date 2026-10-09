"""Calculation Engine tests: movement from the design document, combat from docs/game_mechanics.md."""
from dataclasses import replace
from fractions import Fraction

import pytest

from beastborn.domain import EffectKind, EffectSpec, Position, StatusEffect, TerrainType, Tile, UnitStats
from beastborn.engine import CombatantSnapshot, RulesConfig, StandardCalculationEngine

G, M = TerrainType.GRASS, TerrainType.MUD

UNIT = UnitStats(key="u", name="Unit", code="U", hp=10, atk=6, en_atk=2, defense=2, max_en=10, reg_en=4)
BOW = replace(UNIT, key="bow", atk=8, attack_range=4)


@pytest.fixture
def ce():
    return StandardCalculationEngine()


def snap(stats=UNIT, tile=Tile(), defense=None, hp=None, effects=(), at=(0, 0)):
    return CombatantSnapshot(
        stats=stats,
        hp=stats.hp if hp is None else hp,
        defense=stats.defense if defense is None else defense,
        effects=effects,
        tile=tile,
        position=Position(*at),
    )


def attack(ce, attacker=UNIT, defender=UNIT, distance=1, att_elev=0, def_elev=0, def_terrain=G, **defender_kw):
    return ce.resolve_attack(
        snap(attacker, Tile(G, att_elev)),
        snap(defender, Tile(def_terrain, def_elev), at=(distance, 0), **defender_kw),
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


@pytest.mark.parametrize("src, dst, expected", [(Tile(G, 0), Tile(G, 0), 3), (Tile(G, 0), Tile(M, 1), 6), (Tile(G, 1), Tile(M, 0), 6)])
def test_move_penalty_is_added_to_every_step(ce, src, dst, expected):
    slow = replace(UNIT, move_penalty=1)
    assert ce.step_cost(slow, src, dst) == expected


# ---------------------------------------------------------------- energy
def test_regeneration_caps_at_max(ce):
    assert ce.regenerate(UNIT, 0) == 4
    assert ce.regenerate(UNIT, 4) == 8
    assert ce.regenerate(UNIT, 8) == 10  # hoarding stops at MAX_EN


def test_initial_energy_and_attack_cost(ce):
    assert ce.initial_energy(UNIT) == 0
    assert ce.attack_cost(UNIT) == 2  # 1. Energy_remaining = Energy_current - en_atk
    custom = StandardCalculationEngine(RulesConfig(starting_energy=99))
    assert custom.initial_energy(UNIT) == UNIT.max_en


def test_attack_range(ce):
    assert not ce.in_attack_range(UNIT, 0)
    assert ce.in_attack_range(UNIT, 1)
    assert not ce.in_attack_range(UNIT, 2)
    assert ce.in_attack_range(replace(UNIT, attack_range=3), 3)
    assert not ce.in_attack_range(replace(UNIT, attack_range=3), 4)


def test_ranged_attacks_cannot_go_beyond_four_tiles(ce):
    """3. '> 4 tiles: the attack can't be performed', even if the unit's range says more."""
    long_bow = replace(BOW, attack_range=6)
    assert [ce.in_attack_range(long_bow, d) for d in range(1, 7)] == [True, True, True, True, False, False]


# ---------------------------------------------------------------- 2. elevation momentum
@pytest.mark.parametrize("att, dfn, expected", [(0, 0, 0), (1, 0, 1), (2, 0, 2), (0, 1, -1), (0, 2, -2), (2, 1, 1)])
def test_elevation_modifier_is_level_difference(ce, att, dfn, expected):
    assert ce.elevation_modifier(Tile(G, att), Tile(G, dfn)) == expected


@pytest.mark.parametrize(
    "att, dfn, momentum",
    [(0, 0, 6), (1, 0, 9), (2, 0, 12), (0, 1, 3), (0, 2, 0)],  # ATK 6 + 0.5 * 6 * EM
)
def test_momentum(ce, att, dfn, momentum):
    result = attack(ce, att_elev=att, def_elev=dfn)
    assert result.momentum == momentum
    assert result.range_multiplier == 1  # melee: no Range Dissipation


def test_momentum_with_odd_atk_is_exact(ce):
    result = attack(ce, replace(UNIT, atk=5), replace(UNIT, defense=0), att_elev=1)
    assert result.momentum == Fraction(15, 2)  # 7.5, not rounded yet
    assert result.damage == 7  # rounded down only at the end


# ---------------------------------------------------------------- 3. range dissipation
@pytest.mark.parametrize("distance, rp", [(1, Fraction(1, 2)), (2, 1), (3, Fraction(4, 5)), (4, Fraction(4, 5))])
def test_rp_multiplier_by_distance(ce, distance, rp):
    assert ce.range_multiplier(BOW, distance) == rp
    result = attack(ce, BOW, replace(UNIT, defense=0), distance=distance)
    assert result.base_damage == 8 * rp  # 3. Base_Damage = Momentum_Damage * RP
    assert result.damage == int(8 * rp)


def test_beyond_four_tiles_does_no_damage(ce):
    assert ce.range_multiplier(BOW, 5) is None
    result = attack(ce, BOW, distance=5)
    assert result.out_of_range and result.blocked and result.damage == 0
    assert result.formula() == "out of range"


def test_height_does_not_change_rp(ce):
    """Height only works through Momentum; RP depends on distance alone."""
    flat = attack(ce, BOW, distance=4)
    high = attack(ce, BOW, distance=4, att_elev=1)
    assert flat.range_multiplier == high.range_multiplier == Fraction(4, 5)
    assert (high.momentum, high.base_damage) == (12, Fraction(48, 5))  # 12 * 0.8 = 9.6


def test_melee_units_ignore_range_dissipation(ce):
    assert ce.range_multiplier(UNIT, 1) == 1
    assert attack(ce).formula() == "6 - 2 = 4"


def test_ranged_example_from_a_hill(ce):
    """ATK 8 from one level up, 4 tiles away: 12 * 0.8 = 9.6, minus DEF 2 = 7.6 -> 7."""
    result = attack(ce, BOW, distance=4, att_elev=1)
    assert result.damage == 7
    assert result.formula() == "8 x1.5 height x0.8 range - 2 = 7.6 -> 7"


def test_ranged_unit_adjacent_is_weak(ce):
    result = attack(ce, BOW, distance=1)
    assert result.base_damage == 4 and result.damage == 2
    assert result.formula() == "8 x0.5 range - 2 = 2"


def test_custom_range_table():
    ce = StandardCalculationEngine(RulesConfig(range_dissipation={1: 1, 2: 0.25}))
    assert ce.range_multiplier(BOW, 2) == Fraction(1, 4)
    assert ce.range_multiplier(BOW, 3) is None
    assert not ce.in_attack_range(BOW, 3)


# ---------------------------------------------------------------- 4. defender effective DEF
@pytest.mark.parametrize("defense, terrain, expected", [(3, G, 3), (3, M, 1), (4, M, 2), (1, M, 0), (0, M, 0), (5, M, 2)])
def test_current_defense(ce, defense, terrain, expected):
    assert ce.current_defense(defense, Tile(terrain, 0)) == expected


# ---------------------------------------------------------------- 5. final damage
def test_hill_and_mud_example(ce):
    """ATK 6 on a hill (+1) vs DEF 4 standing in mud: 9 - floor(4 / 2) = 7."""
    result = attack(ce, defender=replace(UNIT, defense=4), att_elev=1, def_terrain=M)
    assert (result.momentum, result.current_defense) == (9, 2)
    assert result.damage == 7
    assert result.formula() == "6 x1.5 height - 2 = 7"


def test_fractions_are_rounded_down_at_the_end(ce):
    result = attack(ce, BOW, replace(UNIT, defense=1), distance=3, def_elev=1)  # 8 * 0.5 * 0.8 - 1 = 2.2
    assert result.raw_damage == Fraction(11, 5) and result.damage == 2
    result = attack(ce, replace(BOW, atk=7), distance=3)  # 7 * 0.8 - 2 = 3.6
    assert result.damage == 3
    assert result.formula() == "7 x0.8 range - 2 = 3.6 -> 3"


@pytest.mark.parametrize("attacker, distance", [(UNIT, 1), (BOW, 1), (BOW, 4)])
def test_every_hit_deals_at_least_one(ce, attacker, distance):
    result = attack(ce, attacker, replace(UNIT, defense=10), distance=distance)
    assert result.raw_damage < 0
    assert result.damage == 1
    assert not result.blocked


def test_minimum_damage_formula(ce):
    assert attack(ce, defender=replace(UNIT, defense=10)).formula() == "6 - 10 = -4 -> 1"
    assert attack(ce, defender=replace(UNIT, defense=6)).formula() == "6 - 6 = 0 -> 1"


def test_weak_hit_still_applies_effects(ce):
    venom = replace(UNIT, on_hit=(EffectSpec(EffectKind.VENOM),))
    result = attack(ce, venom, replace(UNIT, defense=10))
    assert result.damage == 1
    assert result.defender_effects_after == (StatusEffect(EffectKind.VENOM, 1, 5),)


def test_block_when_enabled():
    ce = StandardCalculationEngine(RulesConfig(allow_block=True))
    venom = replace(UNIT, on_hit=(EffectSpec(EffectKind.VENOM),))
    result = attack(ce, venom, replace(UNIT, defense=10))
    assert result.blocked and result.damage == 0  # optional rule: overrides the minimum of 1
    assert result.defender_effects_after == ()  # a blocked hit applies no effects
    assert not attack(ce, defender=replace(UNIT, defense=0)).blocked


def test_attack_does_not_mutate_inputs(ce):
    attacker, defender = snap(), snap(at=(1, 0))
    ce.resolve_attack(attacker, defender)
    assert defender.hp == UNIT.hp and defender.defense == UNIT.defense


# ---------------------------------------------------------------- 6. on-hit effects
@pytest.mark.parametrize("atk, per_round", [(1, 1), (4, 1), (7, 1), (8, 2), (13, 3)])
def test_venom_damage_scales_with_atk(ce, atk, per_round):
    rat = replace(UNIT, atk=atk, on_hit=(EffectSpec(EffectKind.VENOM),))
    result = attack(ce, rat)
    assert result.defender_effects_after == (StatusEffect(EffectKind.VENOM, per_round, 5),)


def test_venom_refreshes_instead_of_stacking(ce):
    rat = replace(UNIT, atk=8, on_hit=(EffectSpec(EffectKind.VENOM),))
    already = (StatusEffect(EffectKind.VENOM, 2, 1),)
    result = attack(ce, rat, effects=already)
    assert result.defender_effects_after == (StatusEffect(EffectKind.VENOM, 2, 5),)


def test_venom_ticks_and_expires(ce):
    effects = (StatusEffect(EffectKind.VENOM, 2, 2),)
    tick = ce.tick_effects(effects, hp=10, defense=3)
    assert tick.hp_change == -2
    assert tick.remaining_effects == (StatusEffect(EffectKind.VENOM, 2, 1),)
    tick = ce.tick_effects(tick.remaining_effects, hp=8, defense=3)
    assert tick.hp_change == -2
    assert tick.remaining_effects == ()
    assert ce.tick_effects((), 10, 3).hp_change == 0


@pytest.mark.parametrize(
    "atk, defense, expected_change",
    [(6, 3, -1), (9, 3, -1), (10, 3, -1), (20, 3, -2), (35, 3, -3), (6, 0, 0), (20, 1, -1)],
)
def test_acid_shred_scales_with_atk_and_stops_at_zero(ce, atk, defense, expected_change):
    acid = replace(UNIT, atk=atk, on_hit=(EffectSpec(EffectKind.ACID),))
    result = attack(ce, acid, defense=defense)
    assert result.defense_change == expected_change


def test_acid_applies_after_damage(ce):
    """The shred does not reduce the DEF used for the hit that applies it."""
    acid = replace(UNIT, on_hit=(EffectSpec(EffectKind.ACID),))
    plain = attack(ce, UNIT, defense=2)
    with_acid = attack(ce, acid, defense=2)
    assert plain.damage == with_acid.damage == 4
