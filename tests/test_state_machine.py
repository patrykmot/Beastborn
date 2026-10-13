import math
from dataclasses import replace
from fractions import Fraction

import pytest

from beastborn import constant as C
from beastborn.domain import Board, EffectKind, EffectSpec, Position, StatusEffect
from beastborn.engine import StandardCalculationEngine
from beastborn.game import (
    AttackCommand, EndTurnCommand, MoveCommand, Phase, custom_game,
)
from beastborn.game import events as ev
from tests.conftest import ARCHER, KING, SOLDIER

FLAT = Board.from_strings([".....", ".....", "....."])


def two_player_game(board=FLAT, p0_soldier=(1, 1), p1_soldier=(3, 1), **kwargs):
    """P0: King (0,0) + Soldier; P1: King (4,2) + Soldier. Units are ids 1..4."""
    return custom_game(
        board,
        [(0, KING, (0, 0)), (0, SOLDIER, p0_soldier), (1, KING, (4, 2)), (1, SOLDIER, p1_soldier)],
        **kwargs,
    )


def unit(gsm, unit_id):
    return gsm.view().unit(unit_id)


# ---------------------------------------------------------------- turn start
def test_first_turn_regenerates_only_active_player():
    gsm = two_player_game()
    assert unit(gsm, 2).energy == 6  # REG_EN
    assert unit(gsm, 4).energy == 0  # P1 has not started a turn yet


def test_energy_hoarding_caps_at_max():
    gsm = two_player_game()
    gsm.submit(EndTurnCommand())  # P1 turn 1: 6
    gsm.submit(EndTurnCommand())  # P0 turn 2
    assert unit(gsm, 2).energy == 10  # 6 + 6 capped at MAX_EN 10
    assert gsm.view().round == 2


# ---------------------------------------------------------------- movement
def test_move_spends_energy():
    gsm = two_player_game(p1_soldier=(4, 0))
    result = gsm.submit(MoveCommand(2, Position(3, 1)))
    assert result.ok
    moved = result.events[0]
    assert isinstance(moved, ev.UnitMoved)
    assert moved.cost == 4 and moved.path[0] == Position(1, 1) and moved.path[-1] == Position(3, 1)
    assert unit(gsm, 2).position == Position(3, 1)
    assert unit(gsm, 2).energy == 2


def test_move_without_enough_energy_changes_nothing():
    gsm = two_player_game()
    before = gsm.view()
    result = gsm.submit(MoveCommand(2, Position(4, 0)))  # 4 tiles = 8 EN, has 6
    assert not result.ok and "energy" in result.error
    assert gsm.view() == before


def test_move_takes_cheapest_path_around_mud_and_hills():
    board = Board.from_strings(
        [".....", ".mmm.", "....."],
        ["00000", "00000", "01110"],
    )
    gsm = custom_game(board, [(0, SOLDIER, (0, 1)), (0, KING, (0, 0)), (1, KING, (4, 0))])
    tiles = gsm.reachable_tiles(1)
    assert tiles[Position(1, 1)].cost == 3
    assert tiles[Position(2, 1)].cost == 6
    assert tiles[Position(0, 2)].cost == 2
    assert tiles[Position(1, 2)].cost == 6  # via (0,2): 2 + 4 uphill; via mud would be 3 + 4
    assert Position(4, 1) not in tiles  # out of 6 EN


def test_cannot_move_through_or_onto_units():
    board = Board.from_strings(["...", "...", "..."])
    gsm = custom_game(board, [(0, SOLDIER, (0, 1)), (0, KING, (1, 1)), (1, KING, (2, 2))])
    tiles = gsm.reachable_tiles(1)
    assert Position(1, 1) not in tiles and Position(2, 2) not in tiles
    assert tiles[Position(2, 0)].cost == 6  # must walk around the King
    assert Position(2, 1) not in tiles  # 8 EN the long way round
    assert not gsm.submit(MoveCommand(1, Position(1, 1))).ok


def test_cannot_move_enemy_unit():
    gsm = two_player_game()
    result = gsm.submit(MoveCommand(4, Position(3, 0)))
    assert not result.ok and "another player" in result.error


def test_cannot_move_outside_board():
    gsm = two_player_game()
    assert not gsm.submit(MoveCommand(2, Position(-1, 1))).ok


