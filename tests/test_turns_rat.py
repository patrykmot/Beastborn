"""Exploration test (no assertions): which whole turns ``AllFindActionStrategy._turns`` lists for a rat.

Board 7x7 flat grass. Big Rat (P0, id 1) in the middle at (3,3).
Two enemy Peasants (P1) at distance 2 from it: (3,1) above and (5,3) to the right.
Run with ``pytest tests/test_turns_rat.py -s`` to see the printed commands.
"""
from beastborn.control import AllFindActionStrategy
from beastborn.domain import Board
from beastborn.game import AttackCommand, EndTurnCommand, MoveCommand, custom_game
from tests.conftest import FIXED_BIG_RAT, FIXED_PEASANT


def _short(command):
    if isinstance(command, MoveCommand):
        return f"move {command.destination}"
    if isinstance(command, AttackCommand):
        where = "" if command.move_to is None else f" from {command.move_to}"
        return f"attack #{command.target_id}{where}"
    if isinstance(command, EndTurnCommand):
        return "end"
    return repr(command)


def test_turns_of_a_rat_between_two_enemies():
    board = Board.from_strings(["......."] * 7)
    gsm = custom_game(board, [
        (0, FIXED_BIG_RAT, (3, 3)),  # id 1: the rat, active first
        (1, FIXED_PEASANT, (3, 1)),  # id 2: enemy 2 tiles up
        (1, FIXED_PEASANT, (5, 3)),  # id 3: enemy 2 tiles right
    ])
    rat = gsm.view().unit(gsm.active_unit)
    print(f"\nactive unit: #{rat.id} {rat.stats.name} at {rat.position}, energy {rat.energy}")

    turns = AllFindActionStrategy()._turns(gsm, {})

    print(f"{len(turns)} possible turns:")
    for i, turn in enumerate(turns, 1):
        print(f"{i:3}. " + " -> ".join(_short(c) for c in turn))
