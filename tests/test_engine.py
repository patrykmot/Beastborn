"""Calculation Engine tests: movement from the design document, combat from docs/game_mechanics.md."""
from dataclasses import replace
from fractions import Fraction

import pytest

from beastborn import constant as C
from beastborn.domain import (
    Board, EffectKind, EffectSpec, Player, Position, StatusEffect, TerrainType, Tile, UnitState, UnitStats,
)
from beastborn.engine import CombatantSnapshot, StandardCalculationEngine
from beastborn.game import AttackCommand, GameState, custom_game
from tests.conftest import (
    ARCHER, KING, MAX_RANGE, SOLDIER, acid_shred, hit_damage, mud_defense, range_multiplier, venom_per_round,
)

G, M = TerrainType.GRASS, TerrainType.MUD

UNIT = UnitStats(key="u", name="Unit", code="U", hp=10, atk=6, en_atk=2, defense=2, max_en=10, reg_en=4)
BOW = replace(UNIT, key="bow", atk=8, attack_range=4)

K = Fraction(str(C.ELEVATION_COEF))  # 2. ELEVATION_COEF, exact (0.3 -> 3/10)


def momentum(atk, em):
    """2. Momentum_Damage = ATK + ELEVATION_COEF * ATK * Elevation_Modifier (never negative)."""
    return max(Fraction(0), atk + K * atk * em)


@pytest.fixture
def ce():
    return StandardCalculationEngine()


@pytest.fixture
def ce_half(monkeypatch):
    """Rules pinned to the worked examples (ELEVATION_COEF 0.5, RP 0.5 / 1 / 0.8 / 0.8, half DEF in mud, at least 1
    damage): the hand-made formula texts below stay valid after a rebalance of constant.py."""
    monkeypatch.setattr(C, "ELEVATION_COEF", 0.5)
    monkeypatch.setattr(C, "RANGE_DISSIPATION", {1: 0.5, 2: 1.0, 3: 0.8, 4: 0.8})
    monkeypatch.setattr(C, "TERRAIN_DEFENSE_DIVISOR", {"mud": 2})
    monkeypatch.setattr(C, "MIN_DAMAGE", 1)
    monkeypatch.setattr(C, "ALLOW_BLOCK", False)
    return StandardCalculationEngine()


@pytest.fixture
def no_block(monkeypatch):
    """The minimum damage rule applies only without blocking (ALLOW_BLOCK)."""
    monkeypatch.setattr(C, "ALLOW_BLOCK", False)


def snap(stats=UNIT, tile=Tile(), defense=None, hp=None, effects=(), at=(0, 0), unit_id=None):
    return CombatantSnapshot(
        stats=stats,
        hp=stats.hp if hp is None else hp,
        defense=stats.defense if defense is None else defense,
        effects=effects,
        tile=tile,
        position=Position(*at),
        unit_id=unit_id,
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
        (Tile(G, 0), Tile(G, 0), C.EN_NT),
        (Tile(G, 0), Tile(M, 0), C.EN_DT),
        (Tile(M, 0), Tile(G, 0), C.EN_NT),  # leaving mud costs nothing extra
        (Tile(G, 0), Tile(G, 1), C.EN_NT + C.EN_MU),  # uphill (doc example)
        (Tile(G, 0), Tile(M, 1), C.EN_DT + C.EN_MU),  # uphill into mud (doc example)
        (Tile(G, 0), Tile(G, 2), C.EN_NT + 2 * C.EN_MU),  # two levels up (D8)
        (Tile(G, 1), Tile(G, 2), C.EN_NT + C.EN_MU),
        (Tile(G, 1), Tile(G, 0), C.EN_MD_NORMAL),
        (Tile(G, 1), Tile(M, 0), C.EN_MD_DIFFICULT),  # D7
        (Tile(G, 2), Tile(G, 0), C.EN_MD_NORMAL),  # downhill cost does not depend on levels
        (Tile(G, 1), Tile(G, 1), C.EN_NT),  # level ground at height
    ],
)
def test_step_cost(ce, src, dst, expected):
    assert ce.step_cost(UNIT, src, dst) == expected