# ---------------------------------------------------------------- attacks
def test_attack_damages_and_spends_energy():
    gsm = two_player_game(p1_soldier=(2, 1))
    preview = gsm.preview_attack(2, 4)
    result = gsm.submit(AttackCommand(2, 4))
    assert result.ok
    hit = result.events[0]
    assert isinstance(hit, ev.UnitAttacked)
    assert hit.result == preview  # deterministic: the preview is exactly the outcome
    assert hit.result.damage == 3  # ATK 4 - DEF 1
    assert unit(gsm, 4).hp == 7
    assert unit(gsm, 2).energy == 4


def test_multiple_attacks_while_energy_lasts():
    gsm = two_player_game(p1_soldier=(2, 1))
    assert gsm.submit(AttackCommand(2, 4)).ok
    assert gsm.submit(AttackCommand(2, 4)).ok
    assert gsm.submit(AttackCommand(2, 4)).ok
    result = gsm.submit(AttackCommand(2, 4))  # 0 energy left
    assert not result.ok and "energy" in result.error
    assert unit(gsm, 4).hp == 1


def test_attack_limit_from_constant(monkeypatch):
    monkeypatch.setattr(C, "MAX_ATTACKS_PER_TURN", 1)
    gsm = two_player_game(p1_soldier=(2, 1))
    assert gsm.submit(AttackCommand(2, 4)).ok
    assert not gsm.submit(AttackCommand(2, 4)).ok


def test_attack_rejections():
    gsm = two_player_game(p0_soldier=(0, 1), p1_soldier=(4, 1))  # 6 EN: 2 steps + 1 attack, the target needs 3 steps
    assert "out of range" in gsm.submit(AttackCommand(2, 4)).error
    assert "own unit" in gsm.submit(AttackCommand(2, 1)).error
    assert "another player" in gsm.submit(AttackCommand(4, 2)).error
    assert "does not exist" in gsm.submit(AttackCommand(2, 99)).error


def test_attack_targets_lists_only_valid_targets():
    gsm = two_player_game(p1_soldier=(2, 1))
    targets = gsm.attack_targets(2)
    assert set(targets) == {4}
    assert targets[4].damage == 3
    assert gsm.attack_targets(4) == {}  # not this player's turn


def test_elevation_and_mud_in_a_real_fight():
    """ATK 4 on a hill (x(1 + ELEVATION_COEF)) vs DEF 4 in mud (floor(4 / 2) = 2); 0.3: 5.2 - 2 -> 3 damage."""
    board = Board.from_strings(["...", ".m.", "..."], ["010", "000", "000"])
    beast = replace(SOLDIER, atk=4)
    target = replace(SOLDIER, defense=4, hp=10)
    gsm = custom_game(board, [(0, beast, (1, 0)), (1, target, (1, 1)), (0, KING, (0, 2)), (1, KING, (2, 2))])
    result = gsm.submit(AttackCommand(1, 2))
    expected = max(1, math.floor(4 * (1 + Fraction(str(C.ELEVATION_COEF))) - 2))
    assert result.events[0].result.damage == expected
    assert unit(gsm, 2).hp == 10 - expected


# ---------------------------------------------------------------- archer (ranged)
LONG = Board.from_strings(["......", "......"], ["000000", "100000"])


def archer_game(target_x):
    """P0 Archer at (0,0), P1 Soldier at (target_x, 0); Kings on the bottom row."""
    return custom_game(LONG, [(0, ARCHER, (0, 0)), (1, SOLDIER, (target_x, 0)), (0, KING, (1, 1)), (1, KING, (5, 1))])


@pytest.mark.parametrize("x, damage", [(1, 3), (2, 7), (3, 5), (4, 5)])  # 8 * RP(0.5, 1, 0.8, 0.8) - DEF 1, rounded down
def test_archer_damage_depends_on_distance(x, damage):
    gsm = archer_game(x)
    assert set(gsm.attack_targets(1)) == {2}
    result = gsm.submit(AttackCommand(1, 2))
    assert result.ok and result.events[0].result.damage == damage
    assert unit(gsm, 2).hp == 10 - damage
    assert unit(gsm, 1).energy == 6 - 2


