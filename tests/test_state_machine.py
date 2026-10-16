import math
from dataclasses import fields, is_dataclass, replace
from fractions import Fraction

import pytest

from beastborn import constant as C
from beastborn.domain import Board, EffectKind, EffectSpec, Position, StatusEffect
from beastborn.engine import StandardCalculationEngine
from beastborn.game import (
    AttackCommand, Command, CommandType, EndTurnCommand, MoveCommand, Phase, WrapperCommand, custom_game,
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


def turn_of(gsm, unit_id):
    """End turns until it is ``unit_id``'s turn; returns the events on the way. (The GSM picks who acts.)"""
    events = []
    for _ in range(50):
        if gsm.active_unit == unit_id:
            return events
        result = gsm.submit(EndTurnCommand())
        assert result.ok, result.error
        events += result.events
    raise AssertionError(f"unit {unit_id} never got its turn")


def active_units(gsm, turns):
    """Ids of the next ``turns`` active units when every unit just ends its turn."""
    order = [gsm.active_unit]
    for _ in range(turns - 1):
        gsm.submit(EndTurnCommand())
        order.append(gsm.active_unit)
    return order


# ---------------------------------------------------------------- turn order
def test_players_alternate_and_units_go_in_id_order():
    gsm = two_player_game()  # P0: 1, 2; P1: 3, 4
    assert active_units(gsm, 5) == [1, 3, 2, 4, 1]
    assert gsm.view().round == 2


def test_round_starts_with_the_first_player_and_bigger_army_finishes_the_round():
    gsm = custom_game(FLAT, [(0, KING, (0, 0)), (0, SOLDIER, (1, 1)), (0, SOLDIER, (2, 0)), (1, KING, (4, 2))])
    assert active_units(gsm, 5) == [1, 4, 2, 3, 1]


def test_only_the_active_unit_regenerates():
    gsm = two_player_game()
    assert (gsm.active_player, gsm.active_unit) == (0, 1)
    assert unit(gsm, 1).energy == 6  # REG_EN at the start of its own turn
    assert unit(gsm, 2).energy == 0  # its turn has not started yet
    turn_of(gsm, 2)
    assert unit(gsm, 2).energy == 6


def test_energy_hoarding_caps_at_max():
    gsm = two_player_game()
    gsm.submit(EndTurnCommand())
    turn_of(gsm, 1)  # round 2
    assert unit(gsm, 1).energy == 10  # 6 + 6 capped at MAX_EN 10
    assert gsm.view().round == 2


def test_only_the_active_unit_can_act():
    gsm = two_player_game()
    assert gsm.active_unit == 1
    result = gsm.submit(MoveCommand(2, Position(2, 1)))
    assert not result.ok and "not this unit's turn" in result.error
    assert gsm.reachable_tiles(2) == {} and gsm.attack_plans(2) == {}
    assert "another player" in gsm.submit(MoveCommand(3, Position(3, 2))).error


def test_end_turn_without_acting_is_waiting():
    gsm = two_player_game()
    result = gsm.submit(EndTurnCommand())
    assert result.events[0] == ev.TurnEnded(0, 1, idle=True)
    assert isinstance(result.events[1], ev.TurnStarted) and result.events[1].unit_id == 3


def test_turn_ends_by_itself_when_the_unit_cannot_do_more():
    gsm = two_player_game(p1_soldier=(2, 1))
    turn_of(gsm, 2)
    assert gsm.submit(AttackCommand(2, 4)).ok and gsm.active_unit == 2  # 4 EN left
    assert gsm.submit(AttackCommand(2, 4)).ok and gsm.active_unit == 2  # 2 EN left: still enough for a step
    result = gsm.submit(AttackCommand(2, 4))  # 0 EN: nothing left to do
    kinds = [type(e) for e in result.events]
    assert kinds[:3] == [ev.UnitAttacked, ev.TurnEnded, ev.TurnStarted]
    assert result.events[1] == ev.TurnEnded(0, 2, idle=False)
    assert gsm.active_unit == 4
    assert gsm.command_log[-1] == AttackCommand(2, 4)  # the automatic turn end is not a command


def test_unit_that_cannot_act_is_put_off_to_the_end_of_the_round():
    # The King (1) is boxed in by its own Soldiers and has no enemy in range.
    gsm = custom_game(FLAT, [(0, KING, (0, 0)), (0, SOLDIER, (1, 0)), (0, SOLDIER, (0, 1)), (1, KING, (4, 2))])
    assert gsm.active_unit == 2
    assert active_units(gsm, 3) == [2, 4, 3]
    result = gsm.submit(EndTurnCommand())  # only the boxed-in King is left: its turn starts and ends by itself
    kinds = [type(e) for e in result.events]
    assert ev.TurnEnded(0, 1, idle=True) in result.events
    assert kinds.index(ev.EnergyRegenerated) < kinds.index(ev.RoundStarted)  # it still regenerated this round
    assert gsm.view().round == 2 and gsm.active_unit == 2


def test_put_off_unit_acts_as_soon_as_it_is_free():
    gsm = custom_game(FLAT, [(0, KING, (0, 0)), (0, SOLDIER, (1, 0)), (0, SOLDIER, (0, 1)), (1, KING, (4, 2))])
    assert gsm.submit(MoveCommand(2, Position(2, 0))).ok  # frees (1,0)
    turn_of(gsm, 4)
    gsm.submit(EndTurnCommand())
    assert gsm.active_unit == 1  # before Soldier 3: id order again


# ---------------------------------------------------------------- movement
def test_move_spends_energy():
    gsm = two_player_game(p1_soldier=(4, 0))
    turn_of(gsm, 2)
    result = gsm.submit(MoveCommand(2, Position(3, 1)))
    assert result.ok
    moved = result.events[0]
    assert isinstance(moved, ev.UnitMoved)
    assert moved.cost == 4 and moved.path[0] == Position(1, 1) and moved.path[-1] == Position(3, 1)
    assert unit(gsm, 2).position == Position(3, 1)
    assert unit(gsm, 2).energy == 2


def test_move_without_enough_energy_changes_nothing():
    gsm = two_player_game()
    turn_of(gsm, 2)
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
    turn_of(gsm, 2)
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
    turn_of(gsm, 2)
    assert gsm.submit(AttackCommand(2, 4)).ok
    assert gsm.submit(AttackCommand(2, 4)).ok
    assert gsm.submit(AttackCommand(2, 4)).ok
    assert unit(gsm, 4).hp == 1 and unit(gsm, 2).energy == 0
    assert not gsm.submit(AttackCommand(2, 4)).ok  # its turn is over


def test_not_enough_energy_to_attack():
    gsm = two_player_game(p1_soldier=(2, 1))
    turn_of(gsm, 2)
    gsm._state.units[2].energy = 1
    result = gsm.submit(AttackCommand(2, 4))
    assert not result.ok and "energy" in result.error


def test_attack_limit_from_constant(monkeypatch):
    monkeypatch.setattr(C, "MAX_ATTACKS_PER_TURN", 1)
    gsm = two_player_game(p1_soldier=(2, 1))
    turn_of(gsm, 2)
    assert gsm.submit(AttackCommand(2, 4)).ok
    assert not gsm.submit(AttackCommand(2, 4)).ok


def test_attack_rejections():
    gsm = two_player_game(p0_soldier=(0, 1), p1_soldier=(4, 1))  # 6 EN: 2 steps + 1 attack, the target needs 3 steps
    turn_of(gsm, 2)
    assert "out of range" in gsm.submit(AttackCommand(2, 4)).error
    assert "not this unit's turn" in gsm.submit(AttackCommand(1, 4)).error
    assert "own unit" in gsm.submit(AttackCommand(2, 1)).error
    assert "another player" in gsm.submit(AttackCommand(4, 2)).error
    assert "does not exist" in gsm.submit(AttackCommand(2, 99)).error


def test_attack_targets_lists_only_valid_targets():
    gsm = two_player_game(p1_soldier=(2, 1))
    turn_of(gsm, 2)
    targets = gsm.attack_targets(2)
    assert set(targets) == {4}
    assert targets[4].damage == 3
    assert gsm.attack_targets(4) == {}  # not this player's turn
    assert gsm.attack_targets(1) == {}  # not this unit's turn


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
    moved, hit, ended = result.events[:3]  # 1 EN left: its turn ends by itself
    assert ended == ev.TurnEnded(0, 1, idle=False)
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
    turn_of(gsm, 2)
    gsm.submit(AttackCommand(2, 4))
    assert unit(gsm, 4).hp == 7
    assert unit(gsm, 4).effects == (StatusEffect(EffectKind.VENOM, 1, 5, source_id=2),)
    result = gsm.submit(EndTurnCommand())  # the victim's turn is next
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
    )  # turn order: 1, 4, 2, 5, 3
    turn_of(gsm, 2)
    for _ in range(2):  # the second hit of the same rat refreshes its stack, it does not add one
        assert gsm.submit(AttackCommand(2, 5)).ok
    turn_of(gsm, 3)  # the victim's turn comes in between: one tick
    assert gsm.submit(AttackCommand(3, 5)).ok
    assert unit(gsm, 5).hp == 20 - 3 * 3 - 1
    assert unit(gsm, 5).effects == (
        StatusEffect(EffectKind.VENOM, 1, 4, source_id=2), StatusEffect(EffectKind.VENOM, 1, 5, source_id=3),
    )
    ticks = [e for e in turn_of(gsm, 5) if isinstance(e, ev.EffectTicked)]
    assert [(t.unit_id, t.amount, t.hp_after) for t in ticks] == [(5, 1, 9), (5, 1, 8)]
    assert unit(gsm, 5).hp == 8
    assert [e.remaining_turns for e in unit(gsm, 5).effects] == [3, 4]


