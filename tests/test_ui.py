"""UI presenter tests (frontend-neutral)."""
from beastborn.control import PassController
from beastborn.domain import Board, Position
from beastborn.game import Phase, custom_game
from beastborn.ui.interaction import Interaction
from beastborn.ui.interface import Cancel, ClickTile, EndTurn, HoverTile, Quit
from tests.conftest import KING, SOLDIER

FLAT = Board.from_strings([".....", ".....", "....."])


def scenario():
    gsm = custom_game(FLAT, [(0, KING, (0, 0)), (0, SOLDIER, (1, 1)), (1, KING, (4, 2)), (1, SOLDIER, (3, 1))])
    return gsm, Interaction(gsm)


def test_click_own_unit_selects_and_shows_reachable():
    _, ui = scenario()
    ui.handle(ClickTile(Position(1, 1)))
    assert ui.selected == 2
    assert Position(2, 1) in ui.reachable
    ui.handle(ClickTile(Position(1, 1)))  # click again = deselect
    assert ui.selected is None and ui.reachable == {}


def test_click_tile_moves_and_click_enemy_attacks():
    gsm, ui = scenario()
    ui.handle(ClickTile(Position(1, 1)))
    ui.handle(ClickTile(Position(2, 1)))
    assert gsm.view().unit(2).position == Position(2, 1)
    assert ui.selected == 2  # stays selected after moving
    assert 4 in ui.targets
    ui.handle(HoverTile(Position(3, 1)))
    assert ui.hover_attack().damage == 3
    ui.handle(ClickTile(Position(3, 1)))
    assert gsm.view().unit(4).hp == 7
    assert any("hit" in line for line in ui.log)


def test_errors_are_shown_not_raised():
    _, ui = scenario()
    ui.handle(ClickTile(Position(1, 1)))
    ui.handle(ClickTile(Position(4, 2)))  # enemy King: out of range and too far to walk to
    assert ui.message_is_error and "range" in ui.message
    ui.handle(Cancel())
    ui.handle(ClickTile(Position(4, 2)))  # nothing selected -> enemy info message
    assert ui.message_is_error and "belongs to" in ui.message


def test_end_turn_and_cancel():
    gsm, ui = scenario()
    ui.handle(ClickTile(Position(1, 1)))
    ui.handle(Cancel())
    assert ui.selected is None
    ui.handle(EndTurn())
    assert gsm.active_player == 1
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


def test_no_commands_after_game_over():
    gsm, ui = scenario()
    gsm._state.phase = Phase.GAME_OVER
    ui.handle(EndTurn())
    assert gsm.command_log == []


def test_main_parses_arguments():
    from main import parse_args

    args = parse_args(["--port", "9000", "--reload"])
    assert (args.host, args.port, args.reload) == ("127.0.0.1", 9000, True)
    assert parse_args([]).port == 8000


def test_click_far_enemy_moves_then_attacks_and_hover_shows_the_planned_hit():
    gsm = custom_game(FLAT, [(0, KING, (0, 0)), (0, SOLDIER, (0, 1)), (1, KING, (4, 2)), (1, SOLDIER, (3, 1))])
    ui = Interaction(gsm)
    ui.handle(ClickTile(Position(0, 1)))
    assert ui.targets == {} and set(ui.attack_plans) == {4}
    ui.handle(HoverTile(Position(3, 1)))
    assert ui.hover_plan().origin == Position(2, 1)
    assert ui.hover_attack() == ui.hover_plan().result
    ui.handle(ClickTile(Position(3, 1)))
    assert gsm.view().unit(2).position == Position(2, 1) and gsm.view().unit(4).hp == 7
    assert "moved (0,1)->(2,1)" in ui.log[-2] and "hit" in ui.log[-1]
    walk, hit = ui.last_actions  # a move step, then an attack step
    assert walk.path[0] == Position(0, 1) and walk.path[-1] == Position(2, 1)
    assert hit.source == Position(2, 1) and hit.target == Position(3, 1)


def test_plain_move_is_recorded_for_animation():
    gsm, ui = scenario()
    ui.handle(ClickTile(Position(1, 1)))
    ui.handle(ClickTile(Position(1, 2)))
    (walk,) = ui.last_actions
    assert (walk.seq, walk.unit_id, walk.owner, walk.path) == (1, 2, 0, (Position(1, 1), Position(1, 2)))
    ui.handle(Cancel())  # any next input clears it
    assert ui.last_actions == []