def test_archer_cannot_shoot_beyond_four_tiles_and_keeps_energy(monkeypatch):
    monkeypatch.setattr(C, "AUTO_MOVE_TO_ATTACK", False)
    gsm = archer_game(5)
    assert gsm.attack_targets(1) == {}
    result = gsm.submit(AttackCommand(1, 2))
    assert not result.ok and "range" in result.error
    assert unit(gsm, 1).energy == 6


def test_archer_beyond_four_tiles_steps_closer_and_shoots():
    gsm = archer_game(5)
    result = gsm.submit(AttackCommand(1, 2))
    moved, hit = result.events
    assert isinstance(moved, ev.UnitMoved) and moved.path == (Position(0, 0), Position(1, 0))
    assert hit.result.distance == 4 and hit.result.damage == 5  # 8 x0.8 - 1 = 5.4 -> 5
    assert unit(gsm, 1).energy == 6 - 3 - 2  # step 2 + move_penalty 1, then the attack


def test_archer_on_a_hill_shoots_harder():
    board = Board.from_strings(["......", "......"], ["100000", "000000"])
    gsm = custom_game(board, [(0, ARCHER, (0, 0)), (1, SOLDIER, (4, 0)), (0, KING, (0, 1)), (1, KING, (5, 1))])
    result = gsm.submit(AttackCommand(1, 2)).events[0].result
    momentum = 8 * (1 + Fraction(str(C.ELEVATION_COEF)))  # ATK 8, one level up; 0.3: 10.4
    base = momentum * Fraction(4, 5)  # 4 tiles: RP 0.8; 0.3: 8.32
    assert (result.momentum, result.base_damage, result.damage) == (momentum, base, math.floor(base - 1))
    assert result.damage > math.floor(8 * Fraction(4, 5) - 1)  # harder than from flat ground


def test_archer_is_slow():
    gsm = archer_game(5)
    tiles = gsm.reachable_tiles(1)
    assert tiles[Position(1, 0)].cost == 3  # 2 + move_penalty 1
    assert tiles[Position(2, 0)].cost == 6
    assert Position(3, 0) not in tiles  # a Soldier with 6 EN gets 3 tiles


# ---------------------------------------------------------------- status effects
def test_venom_ticks_at_start_of_victims_turn():
    rat = replace(SOLDIER, on_hit=(EffectSpec(EffectKind.VENOM),))  # ATK 4 -> 1 HP per round, 5 rounds
    gsm = two_player_game(p1_soldier=(2, 1))
    gsm._state.units[2].stats = rat  # P0 soldier becomes a rat
    gsm.submit(AttackCommand(2, 4))
    assert unit(gsm, 4).hp == 7
    assert unit(gsm, 4).effects == (StatusEffect(EffectKind.VENOM, 1, 5, source_id=2),)
    result = gsm.submit(EndTurnCommand())
    ticks = [e for e in result.events if isinstance(e, ev.EffectTicked)]
    assert len(ticks) == 1 and ticks[0].unit_id == 4
    assert unit(gsm, 4).hp == 6
    assert unit(gsm, 4).effects == (StatusEffect(EffectKind.VENOM, 1, 4, source_id=2),)


def test_venom_from_two_rats_stacks_and_ticks_twice():
    rat = replace(SOLDIER, on_hit=(EffectSpec(EffectKind.VENOM),))  # 4 - 1 = 3 damage, Venom 1 HP x 5 rounds
    gsm = custom_game(
        FLAT,
        [(0, KING, (0, 0)), (0, rat, (2, 1)), (0, rat, (3, 0)),
         (1, KING, (4, 2)), (1, replace(SOLDIER, hp=20), (3, 1))],
    )
    for _ in range(2):  # the second hit of the same rat refreshes its stack, it does not add one
        assert gsm.submit(AttackCommand(2, 5)).ok
    assert gsm.submit(AttackCommand(3, 5)).ok
    assert unit(gsm, 5).hp == 20 - 3 * 3
    assert unit(gsm, 5).effects == (
        StatusEffect(EffectKind.VENOM, 1, 5, source_id=2), StatusEffect(EffectKind.VENOM, 1, 5, source_id=3),
    )
    result = gsm.submit(EndTurnCommand())
    ticks = [e for e in result.events if isinstance(e, ev.EffectTicked)]
    assert [(t.unit_id, t.amount, t.hp_after) for t in ticks] == [(5, 1, 10), (5, 1, 9)]
    assert unit(gsm, 5).hp == 9
    assert [e.remaining_turns for e in unit(gsm, 5).effects] == [4, 4]