def test_start_of_turn_order_regen_then_venom_then_death():
    gsm = two_player_game()
    victim = gsm._state.units[4]
    victim.hp = 1
    victim.effects = (StatusEffect(EffectKind.VENOM, 1, 3),)
    turn_of(gsm, 2)
    result = gsm.submit(EndTurnCommand())  # unit 4's turn
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
    turn_of(gsm, 2)
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
    turn_of(gsm, 2)
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
    assert gsm.active_player == 2  # Player 2 is out
    gsm.submit(EndTurnCommand())
    assert gsm.active_unit == 2 and gsm.view().round == 1
    gsm.submit(EndTurnCommand())
    assert gsm.active_unit == 1 and gsm.view().round == 2


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
    turn_of(gsm, 2)
    gsm.submit(MoveCommand(2, Position(4, 0)))  # rejected
    gsm.submit(MoveCommand(2, Position(2, 1)))
    gsm.submit(EndTurnCommand())
    assert gsm.command_log == [EndTurnCommand(), EndTurnCommand(), MoveCommand(2, Position(2, 1)), EndTurnCommand()]


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
    turn_of(standard, 2)
    turn_of(cheap, 2)
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
    """P0 Soldier (0,1) with 6 EN, at its turn; P1 Soldier (3,1). Only (2,1) is in reach (4 EN) and next to the target.

    Its turn comes after two other turns: the command log starts with two ``EndTurnCommand``s.
    """
    gsm = two_player_game(p0_soldier=(0, 1), p1_soldier=(3, 1), **kwargs)
    turn_of(gsm, 2)
    return gsm