@pytest.mark.parametrize(
    "src, dst, expected",
    [
        (Tile(G, 0), Tile(G, 0), C.EN_NT),
        (Tile(G, 0), Tile(M, 1), C.EN_DT + C.EN_MU),
        (Tile(G, 1), Tile(M, 0), C.EN_MD_DIFFICULT),
    ],
)
def test_move_penalty_is_added_to_every_step(ce, src, dst, expected):
    slow = replace(UNIT, move_penalty=1)
    assert ce.step_cost(slow, src, dst) == expected + 1


# ---------------------------------------------------------------- energy
def test_regeneration_caps_at_max(ce):
    assert ce.regenerate(UNIT, 0) == 4
    assert ce.regenerate(UNIT, 4) == 8
    assert ce.regenerate(UNIT, 8) == 10  # hoarding stops at MAX_EN


def test_initial_energy_and_attack_cost(ce):
    assert ce.initial_energy(UNIT) == min(UNIT.max_en, C.STARTING_ENERGY)
    assert ce.attack_cost(UNIT) == UNIT.en_atk  # 1. Energy_remaining = Energy_current - en_atk


def test_starting_energy_is_capped_at_max_en(ce, monkeypatch):
    monkeypatch.setattr(C, "STARTING_ENERGY", 99)
    assert ce.initial_energy(UNIT) == UNIT.max_en


def test_attack_range(ce):
    assert not ce.in_attack_range(UNIT, 0)
    assert ce.in_attack_range(UNIT, 1)
    assert not ce.in_attack_range(UNIT, 2)
    short_bow = replace(UNIT, attack_range=2)
    assert ce.in_attack_range(short_bow, 2) == (2 in C.RANGE_DISSIPATION)
    assert not ce.in_attack_range(short_bow, 3)  # beyond the unit's own range


def test_ranged_attacks_cannot_go_beyond_the_range_table(ce):
    """3. Farther than RANGE_DISSIPATION reaches, the attack can't be performed, even if the unit's range says more."""
    long_bow = replace(BOW, attack_range=MAX_RANGE + 2)
    distances = range(1, MAX_RANGE + 3)
    assert [ce.in_attack_range(long_bow, d) for d in distances] == [d in C.RANGE_DISSIPATION for d in distances]


# ---------------------------------------------------------------- 2. elevation momentum
@pytest.mark.parametrize("att, dfn, expected", [(0, 0, 0), (1, 0, 1), (2, 0, 2), (0, 1, -1), (0, 2, -2), (2, 1, 1)])
def test_elevation_modifier_is_level_difference(ce, att, dfn, expected):
    assert ce.elevation_modifier(Tile(G, att), Tile(G, dfn)) == expected


@pytest.mark.parametrize("att, dfn", [(0, 0), (1, 0), (2, 0), (0, 1), (0, 2)])
def test_momentum(ce, att, dfn):
    result = attack(ce, att_elev=att, def_elev=dfn)
    assert result.momentum == momentum(UNIT.atk, att - dfn)  # ATK + ELEVATION_COEF * ATK * EM
    assert result.range_multiplier == 1  # melee: no Range Dissipation


@pytest.mark.parametrize("coef, expected", [(0.3, Fraction(39, 5)), (0.5, 9), (Fraction(1, 3), 8), (0, 6)])
def test_custom_elevation_coef(ce, monkeypatch, coef, expected):
    """ATK 6, one level up: 6 + coef * 6 (0.3 -> 7.8, exact)."""
    monkeypatch.setattr(C, "ELEVATION_COEF", coef)
    assert attack(ce, att_elev=1).momentum == expected


def test_momentum_never_negative(ce, monkeypatch, no_block):
    monkeypatch.setattr(C, "ELEVATION_COEF", 0.6)
    result = attack(ce, def_elev=2)  # 6 - 0.6 * 6 * 2 = -1.2 -> 0
    assert result.momentum == 0
    assert result.damage == C.MIN_DAMAGE


def test_momentum_with_odd_atk_is_exact(ce_half):
    result = attack(ce_half, replace(UNIT, atk=5), replace(UNIT, defense=0), att_elev=1)
    assert result.momentum == Fraction(15, 2)  # 7.5, not rounded yet
    assert result.damage == 7  # rounded down only at the end