def test_start_of_turn_order_regen_then_venom_then_death():
    gsm = two_player_game()
    victim = gsm._state.units[4]
    victim.hp = 1
    victim.effects = (StatusEffect(EffectKind.VENOM, 1, 3),)
    result = gsm.submit(EndTurnCommand())
    kinds = [type(e) for e in result.events]
    i_regen = kinds.index(ev.EnergyRegenerated)
    i_tick = kinds.index(ev.EffectTicked)
    i_death = kinds.index(ev.UnitDied)
    assert kinds.index(ev.TurnStarted) < i_regen < i_tick < i_death
    assert unit(gsm, 4) is None


def test_acid_reduces_defense_permanently():
    acid = replace(SOLDIER, on_hit=(EffectSpec(EffectKind.ACID),))  # ATK 4 -> -1 DEF per hit
    gsm = two_player_game(p1_soldier=(2, 1))
    gsm._state.units[2].stats = acid
    gsm.submit(AttackCommand(2, 4))
    assert unit(gsm, 4).defense == 0
    gsm.submit(AttackCommand(2, 4))
    assert unit(gsm, 4).defense == 0  # never below zero
    assert unit(gsm, 4).hp == 10 - 3 - 4


# ---------------------------------------------------------------- death & victory
def test_killing_boss_wins_two_player_game():
    weak_king = replace(KING, hp=3)
    gsm = custom_game(FLAT, [(0, SOLDIER, (0, 0)), (1, weak_king, (1, 0)), (1, SOLDIER, (4, 2))])
    result = gsm.submit(AttackCommand(1, 2))
    kinds = [type(e) for e in result.events]
    assert kinds == [ev.UnitAttacked, ev.UnitDied, ev.UnitDied, ev.PlayerEliminated, ev.GameOver]
    view = gsm.view()
    assert view.phase is Phase.GAME_OVER and view.winner == 0
    assert view.units_of(1) == []  # D16: whole army removed
    assert not gsm.submit(EndTurnCommand()).ok


def test_killing_normal_unit_does_not_end_game():
    gsm = two_player_game(p1_soldier=(2, 1))
    gsm._state.units[4].hp = 3
    result = gsm.submit(AttackCommand(2, 4))
    assert [type(e) for e in result.events] == [ev.UnitAttacked, ev.UnitDied]
    assert gsm.phase is Phase.AWAITING_COMMAND


def test_eliminated_player_is_skipped_in_three_player_game():
    board = Board.from_strings([".....", ".....", "....."])
    weak_king = replace(KING, hp=3)
    gsm = custom_game(
        board,
        [(0, SOLDIER, (0, 0)), (0, KING, (0, 2)), (1, weak_king, (1, 0)), (2, KING, (4, 2))],
        num_players=3,
    )
    gsm.submit(AttackCommand(1, 3))
    assert gsm.phase is Phase.AWAITING_COMMAND  # two players left
    gsm.submit(EndTurnCommand())
    assert gsm.active_player == 2
    gsm.submit(EndTurnCommand())
    assert gsm.active_player == 0 and gsm.view().round == 2


def test_boss_killed_by_venom_at_own_turn_start():
    gsm = two_player_game()
    boss = gsm._state.units[3]
    boss.hp = 1
    boss.effects = (StatusEffect(EffectKind.VENOM, 1, 1),)
    result = gsm.submit(EndTurnCommand())
    assert isinstance(result.events[-1], ev.GameOver)
    assert gsm.view().winner == 0


def test_command_log_records_only_accepted_commands():
    gsm = two_player_game()
    gsm.submit(MoveCommand(2, Position(4, 0)))  # rejected
    gsm.submit(MoveCommand(2, Position(2, 1)))
    gsm.submit(EndTurnCommand())
    assert gsm.command_log == [MoveCommand(2, Position(2, 1)), EndTurnCommand()]


