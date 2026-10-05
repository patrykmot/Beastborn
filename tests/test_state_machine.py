from dataclasses import replace

import pytest

from beastborn.domain import Board, EffectKind, EffectSpec, Position, StatusEffect
from beastborn.engine import StandardCalculationEngine
from beastborn.game import (
    AttackCommand, EndTurnCommand, GameConfig, MoveCommand, Phase, custom_game,
)
from beastborn.game import events as ev
from tests.conftest import KING, SOLDIER

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
    assert hit.result.damage == 3  # 2*2 - DEF 1
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


def test_attack_limit_from_config():
    gsm = two_player_game(p1_soldier=(2, 1), config=GameConfig(max_attacks_per_turn=1))
    assert gsm.submit(AttackCommand(2, 4)).ok
    assert not gsm.submit(AttackCommand(2, 4)).ok


def test_attack_rejections():
    gsm = two_player_game(p0_soldier=(0, 1), p1_soldier=(3, 1))
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
    """The design document example played through the GSM."""
    board = Board.from_strings(["...", ".m.", "..."], ["010", "000", "000"])
    beast = replace(SOLDIER, atk=2, en_atk=2)
    target = replace(SOLDIER, defense=4, hp=10)
    gsm = custom_game(board, [(0, beast, (1, 0)), (1, target, (1, 1)), (0, KING, (0, 2)), (1, KING, (2, 2))])
    result = gsm.submit(AttackCommand(1, 2))
    assert result.events[0].result.damage == 4
    assert unit(gsm, 2).hp == 6


# ---------------------------------------------------------------- status effects
def test_venom_ticks_at_start_of_victims_turn():
    rat = replace(SOLDIER, on_hit=(EffectSpec(EffectKind.VENOM, 1, 3),))
    gsm = two_player_game(p1_soldier=(2, 1))
    gsm._state.units[2].stats = rat  # P0 soldier becomes a rat
    gsm.submit(AttackCommand(2, 4))
    assert unit(gsm, 4).hp == 7
    assert unit(gsm, 4).effects == (StatusEffect(EffectKind.VENOM, 1, 3),)
    result = gsm.submit(EndTurnCommand())
    ticks = [e for e in result.events if isinstance(e, ev.EffectTicked)]
    assert len(ticks) == 1 and ticks[0].unit_id == 4
    assert unit(gsm, 4).hp == 6
    assert unit(gsm, 4).effects == (StatusEffect(EffectKind.VENOM, 1, 2),)


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
    acid = replace(SOLDIER, on_hit=(EffectSpec(EffectKind.ACID, 1),))
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