# ---------------------------------------------------------------- 3. range dissipation
@pytest.mark.parametrize("distance", sorted(C.RANGE_DISSIPATION))
def test_rp_multiplier_by_distance(ce, no_block, distance):
    rp = range_multiplier(distance)
    assert ce.range_multiplier(BOW, distance) == rp
    result = attack(ce, BOW, replace(UNIT, defense=0), distance=distance)
    assert result.base_damage == BOW.atk * rp  # 3. Base_Damage = Momentum_Damage * RP
    assert result.damage == hit_damage(BOW.atk * rp)


def test_beyond_the_range_table_does_no_damage(ce):
    assert ce.range_multiplier(BOW, MAX_RANGE + 1) is None
    result = attack(ce, BOW, distance=MAX_RANGE + 1)
    assert result.out_of_range and result.blocked and result.damage == 0
    assert result.formula() == "out of range"


def test_height_does_not_change_rp(ce):
    """Height only works through Momentum; RP depends on distance alone."""
    flat = attack(ce, BOW, distance=MAX_RANGE)
    high = attack(ce, BOW, distance=MAX_RANGE, att_elev=1)
    rp = range_multiplier(MAX_RANGE)
    assert flat.range_multiplier == high.range_multiplier == rp
    assert (high.momentum, high.base_damage) == (momentum(BOW.atk, 1), momentum(BOW.atk, 1) * rp)


def test_melee_units_ignore_range_dissipation(ce):
    assert ce.range_multiplier(UNIT, 1) == 1
    assert attack(ce).formula() == "6 - 2 = 4"


def test_ranged_example_from_a_hill(ce_half):
    """ATK 8 from one level up (coef 0.5), 4 tiles away: 12 * 0.8 = 9.6, minus DEF 2 = 7.6 -> 7."""
    result = attack(ce_half, BOW, distance=4, att_elev=1)
    assert result.damage == 7
    assert result.formula() == "8 x1.5 height x0.8 range - 2 = 7.6 -> 7"


def test_ranged_unit_adjacent_is_weak(ce_half):
    result = attack(ce_half, BOW, distance=1)
    assert result.base_damage == 4 and result.damage == 2
    assert result.formula() == "8 x0.5 range - 2 = 2"


def test_custom_range_table(ce, monkeypatch):
    monkeypatch.setattr(C, "RANGE_DISSIPATION", {1: 1, 2: 0.25})
    assert ce.range_multiplier(BOW, 2) == Fraction(1, 4)
    assert ce.range_multiplier(BOW, 3) is None
    assert not ce.in_attack_range(BOW, 3)


# ---------------------------------------------------------------- 4. defender effective DEF
@pytest.mark.parametrize("defense", [0, 1, 3, 4, 5])
def test_current_defense(ce, defense):
    assert ce.current_defense(defense, Tile(G, 0)) == defense
    assert ce.current_defense(defense, Tile(M, 0)) == mud_defense(defense)


# ---------------------------------------------------------------- 5. final damage
def test_hill_and_mud_example(ce_half):
    """ATK 6 on a hill (+1, coef 0.5) vs DEF 4 standing in mud: 9 - floor(4 / 2) = 7."""
    result = attack(ce_half, defender=replace(UNIT, defense=4), att_elev=1, def_terrain=M)
    assert (result.momentum, result.current_defense) == (9, 2)
    assert result.damage == 7
    assert result.formula() == "6 x1.5 height - 2 = 7"


def test_fractions_are_rounded_down_at_the_end(ce_half):
    result = attack(ce_half, BOW, replace(UNIT, defense=1), distance=3, def_elev=1)  # 8 * 0.5 * 0.8 - 1 = 2.2
    assert result.raw_damage == Fraction(11, 5) and result.damage == 2
    result = attack(ce_half, replace(BOW, atk=7), distance=3)  # 7 * 0.8 - 2 = 3.6
    assert result.damage == 3
    assert result.formula() == "7 x0.8 range - 2 = 3.6 -> 3"


@pytest.mark.parametrize("attacker, distance", [(UNIT, 1), (BOW, 1), (BOW, MAX_RANGE)])
def test_every_hit_deals_at_least_the_minimum(ce, no_block, attacker, distance):
    result = attack(ce, attacker, replace(UNIT, defense=10), distance=distance)
    assert result.raw_damage < 0
    assert result.damage == C.MIN_DAMAGE
    assert not result.blocked