# ---------------------------------------------------------------- engine swap
class CheapMovesEngine(StandardCalculationEngine):
    """A different rule set: every step costs 1 EN and attacks always deal 1 damage."""

    def step_cost(self, unit, src, dst):
        return 1

    def resolve_attack(self, attacker, defender):
        result = super().resolve_attack(attacker, defender)
        return replace(result, damage=1)


def test_swapping_calculation_engine_changes_rules_without_touching_gsm():
    standard = two_player_game(p1_soldier=(2, 1))
    cheap = two_player_game(p1_soldier=(2, 1), engine=CheapMovesEngine())
    assert standard.reachable_tiles(2)[Position(1, 2)].cost == 2
    assert cheap.reachable_tiles(2)[Position(1, 2)].cost == 1
    cheap.submit(AttackCommand(2, 4))
    assert unit(cheap, 4).hp == 9


def test_rejects_unknown_command():
    gsm = two_player_game()
    assert not gsm.submit("jump").ok


def test_cannot_start_twice():
    gsm = two_player_game()
    with pytest.raises(RuntimeError):
        gsm.start()


# ---------------------------------------------------------------- move, then attack
def far_game(**kwargs):
    """P0 Soldier (0,1) with 6 EN; P1 Soldier (3,1). Only (2,1) is in reach (4 EN) and next to the target."""
    return two_player_game(p0_soldier=(0, 1), p1_soldier=(3, 1), **kwargs)


def test_attack_out_of_range_moves_first_then_attacks():
    gsm = far_game()
    result = gsm.submit(AttackCommand(2, 4))
    assert result.ok
    moved, hit = result.events
    assert isinstance(moved, ev.UnitMoved) and isinstance(hit, ev.UnitAttacked)
    assert moved.path[0] == Position(0, 1) and moved.path[-1] == Position(2, 1)
    assert moved.cost == 4 and moved.energy_after == 2
    assert (hit.source, hit.target) == (Position(2, 1), Position(3, 1))
    assert hit.result.damage == 3 and hit.energy_after == 0
    assert unit(gsm, 2).position == Position(2, 1) and unit(gsm, 4).hp == 7


def test_move_attack_is_logged_with_the_tile_it_was_made_from():
    gsm = far_game()
    gsm.submit(AttackCommand(2, 4))
    assert gsm.command_log == [AttackCommand(2, 4, Position(2, 1))]


def test_attack_in_range_never_moves():
    gsm = two_player_game(p1_soldier=(2, 1))
    result = gsm.submit(AttackCommand(2, 4))
    assert [type(e) for e in result.events] == [ev.UnitAttacked]
    assert unit(gsm, 2).position == Position(1, 1)
    assert gsm.command_log == [AttackCommand(2, 4)]


def test_ranged_unit_next_to_its_target_does_not_step_back():
    gsm = archer_game(1)
    plan = gsm.plan_attack(1, 2)
    assert not plan.needs_move and plan.result.range_multiplier == Fraction(1, 2)


def test_plan_is_exactly_what_happens():
    gsm = far_game()
    plan = gsm.plan_attack(2, 4)
    assert plan.needs_move and plan.origin == Position(2, 1) and plan.move_cost == 4
    hit = gsm.submit(AttackCommand(2, 4)).events[1]
    assert hit.result == plan.result


def test_rejected_move_attack_changes_nothing():
    gsm = two_player_game(p0_soldier=(0, 1), p1_soldier=(4, 1))
    before = gsm.view()
    assert gsm.plan_attack(2, 4) is None
    result = gsm.submit(AttackCommand(2, 4))
    assert not result.ok and "cannot be reached" in result.error
    assert gsm.view() == before and gsm.command_log == []


def test_attack_limit_is_checked_before_moving(monkeypatch):
    monkeypatch.setattr(C, "MAX_ATTACKS_PER_TURN", 0)
    gsm = far_game()
    result = gsm.submit(AttackCommand(2, 4))
    assert not result.ok and "No attacks left" in result.error
    assert unit(gsm, 2).position == Position(0, 1) and unit(gsm, 2).energy == 6


