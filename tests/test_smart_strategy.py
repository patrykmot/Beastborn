"""SmartFindActionStrategy: a few sensible actions per unit turn (ATTACK / DEFENSE rules), never changes the game."""
import random
from dataclasses import replace

import pytest

from beastborn.control import (
    AllFindActionStrategy, BotPlayerController, BotVoice, Mood, SmartFindActionStrategy, StrategyMode,
)
from beastborn.domain import Board, Position
from beastborn.game import custom_game, new_game
from beastborn.game.commands import AttackCommand, MoveCommand, WrapperCommand
from tests.conftest import KING, SOLDIER

pytestmark = pytest.mark.usefixtures("scenario_rules")  # tests/conftest.py: a flat step costs 2 EN

LINES = {mood: ["..."] for mood in Mood}
WIDE = Board.from_strings([".......", ".......", "......."])


def parts(action):
    return action.commands if isinstance(action, WrapperCommand) else (action,)


def is_attack(action):
    return isinstance(parts(action)[0], AttackCommand)


def end(action, start):
    """Where the unit stands after the turn."""
    for c in parts(action):
        if isinstance(c, MoveCommand):
            start = c.destination
        elif isinstance(c, AttackCommand) and c.move_to is not None:
            start = c.move_to
    return start


def front_game(boss_hp=None):
    """P1 Soldier (1) at (4,1) next to the P2 Soldier (3) at (5,1); P1 King (2) far behind at (0,0). Unit 1 moves."""
    gsm = custom_game(WIDE, [(0, SOLDIER, (4, 1)), (0, KING, (0, 0)), (1, SOLDIER, (5, 1)), (1, KING, (6, 2))])
    if boss_hp is not None:
        gsm._state.units[2].hp = boss_hp
    return gsm


def snapshot(gsm):
    return list(gsm.command_log), gsm.view(), gsm.version


# ---------------------------------------------------------------- general
def test_at_most_max_actions_all_possible_and_the_game_untouched():
    gsm = new_game(2, seed=7)
    strategy = SmartFindActionStrategy(rng=random.Random(1))
    for _ in range(8):
        before = snapshot(gsm)
        everything = AllFindActionStrategy().find_all_possible_actions(gsm)
        chosen = strategy.find_all_possible_actions(gsm)
        assert snapshot(gsm) == before
        assert 1 <= len(chosen) <= 5 and len(set(chosen)) == len(chosen)
        assert set(chosen) <= set(everything)
        assert strategy.find_all_possible_actions(gsm) == chosen  # cached for the state
        gsm.submit(chosen[0])


def test_max_actions_is_a_parameter():
    gsm = front_game()
    assert len(SmartFindActionStrategy(2).find_all_possible_actions(gsm)) == 2
    assert len(SmartFindActionStrategy(8).find_all_possible_actions(gsm)) == 8
    with pytest.raises(ValueError):
        SmartFindActionStrategy(0)


def test_a_winning_hit_is_always_kept():
    gsm = custom_game(WIDE, [(0, SOLDIER, (1, 1)), (0, KING, (0, 0)), (1, replace(KING, hp=3), (2, 1))])
    (only,) = SmartFindActionStrategy(1).find_all_possible_actions(gsm)
    assert parts(only)[0] == AttackCommand(1, 3)


# ---------------------------------------------------------------- modes
def test_mode_attack_while_the_boss_is_above_half_hp():
    assert SmartFindActionStrategy().mode(front_game()) is StrategyMode.ATTACK
    assert SmartFindActionStrategy().mode(front_game(boss_hp=11)) is StrategyMode.ATTACK  # 11 > 20 / 2
    assert SmartFindActionStrategy().mode(front_game(boss_hp=10)) is StrategyMode.DEFENSE


def test_mode_attack_when_only_the_enemy_boss_is_left():
    gsm = custom_game(WIDE, [(0, SOLDIER, (4, 1)), (0, KING, (0, 0)), (1, KING, (6, 2))])
    gsm._state.units[2].hp = 1
    assert SmartFindActionStrategy().mode(gsm) is StrategyMode.ATTACK


def test_attack_mode_attacks_first_then_moves_to_the_closest_enemy_and_the_boss():
    gsm = front_game()
    chosen = SmartFindActionStrategy().find_all_possible_actions(gsm)
    attacks = [a for a in chosen if is_attack(a)]
    moves = [a for a in chosen if not is_attack(a)]
    assert is_attack(chosen[0]) and 1 <= len(attacks) <= 3 and len(moves) == 2
    start = Position(4, 1)
    assert any(end(a, start).manhattan(Position(5, 1)) == 1 for a in moves)  # next to the closest enemy
    assert any(end(a, start).manhattan(Position(6, 2)) == 1 for a in moves)  # next to the enemy Boss


def test_defense_mode_moves_home_and_attacks_only_on_the_way_home():
    gsm = front_game(boss_hp=5)
    start, home = Position(4, 1), Position(0, 0)
    chosen = SmartFindActionStrategy().find_all_possible_actions(gsm)
    moves = [a for a in chosen if not is_attack(a)]
    attacks = [a for a in chosen if is_attack(a)]
    assert len(moves) >= 3
    every_move = [a for a in AllFindActionStrategy().find_all_possible_actions(gsm) if not is_attack(a)]
    assert end(moves[0], start).manhattan(home) == min(end(a, start).manhattan(home) for a in every_move)
    assert attacks, "hit and step back towards the Boss"
    for a in attacks:
        distance = end(a, start).manhattan(home)
        assert distance < start.manhattan(home) or distance <= 2


def test_the_boss_keeps_away_from_enemies():
    # P1 King (1) at (2,1) next to the P2 Soldier (3) at (3,1).
    gsm = custom_game(WIDE, [(0, KING, (2, 1)), (0, SOLDIER, (0, 0)), (1, SOLDIER, (3, 1)), (1, KING, (6, 2))])
    start = Position(2, 1)
    enemies = [Position(3, 1), Position(6, 2)]

    def safety(action):
        return min(end(action, start).manhattan(e) for e in enemies)

    chosen = SmartFindActionStrategy().find_all_possible_actions(gsm)
    moves = [a for a in chosen if not is_attack(a)]
    every_move = [a for a in AllFindActionStrategy().find_all_possible_actions(gsm) if not is_attack(a)]
    assert safety(moves[0]) == max(safety(a) for a in every_move) > 1  # it steps away from the Soldier
    assert sum(is_attack(a) for a in chosen) <= 1


# ---------------------------------------------------------------- with the bot
def test_bot_with_smart_strategy_plays_and_workers_agree():
    gsm = new_game(2, seed=7)
    for _ in range(3):
        before = snapshot(gsm)
        one = BotPlayerController(4, BotVoice(LINES), random.Random(0), 1, SmartFindActionStrategy())
        two = BotPlayerController(4, BotVoice(LINES), random.Random(0), 2, SmartFindActionStrategy())
        chosen = one.choose_command(gsm)
        assert two.choose_command(gsm) == chosen and two.last_value == one.last_value
        assert snapshot(gsm) == before
        assert gsm.submit(chosen).ok


def test_smart_strategy_for_a_worker():
    plain = SmartFindActionStrategy()
    assert plain.for_worker(3) is plain  # no randomness: the same strategy
    seeded = SmartFindActionStrategy(4, random.Random(0)).for_worker(3)
    assert seeded.max_actions == 4 and seeded.rng.random() == random.Random(3).random()