def test_minimum_damage_formula(ce, no_block):
    assert attack(ce, defender=replace(UNIT, defense=10)).formula() == f"6 - 10 = -4 -> {C.MIN_DAMAGE}"
    assert attack(ce, defender=replace(UNIT, defense=6)).formula() == f"6 - 6 = 0 -> {C.MIN_DAMAGE}"


def test_weak_hit_still_applies_effects(ce, no_block):
    venom = replace(UNIT, on_hit=(EffectSpec(EffectKind.VENOM),))
    result = attack(ce, venom, replace(UNIT, defense=10))
    assert result.damage == C.MIN_DAMAGE
    assert result.defender_effects_after == (
        StatusEffect(EffectKind.VENOM, venom_per_round(UNIT.atk), C.VENOM_DURATION),
    )


def test_block_when_enabled(ce, monkeypatch):
    monkeypatch.setattr(C, "ALLOW_BLOCK", True)
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
@pytest.mark.parametrize("atk", [1, 4, 7, 8, 13])
def test_venom_damage_scales_with_atk(ce, atk):
    rat = replace(UNIT, atk=atk, on_hit=(EffectSpec(EffectKind.VENOM),))
    result = attack(ce, rat)
    assert result.defender_effects_after == (StatusEffect(EffectKind.VENOM, venom_per_round(atk), C.VENOM_DURATION),)


def test_venom_strength_rule():
    """max(MIN_EFFECT_MAGNITUDE, ATK // VENOM_ATK_DIVISOR): stronger units poison harder, weak ones still poison."""
    assert venom_per_round(1) == C.MIN_EFFECT_MAGNITUDE
    assert venom_per_round(3 * C.VENOM_ATK_DIVISOR) == max(C.MIN_EFFECT_MAGNITUDE, 3)


def test_venom_from_the_same_attacker_refreshes(ce):
    rat = replace(UNIT, atk=8, on_hit=(EffectSpec(EffectKind.VENOM),))
    already = (StatusEffect(EffectKind.VENOM, venom_per_round(8), 1),)
    result = attack(ce, rat, effects=already)
    assert result.defender_effects_after == (StatusEffect(EffectKind.VENOM, venom_per_round(8), C.VENOM_DURATION),)


def venom_hit(ce, attacker_id, atk, effects):
    rat = replace(UNIT, atk=atk, on_hit=(EffectSpec(EffectKind.VENOM),))
    defender = snap(UNIT, at=(1, 0), effects=effects, unit_id=99)
    return ce.resolve_attack(snap(rat, unit_id=attacker_id), defender).defender_effects_after


def fresh_venom(atk, source_id):
    return StatusEffect(EffectKind.VENOM, venom_per_round(atk), C.VENOM_DURATION, source_id)


def test_venom_from_different_attackers_stacks(ce):
    effects = venom_hit(ce, 1, 8, ())
    effects = venom_hit(ce, 2, 12, effects)
    assert effects == (fresh_venom(8, 1), fresh_venom(12, 2))


def test_venom_refreshes_only_the_attackers_own_stack(ce):
    effects = (StatusEffect(EffectKind.VENOM, 2, 1, 1), StatusEffect(EffectKind.VENOM, 3, 2, 2))
    assert venom_hit(ce, 1, 8, effects) == (fresh_venom(8, 1), effects[1])
    assert venom_hit(ce, 2, 12, effects) == (effects[0], fresh_venom(12, 2))


def test_every_venom_stack_ticks(ce):
    effects = (StatusEffect(EffectKind.VENOM, 2, 1, 1), StatusEffect(EffectKind.VENOM, 3, 2, 2))
    tick = ce.tick_effects(effects, hp=10, defense=3)
    assert tick.hp_change == -5
    assert [t.amount for t in tick.ticks] == [2, 3]
    assert tick.remaining_effects == (StatusEffect(EffectKind.VENOM, 3, 1, 2),)  # source kept, stack 1 expired