def test_move_must_leave_energy_for_the_attack():
    gsm = far_game()
    gsm._state.units[2].energy = 5  # 4 EN move + 2 EN attack = 6
    result = gsm.submit(AttackCommand(2, 4))
    assert not result.ok and "cannot be reached" in result.error
    assert unit(gsm, 2).position == Position(0, 1) and unit(gsm, 2).energy == 5


def test_auto_move_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(C, "AUTO_MOVE_TO_ATTACK", False)
    gsm = far_game()
    assert gsm.submit(AttackCommand(2, 4)).error == "Target is out of range"
    assert gsm.attack_plans(2) == {}


def test_explicit_tile_to_attack_from():
    gsm = far_game()
    assert "out of range from that tile" in gsm.submit(AttackCommand(2, 4, Position(1, 1))).error
    assert "Not enough energy" in gsm.submit(AttackCommand(2, 4, Position(3, 0))).error
    assert gsm.submit(AttackCommand(2, 4, Position(0, 1))).error == "Target is out of range"  # own tile = no move
    assert gsm.command_log == []
    result = gsm.submit(AttackCommand(2, 4, Position(2, 1)))
    assert result.ok and unit(gsm, 2).position == Position(2, 1)


def test_attack_plans_include_targets_after_a_move():
    gsm = far_game()
    assert gsm.attack_targets(2) == {}  # nothing in range from where it stands
    plans = gsm.attack_plans(2)
    assert set(plans) == {4} and plans[4].origin == Position(2, 1)  # the King at (4,2) is too far
    assert gsm.attack_plans(4) == {}  # not this player's turn


def test_picks_the_cheapest_tile_even_if_another_hits_harder():
    # Target (2,1) on flat ground. (2,0) is a hill: more damage, but climbing costs 2 more EN.
    board = Board.from_strings([".....", ".....", "....."], ["00100", "00000", "00000"])
    gsm = custom_game(board, [(0, KING, (0, 0)), (0, SOLDIER, (1, 0)), (1, KING, (4, 2)), (1, SOLDIER, (2, 1))])
    plan = gsm.plan_attack(2, 4)
    assert plan.origin == Position(1, 1) and plan.move_cost == 2


def test_equal_cost_picks_the_tile_with_more_damage():
    # From the hill at (1,0): stay high on (1,1) or walk down to (2,0). Both cost 2 EN; the high tile hits harder.
    board = Board.from_strings([".....", ".....", "....."], ["01000", "01000", "00000"])
    gsm = custom_game(board, [(0, KING, (0, 0)), (0, SOLDIER, (1, 0)), (1, KING, (4, 2)), (1, SOLDIER, (2, 1))])
    plan = gsm.plan_attack(2, 4)
    assert plan.origin == Position(1, 1) and plan.result.elevation_modifier == 1
    assert plan.result.damage > gsm._resolve(gsm._state.units[2], gsm._state.units[4], Position(2, 0)).damage


def test_equal_cost_and_damage_avoids_mud_then_goes_top_left():
    # Every step costs 1 and every hit 1 damage: (2,0) and (3,1) are equally good, but (2,0) is mud (half DEF).
    board = Board.from_strings(["..m..", ".....", "....."])
    gsm = custom_game(
        board, [(0, KING, (0, 0)), (0, SOLDIER, (1, 1)), (1, KING, (4, 2)), (1, SOLDIER, (3, 0))],
        engine=CheapMovesEngine(),
    )
    assert gsm.plan_attack(2, 4).origin == Position(3, 1)
    flat = custom_game(
        FLAT, [(0, KING, (0, 0)), (0, SOLDIER, (1, 1)), (1, KING, (4, 2)), (1, SOLDIER, (3, 0))],
        engine=CheapMovesEngine(),
    )
    assert flat.plan_attack(2, 4).origin == Position(2, 0)  # full tie: top row first


def test_replaying_the_command_log_gives_the_same_game():
    gsm = far_game()
    gsm.submit(AttackCommand(2, 4))
    gsm.submit(EndTurnCommand())
    gsm.submit(AttackCommand(4, 2))  # P1 hits back in place
    gsm.submit(EndTurnCommand())
    gsm.submit(AttackCommand(2, 4))
    replay = far_game()
    for command in gsm.command_log:
        assert replay.submit(command).ok
    assert replay.view() == gsm.view()