def test_attack_out_of_range_moves_first_then_attacks():
    gsm = far_game()
    result = gsm.submit(AttackCommand(2, 4))
    assert result.ok
    moved, hit = result.events[:2]  # then its turn ends: no energy left
    assert isinstance(moved, ev.UnitMoved) and isinstance(hit, ev.UnitAttacked)
    assert moved.path[0] == Position(0, 1) and moved.path[-1] == Position(2, 1)
    assert moved.cost == 4 and moved.energy_after == 2
    assert (hit.source, hit.target) == (Position(2, 1), Position(3, 1))
    assert hit.result.damage == 3 and hit.energy_after == 0
    assert unit(gsm, 2).position == Position(2, 1) and unit(gsm, 4).hp == 7


def test_move_attack_is_logged_with_the_tile_it_was_made_from():
    gsm = far_game()
    gsm.submit(AttackCommand(2, 4))
    assert gsm.command_log[2:] == [AttackCommand(2, 4, Position(2, 1))]


def test_attack_in_range_never_moves():
    gsm = two_player_game(p1_soldier=(2, 1))
    turn_of(gsm, 2)
    result = gsm.submit(AttackCommand(2, 4))
    assert [type(e) for e in result.events] == [ev.UnitAttacked]
    assert unit(gsm, 2).position == Position(1, 1)
    assert gsm.command_log[2:] == [AttackCommand(2, 4)]


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
    turn_of(gsm, 2)
    before, log = gsm.view(), list(gsm.command_log)
    assert gsm.plan_attack(2, 4) is None
    result = gsm.submit(AttackCommand(2, 4))
    assert not result.ok and "cannot be reached" in result.error
    assert gsm.view() == before and gsm.command_log == log


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
    assert gsm.command_log == [EndTurnCommand(), EndTurnCommand()]
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
    turn_of(gsm, 2)
    plan = gsm.plan_attack(2, 4)
    assert plan.origin == Position(1, 1) and plan.move_cost == 2