def test_venom_ticks_and_expires(ce):
    effects = (StatusEffect(EffectKind.VENOM, 2, 2),)
    tick = ce.tick_effects(effects, hp=10, defense=3)
    assert tick.hp_change == -2
    assert tick.remaining_effects == (StatusEffect(EffectKind.VENOM, 2, 1),)
    tick = ce.tick_effects(tick.remaining_effects, hp=8, defense=3)
    assert tick.hp_change == -2
    assert tick.remaining_effects == ()
    assert ce.tick_effects((), 10, 3).hp_change == 0


@pytest.mark.parametrize("atk, defense", [(6, 3), (9, 3), (10, 3), (20, 3), (35, 3), (6, 0), (20, 1)])
def test_acid_shred_scales_with_atk_and_stops_at_zero(ce, atk, defense):
    acid = replace(UNIT, atk=atk, on_hit=(EffectSpec(EffectKind.ACID),))
    result = attack(ce, acid, defense=defense)
    assert result.defense_change == -min(defense, acid_shred(atk))


def test_acid_applies_after_damage(ce):
    """The shred does not reduce the DEF used for the hit that applies it."""
    acid = replace(UNIT, on_hit=(EffectSpec(EffectKind.ACID),))
    plain = attack(ce, UNIT, defense=2)
    with_acid = attack(ce, acid, defense=2)
    assert plain.damage == with_acid.damage == 4


# ---------------------------------------------------------------- score
def test_score(ce):
    """Points per HP an enemy lost (more for a Boss) and per level my units stand above the ground."""
    boss = replace(UNIT, key="boss", hp=20, is_boss=True)
    board = Board.from_strings(["...", "..."], ["012", "000"])

    def unit(unit_id, owner, stats, at, hp):
        return UnitState(unit_id, owner, stats, Position(*at), hp=hp, energy=0, defense=stats.defense)

    units = [
        unit(1, 0, UNIT, (1, 0), hp=10),  # mine, level 1
        unit(2, 0, boss, (2, 0), hp=5),  # mine, level 2 (my own lost HP does not count)
        unit(3, 1, UNIT, (0, 0), hp=7),  # enemy lost 3 HP
        unit(4, 1, boss, (0, 1), hp=18),  # enemy Boss lost 2 HP
        unit(5, 2, UNIT, (1, 1), hp=9),  # second enemy lost 1 HP
    ]
    fallen = [
        unit(6, 1, UNIT, (2, 1), hp=-2),  # dead enemy: all 10 HP missing
        unit(7, 0, UNIT, (2, 1), hp=0),  # my dead unit: nothing for me
    ]
    state = GameState(board, [Player(i, f"P{i + 1}") for i in range(3)], {u.id: u for u in units}, fallen=fallen)

    hp, boss_hp, level = C.SCORE_PER_MISSING_HP, C.SCORE_PER_MISSING_BOSS_HP, C.SCORE_PER_ELEVATION_LEVEL
    kill = C.SCORE_PER_KILL
    # + dead enemy: all 10 HP and the kill bonus
    assert ce.score(state, 0) == (1 + 2) * level + 3 * hp + 2 * boss_hp + 1 * hp + 10 * hp + kill
    # P0 Boss lost 15 HP, P2 unit 1 HP, P0 dead unit 10 HP + the kill bonus
    assert ce.score(state, 1) == 15 * boss_hp + 1 * hp + 10 * hp + kill


def test_score_counts_killed_units():
    """Killing a unit adds its remaining HP and the kill bonus to the score, it does not drop out of it."""
    board = Board.from_strings(["......", "......"])
    gsm = custom_game(board, [(0, ARCHER, (0, 0)), (1, SOLDIER, (2, 0)), (0, KING, (0, 1)), (1, KING, (5, 1))])
    damage = gsm.preview_attack(1, 2).damage
    assert damage < SOLDIER.hp  # the first hit does not kill
    gsm.submit(AttackCommand(1, 2))
    assert gsm.score(0) == damage * C.SCORE_PER_MISSING_HP
    while gsm.view().unit(2) is not None:
        assert gsm.submit(AttackCommand(1, 2)).ok
    assert gsm.score(0) == SOLDIER.hp * C.SCORE_PER_MISSING_HP + C.SCORE_PER_KILL  # overkill does not count
