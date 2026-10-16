"""UI presenter tests (frontend-neutral)."""
import pytest

from beastborn import constant as C
from beastborn.control import PassController
from beastborn.domain import Board, Position
from beastborn.game import Phase, custom_game
from beastborn.ui.interaction import Interaction
from beastborn.ui.interface import ClickTile, EndTurn, HoverTile, Quit
from tests.conftest import KING, SOLDIER

pytestmark = pytest.mark.usefixtures("scenario_rules")  # tests/conftest.py

FLAT = Board.from_strings([".....", ".....", "....."])


def scenario():
    """Turn order: P1 King (1), P2 King (3), P1 Soldier (2), P2 Soldier (4)."""
    gsm = custom_game(FLAT, [(0, KING, (0, 0)), (0, SOLDIER, (1, 1)), (1, KING, (4, 2)), (1, SOLDIER, (3, 1))])
    return gsm, Interaction(gsm)


def soldier_turn(ui):
    ui.handle(EndTurn())
    ui.handle(EndTurn())
    assert ui.active_unit.id == 2


def test_the_game_picks_the_active_unit_and_shows_its_moves():
    gsm, ui = scenario()
    assert ui.active_unit.id == 1
    assert ui.reachable == gsm.reachable_tiles(1) and Position(1, 0) in ui.reachable
    ui.handle(ClickTile(Position(1, 1)))  # another own unit cannot be chosen
    assert ui.message_is_error and "now it is P1 King" in ui.message
    assert ui.active_unit.id == 1 and gsm.command_log == []


def test_click_tile_moves_and_click_enemy_attacks():
    gsm, ui = scenario()
    soldier_turn(ui)
    ui.handle(ClickTile(Position(2, 1)))
    assert gsm.view().unit(2).position == Position(2, 1)
    assert ui.active_unit.id == 2  # 4 EN left: still its turn
    assert not ui.attack_plans[4].needs_move
    ui.handle(HoverTile(Position(3, 1)))
    assert ui.hover_attack().damage == 3
    ui.handle(ClickTile(Position(3, 1)))
    assert gsm.view().unit(4).hp == 7
    assert any("hit" in line for line in ui.log)


def test_errors_are_shown_not_raised():
    _, ui = scenario()
    soldier_turn(ui)
    ui.handle(ClickTile(Position(4, 2)))  # enemy King: out of range and too far to walk to
    assert ui.message_is_error and "range" in ui.message
    ui.handle(ClickTile(Position(4, 0)))  # an empty tile out of reach: nothing happens
    assert ui.message == "" and ui.active_unit.id == 2


def test_end_turn_passes_to_the_next_unit():
    gsm, ui = scenario()
    ui.handle(EndTurn())
    assert (gsm.active_player, ui.active_unit.id) == (1, 3)
    assert ui.log[-1] == "P1 King waits"
    ui.handle(Quit())
    assert ui.quit_requested


def test_bot_controller_plays_its_turn():
    gsm = custom_game(FLAT, [(0, KING, (0, 0)), (1, KING, (4, 2))])
    ui = Interaction(gsm, controllers={1: PassController()})
    ui.handle(EndTurn())
    assert gsm.active_player == 1
    ui.handle(EndTurn())  # humans cannot act during the bot's turn
    assert gsm.active_player == 1
    ui.tick()
    assert gsm.active_player == 0 and gsm.view().round == 2
    assert "--- Round 2 ---" in ui.log


def test_no_commands_after_game_over():
    gsm, ui = scenario()
    gsm._state.phase = Phase.GAME_OVER
    ui.handle(EndTurn())
    assert gsm.command_log == []


def test_main_parses_arguments():
    from main import parse_args

    args = parse_args(["--port", "9000", "--reload"])
    assert (args.host, args.port, args.reload) == (C.DEFAULT_HOST, 9000, True)
    assert parse_args([]).port == C.DEFAULT_PORT


def test_click_far_enemy_moves_then_attacks_and_hover_shows_the_planned_hit():
    gsm = custom_game(FLAT, [(0, KING, (0, 0)), (0, SOLDIER, (0, 1)), (1, KING, (4, 2)), (1, SOLDIER, (3, 1))])
    ui = Interaction(gsm)
    soldier_turn(ui)
    assert set(ui.attack_plans) == {4} and ui.attack_plans[4].needs_move  # nothing in range from where it stands
    ui.handle(HoverTile(Position(3, 1)))
    assert ui.hover_plan().origin == Position(2, 1)
    assert ui.hover_attack() == ui.hover_plan().result
    ui.handle(ClickTile(Position(3, 1)))
    assert gsm.view().unit(2).position == Position(2, 1) and gsm.view().unit(4).hp == 7
    assert "moved (0,1)->(2,1)" in ui.log[-2] and "hit" in ui.log[-1]
    walk, hit = ui.last_actions  # a move step, then an attack step
    assert walk.path[0] == Position(0, 1) and walk.path[-1] == Position(2, 1)
    assert hit.source == Position(2, 1) and hit.target == Position(3, 1)
    assert ui.active_unit.id == 4  # no energy left: the game passed the turn by itself


def test_plain_move_is_recorded_for_animation():
    gsm, ui = scenario()
    ui.handle(ClickTile(Position(1, 0)))
    (walk,) = ui.last_actions
    assert (walk.seq, walk.unit_id, walk.owner, walk.path) == (1, 1, 0, (Position(0, 0), Position(1, 0)))
    ui.handle(ClickTile(Position(1, 1)))  # any next input clears it
    assert ui.last_actions == []