def test_equal_cost_picks_the_tile_with_more_damage():
    # From the hill at (1,0): stay high on (1,1) or walk down to (2,0). Both cost 2 EN; the high tile hits harder.
    board = Board.from_strings([".....", ".....", "....."], ["01000", "01000", "00000"])
    gsm = custom_game(board, [(0, KING, (0, 0)), (0, SOLDIER, (1, 0)), (1, KING, (4, 2)), (1, SOLDIER, (2, 1))])
    turn_of(gsm, 2)
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
    turn_of(gsm, 2)
    assert gsm.plan_attack(2, 4).origin == Position(3, 1)
    flat = custom_game(
        FLAT, [(0, KING, (0, 0)), (0, SOLDIER, (1, 1)), (1, KING, (4, 2)), (1, SOLDIER, (3, 0))],
        engine=CheapMovesEngine(),
    )
    turn_of(flat, 2)
    assert flat.plan_attack(2, 4).origin == Position(2, 0)  # full tie: top row first


def test_replaying_the_command_log_gives_the_same_game():
    gsm = far_game()
    gsm.submit(AttackCommand(2, 4))  # no energy left: unit 4's turn starts by itself
    gsm.submit(AttackCommand(4, 2))  # P1 hits back in place
    turn_of(gsm, 2)  # round 2
    gsm.submit(AttackCommand(2, 4))
    replay = two_player_game(p0_soldier=(0, 1), p1_soldier=(3, 1))
    for command in gsm.command_log:
        assert replay.submit(command).ok
    assert replay.view() == gsm.view()


# ---------------------------------------------------------------- wrapper command
def neighbours_game():
    """P0 Soldier 2 at (1,1) with 6 EN at its turn, next to P1 Soldier 4 at (2,1). One hit: 3 damage, 2 EN."""
    gsm = two_player_game(p1_soldier=(2, 1))
    turn_of(gsm, 2)
    return gsm


def test_every_command_has_a_kind():
    move, wrapper = MoveCommand(1, Position(0, 0)), WrapperCommand([EndTurnCommand()])
    commands = [move, AttackCommand(1, 2), EndTurnCommand(), wrapper]
    kinds = [CommandType.MOVE, CommandType.ATTACK, CommandType.END_TURN, CommandType.WRAPPER]
    assert [c.kind for c in commands] == kinds
    assert all(isinstance(c, Command) for c in commands)
    assert wrapper.commands == (EndTurnCommand(),)  # a list is stored as a tuple


def test_wrapper_runs_its_commands_in_order_and_is_logged_as_one():
    gsm = neighbours_game()
    wrapper = WrapperCommand((AttackCommand(2, 4), MoveCommand(2, Position(1, 2)), EndTurnCommand()))
    result = gsm.submit(wrapper)
    assert result.ok
    kinds = [type(e) for e in result.events]
    assert kinds[:4] == [ev.UnitAttacked, ev.UnitMoved, ev.TurnEnded, ev.TurnStarted]
    assert unit(gsm, 4).hp == 7 and unit(gsm, 2).position == Position(1, 2) and gsm.active_unit != 2
    assert gsm.command_log[2:] == [wrapper]
    replay = two_player_game(p1_soldier=(2, 1))
    for command in gsm.command_log:
        assert replay.submit(command).ok
    assert replay.view() == gsm.view()


def test_wrapper_stops_at_a_rejected_command_and_keeps_what_already_ran():
    gsm = neighbours_game()
    result = gsm.submit(WrapperCommand((AttackCommand(2, 4), MoveCommand(2, Position(4, 0)), EndTurnCommand())))
    assert not result.ok and result.error.startswith("Command 2 of 3 rejected")
    assert [type(e) for e in result.events] == [ev.UnitAttacked]  # the attack happened
    assert unit(gsm, 4).hp == 7 and gsm.active_unit == 2  # still its turn (4 EN left)
    assert gsm.command_log[2:] == [WrapperCommand((AttackCommand(2, 4),))]


