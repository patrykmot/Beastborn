"""UI presenter tests (no pygame) + a headless pygame smoke test."""
import pytest

from beastborn.control import PassController
from beastborn.domain import Board, Position
from beastborn.game import Phase, custom_game, new_game
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
    ui.handle(ClickTile(Position(3, 1)))  # enemy out of range
    assert ui.message_is_error and "range" in ui.message
    ui.handle(ClickTile(Position(3, 1)))
    ui.handle(ClickTile(Position(4, 2)))  # nothing selected -> enemy info message
    assert ui.message_is_error


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


@pytest.fixture
def headless(monkeypatch):
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")


def test_pygame_frontend_smoke(headless, tmp_path):
    pytest.importorskip("pygame")
    from beastborn.ui.pygame_ui.app import PygameFrontend
    from beastborn.ui.pygame_ui.layout import Layout

    script = [ClickTile(Position(1, 0)), HoverTile(Position(1, 1)), EndTurn()]

    class ScriptedInput:
        def __init__(self, layout):
            self.layout = layout

        def poll(self):
            return [script.pop(0)] if script else []

    gsm = new_game(4, seed=3)
    shot = tmp_path / "frame.png"
    ui = PygameFrontend(ScriptedInput).run(gsm, max_frames=5, screenshot=str(shot))
    assert shot.exists() and shot.stat().st_size > 0
    assert gsm.active_player == 1  # the scripted End Turn went through
    assert isinstance(ui, Interaction)

    layout = Layout.for_board(12, 12)
    assert layout.tile_at(0, 0) == Position(0, 0)
    assert layout.tile_at(layout.board_w + 5, 5) is None


def test_mouse_keyboard_translation(headless):
    pygame = pytest.importorskip("pygame")
    from beastborn.ui.pygame_ui.input_mouse_kb import MouseKeyboardInput
    from beastborn.ui.pygame_ui.layout import Layout

    layout = Layout.for_board(12, 12)
    adapter = MouseKeyboardInput(layout)
    t = layout.tile
    click = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(t * 2 + 1, t * 3 + 1))
    assert adapter.translate(click) == ClickTile(Position(2, 3))
    button = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=layout.end_turn_button.center)
    assert adapter.translate(button) == EndTurn()
    assert adapter.translate(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=3, pos=(1, 1))) == Cancel()
    assert adapter.translate(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_e)) == EndTurn()
    assert adapter.translate(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE)) == Cancel()
    assert adapter.translate(pygame.event.Event(pygame.QUIT)) == Quit()


def test_main_parses_arguments():
    from main import parse_args

    args = parse_args(["--players", "3", "--seed", "5"])
    assert (args.players, args.seed, args.width) == (3, 5, 12)