def test_wrapper_never_acts_after_the_units_turn_is_over():
    gsm = neighbours_game()
    attacks = [AttackCommand(2, 4)] * 3  # 3 x 2 EN: the GSM ends the turn after the third hit
    result = gsm.submit(WrapperCommand((*attacks, EndTurnCommand())))  # the end turn would hit the next unit
    assert not result.ok and "already over" in result.error
    assert unit(gsm, 4).hp == 1 and gsm.active_unit == 4  # unit 4's turn was not ended
    assert gsm.command_log[2:] == [WrapperCommand(tuple(attacks))]


def test_wrapper_rules_are_checked_before_anything_runs():
    gsm = neighbours_game()
    before, log = gsm.view(), list(gsm.command_log)
    hit = AttackCommand(2, 4)
    assert "at least one" in gsm.submit(WrapperCommand(())).error
    assert "only move, attack" in gsm.submit(WrapperCommand((hit, WrapperCommand((hit,))))).error
    assert "last command" in gsm.submit(WrapperCommand((EndTurnCommand(), hit))).error
    assert gsm.view() == before and gsm.command_log == log


# ---------------------------------------------------------------- possible actions
def unit_ids(action):
    """Ids of the units an action uses."""
    steps = action.commands if isinstance(action, WrapperCommand) else (action,)
    used = [c for c in steps if c.kind is not CommandType.END_TURN]
    return {c.attacker_id if isinstance(c, AttackCommand) else c.unit_id for c in used}


def test_possible_actions_are_whole_turns():
    gsm = neighbours_game()
    actions = gsm.find_all_possible_actions()
    end, hit = EndTurnCommand(), AttackCommand(2, 4)
    assert actions[0] == end  # wait
    assert hit not in actions  # 4 EN left after one hit: the turn would go on, so it needs an end turn
    assert WrapperCommand((hit, end)) in actions
    assert WrapperCommand((hit, hit, hit)) in actions  # 0 EN left: the GSM ends the turn, no end turn needed
    assert WrapperCommand((hit, MoveCommand(2, Position(1, 2)), end)) in actions  # hit, step away, 2 EN kept
    assert WrapperCommand((MoveCommand(2, Position(1, 2)), end)) in actions
    assert MoveCommand(2, Position(3, 0)) in actions  # 3 steps = 6 EN: nothing left, the turn ends by itself
    assert len(set(actions)) == len(actions)
    for action in actions:  # moves are never chained, and a move comes only last (before end turn)
        steps = action.commands if isinstance(action, WrapperCommand) else (action,)
        assert len(steps) >= 2 or not isinstance(action, WrapperCommand)  # one step = the command itself
        moves = [i for i, c in enumerate(steps) if c.kind is CommandType.MOVE]
        assert len(moves) <= 1 and all(i >= len(steps) - 2 for i in moves)
        assert all(c.kind is not CommandType.END_TURN for c in steps[:-1])


def test_every_possible_action_is_accepted_and_ends_the_turn():
    for action in neighbours_game().find_all_possible_actions():
        gsm = neighbours_game()  # one action per game: an action changes the state
        result = gsm.submit(action)
        assert result.ok, (action, result.error)
        assert ev.TurnEnded(0, 2, idle=action == EndTurnCommand()) in result.events
        assert gsm.active_unit != 2


def test_possible_actions_move_first_with_an_explicit_tile():
    gsm = far_game()  # only (2,1) is in reach and next to the target
    actions = gsm.find_all_possible_actions()
    assert AttackCommand(2, 4, Position(2, 1)) in actions  # 0 EN left afterwards: a single command
    assert AttackCommand(2, 4) not in actions
    assert gsm.submit(AttackCommand(2, 4, Position(2, 1))).ok


def test_possible_actions_do_not_change_the_game_and_are_cached():
    gsm = neighbours_game()
    state, log = gsm._state, list(gsm.command_log)
    expected = state.copy()
    actions = gsm.find_all_possible_actions()
    assert gsm._state is state and state == expected and gsm.command_log == log  # every try was rolled back
    actions.clear()  # the caller gets its own list
    assert gsm.find_all_possible_actions() == neighbours_game().find_all_possible_actions()
    gsm.submit(EndTurnCommand())
    assert all(unit_ids(a) == {gsm.active_unit} for a in gsm.find_all_possible_actions() if a != EndTurnCommand())
    gsm.rollback()  # back to the first state: the cached list is valid again
    assert gsm.find_all_possible_actions() == neighbours_game().find_all_possible_actions()


def test_unit_that_cannot_act_can_only_end_its_turn():
    gsm = neighbours_game()
    gsm._state.units[2].energy = 0
    assert gsm.find_all_possible_actions() == [EndTurnCommand()]


def test_killing_the_last_boss_ends_the_list_of_commands():
    gsm = two_player_game(p0_soldier=(3, 2))  # next to the P1 King (4,2) and the P1 Soldier (3,1)
    gsm._state.units[3].hp = 1
    turn_of(gsm, 2)
    actions = gsm.find_all_possible_actions()
    assert AttackCommand(2, 3) in actions
    assert not any(isinstance(a, WrapperCommand) and a.commands[0] == AttackCommand(2, 3) for a in actions)
    assert isinstance(gsm.submit(AttackCommand(2, 3)).events[-1], ev.GameOver)
    assert gsm.find_all_possible_actions() == []


# ---------------------------------------------------------------- rollback
def test_rollback_undoes_every_kind_of_command_exactly():
    gsm = two_player_game(p1_soldier=(2, 1))  # turn order: 1, 3, 2, 4
    commands = [
        EndTurnCommand(),  # the next unit's turn starts: it regenerates
        WrapperCommand((MoveCommand(3, Position(3, 2)), EndTurnCommand())),
        AttackCommand(2, 4),
        WrapperCommand((AttackCommand(2, 4), MoveCommand(2, Position(4, 0)))),  # rejected part-way: the hit stays
        AttackCommand(2, 4),  # 0 EN: the GSM ends the turn by itself
    ]
    saved = []
    for command in commands:
        saved.append((gsm._state.copy(), list(gsm.command_log)))
        gsm.submit(command)
    assert len(gsm.command_log) == len(commands) and unit(gsm, 4).hp == 1 and gsm.active_unit == 4
    end = gsm.view()
    for state, log in reversed(saved):
        assert gsm.rollback() is not None
        assert gsm._state == state and gsm.command_log == log
    assert gsm.rollback() is None  # back at the start of the game
    for command in commands:  # the game goes on normally after a rollback
        gsm.submit(command)
    assert gsm.view() == end


def test_rollback_of_a_won_game_brings_the_army_back():
    weak_king = replace(KING, hp=3)
    gsm = custom_game(FLAT, [(0, SOLDIER, (0, 0)), (1, weak_king, (1, 0)), (1, SOLDIER, (4, 2))])
    before, expected = gsm.view(), gsm._state.copy()
    gsm.submit(AttackCommand(1, 2))
    assert gsm.phase is Phase.GAME_OVER and gsm.view().units_of(1) == []
    assert gsm.rollback() == AttackCommand(1, 2)
    assert gsm.phase is Phase.AWAITING_COMMAND and gsm.view() == before and gsm._state == expected
    assert gsm.submit(AttackCommand(1, 2)).ok


def test_rollback_brings_back_the_same_objects():
    gsm = neighbours_game()
    state, soldier = gsm._state, gsm._state.units[2]
    gsm.submit(MoveCommand(2, Position(1, 2)))
    assert soldier.position == Position(1, 1)  # the command changed a copy
    gsm.rollback()
    assert gsm._state is state and gsm._state.units[2] is soldier


def test_rejected_command_leaves_nothing_to_roll_back():
    gsm = two_player_game()
    assert not gsm.submit(MoveCommand(1, Position(4, 0))).ok
    assert gsm.rollback() is None and gsm.command_log == []


def test_state_copy_shares_nothing_mutable():
    gsm = two_player_game(p1_soldier=(2, 1))
    gsm._state.units[4].hp = 3
    turn_of(gsm, 2)
    gsm.submit(AttackCommand(2, 4))  # unit 4 falls: ``fallen`` is not empty
    state = gsm._state
    clone = state.copy()
    assert clone == state
    for f in fields(state):
        original, copied = getattr(state, f.name), getattr(clone, f.name)
        if isinstance(original, (list, dict, set)):
            assert copied is not original, f.name
            items = original.values() if isinstance(original, dict) else original
            copied_items = copied.values() if isinstance(copied, dict) else copied
            for a, b in zip(items, copied_items):
                if is_dataclass(a) and not a.__dataclass_params__.frozen:
                    assert a is not b, f.name
