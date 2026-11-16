"""Beastborn Game Notation: the format, GSM listeners, recording games, and replaying them through Interaction."""
import dataclasses
import random

import pytest

from beastborn.control import BotPlayerController, ReplayController
from beastborn.control.find_action_strategy import AllFindActionStrategy, RandomAllFindActionStrategy
from beastborn.domain import Board, Position
from beastborn.game import (
    AttackCommand, CommandType, EndTurnCommand, GameListener, GameRecord, GameRecorder, MoveCommand, Phase,
    WrapperCommand, custom_game, new_game,
)
from beastborn.game.events import UnitMoved
from beastborn.game.bgn import (
    BgnAction, BgnEntry, BgnError, append_bgn, parse_bgn, parse_tile, read_bgn, replay, tile_name, write_bgn,
)
from beastborn.ui.interaction import Interaction
from beastborn.ui.replay import Replay
from tests.conftest import KING

MOVE, ATTACK, END = CommandType.MOVE, CommandType.ATTACK, CommandType.END_TURN
P = Position


def same_game(a, b) -> bool:
    """Two views show the same game (a Board compares by identity, so its text is compared)."""
    return dataclasses.replace(a, board=None) == dataclasses.replace(b, board=None) and str(a.board) == str(b.board)


def play_random_game(gsm, seed: int, max_commands: int = 2000, whole_turns: bool = True):
    """Random legal turns, attacks preferred so the game ends. ``whole_turns=False``: one command at a time, as a
    human clicks (attacks without ``move_to``: the GSM picks the tile and logs it)."""
    rng, strategy = random.Random(seed), AllFindActionStrategy()
    while gsm.phase is Phase.AWAITING_COMMAND and len(gsm.command_log) < max_commands:
        actions = strategy.find_all_possible_actions(gsm)
        attacks = [a for a in actions if isinstance(a, WrapperCommand) and any(c.kind is ATTACK for c in a.commands)]
        action = rng.choice(attacks or actions)
        if whole_turns or not isinstance(action, WrapperCommand):
            assert gsm.submit(action).ok
            continue
        unit = gsm.active_unit
        for part in action.commands:
            if isinstance(part, AttackCommand):
                part = AttackCommand(part.attacker_id, part.target_id)
            result = gsm.submit(part)
            assert result.ok, result.error
            if gsm.turn_over(unit, result.events):
                break
    return gsm


def recorded_game(seed: int = 3, whole_turns: bool = True, names=("Learner", "Opponent")):
    gsm = new_game(2, seed=seed, width=10, height=10, player_names=list(names))
    recorder = GameRecorder(gsm, game_id=1_700_000_000_000 + seed)
    play_random_game(gsm, seed, whole_turns=whole_turns)
    return gsm, recorder.record()


# ---------------------------------------------------------------- notation
def test_tiles_are_written_like_in_chess():
    assert tile_name(P(0, 0)) == "a1" and tile_name(P(2, 3)) == "c4" and tile_name(P(15, 15)) == "p16"
    for x in range(16):
        for y in range(16):
            assert parse_tile(tile_name(P(x, y))) == P(x, y)
    for bad in ("q1", "a0", "a17", "A1", "a", "1a", "a01", "x3"):
        with pytest.raises(BgnError):
            parse_tile(bad)
    with pytest.raises(BgnError):
        tile_name(P(16, 0))


def test_actions_and_entries():
    assert str(BgnAction(MOVE, P(2, 2))) == "c3"
    assert str(BgnAction(ATTACK, P(3, 3))) == "xd4"
    assert str(BgnAction(ATTACK, P(3, 3), P(2, 2))) == "c3xd4"
    assert str(BgnAction(END)) == "-"
    entry = BgnEntry(P(0, 0), (BgnAction(ATTACK, P(3, 3), P(2, 2)), BgnAction(ATTACK, P(4, 4)), BgnAction(END)), True)
    assert str(entry) == "a1:(c3xd4,xe5,-)"
    for text in ("a1:(c3xd4,xe5,-)", "b2:c3", "b2:xc3", "b2:-", "b2:(-)", "j10:(b2,-)"):
        assert str(BgnEntry.parse(text)) == text
    assert BgnEntry.parse("b2:(-)") != BgnEntry.parse("b2:-")  # a wrapper of one command is not that command
    for bad in ("b2", "b2:", "b2:()", "b2:c3,-", "b2:(c3", "b2:(c3,(d4))", "b2:yc3", "b2:c3xd4xe5"):
        with pytest.raises(BgnError):
            BgnEntry.parse(bad)
    with pytest.raises(BgnError):
        BgnAction(MOVE, P(1, 1), P(2, 2))  # only an attack moves first


def test_the_line_format():
    record = GameRecord(1793637382469, 42, 10, 10, ("Learner", "Opponent"), (
        BgnEntry.parse("a1:(b2,-)"), BgnEntry.parse("j10:(xh9,-)"), BgnEntry.parse("b2:c3"),
    ))
    line = "[1793637382469][42][10x10][2][Learner|Opponent][a1:(b2,-) j10:(xh9,-) b2:c3]"
    assert record.to_line() == line
    assert GameRecord.from_line(line) == record
    assert GameRecord.from_line("[1][2][12x10][3][a|b|c][]") == GameRecord(1, 2, 12, 10, ("a", "b", "c"))


def test_player_names_are_escaped():
    names = ("Bot [v2] | 100%", "new\nline", "%5B")
    record = GameRecord(1, 2, 10, 10, names)
    assert record.to_line() == "[1][2][10x10][3][Bot %5Bv2%5D %7C 100%25|new%0Aline|%255B][]"
    assert GameRecord.from_line(record.to_line()).player_names == names


@pytest.mark.parametrize("line", [
    "", "[1][2][10x10][2][a|b]", "[1][2][10][2][a|b][]", "[1][2][10x10][3][a|b][]", "[1][2][17x10][2][a|b][]",
    "[1][2][10x10][2][a|b][a1:c3 zz]", "[x][2][10x10][2][a|b][]", "[1][2][10x10][2][a|b][] tail",
])
def test_invalid_lines(line):
    with pytest.raises(BgnError):
        GameRecord.from_line(line)


def test_files_hold_several_games(tmp_path):
    first, second = GameRecord(1, 10, 10, 10, ("a", "b")), GameRecord(2, 20, 12, 12, ("c", "d"))
    path = tmp_path / "games.bgn"
    write_bgn(path, [first])
    append_bgn(path, second)
    assert path.read_bytes().count(b"\n") == 2 and b"\r" not in path.read_bytes()
    assert read_bgn(path) == [first, second]
    assert parse_bgn(f"\n{first.to_line()}\r\n\n{second.to_line()}\n") == [first, second]
    with pytest.raises(BgnError, match="Line 2"):
        parse_bgn(f"{first.to_line()}\nnot bgn\n")
    write_bgn(path, [second])  # a new file
    assert read_bgn(path) == [second]


# ---------------------------------------------------------------- listeners
class Spy(GameListener):
    def __init__(self):
        self.calls = []

    def on_command(self, gsm, command, result):
        self.calls.append(("command", command, result, len(gsm.command_log)))

    def on_rollback(self, gsm, command):
        self.calls.append(("rollback", command, None, len(gsm.command_log)))

    def log(self):
        """The command log as the calls built it."""
        log = []
        for kind, command, _result, _size in self.calls:
            if kind == "command":
                log.append(command)
            else:
                assert log.pop() == command
        return log


def test_listeners_see_every_logged_command_and_rollback():
    spy, late = Spy(), Spy()
    gsm = new_game(2, seed=1, width=10, height=10, listeners=[spy])
    assert not gsm.submit(MoveCommand(99, P(0, 0))).ok  # rejected: not logged, nobody is told
    assert spy.calls == []
    gsm.add_listener(late)
    gsm.submit(EndTurnCommand())
    gsm.rollback()
    gsm.remove_listener(late)
    gsm.submit(EndTurnCommand())
    assert [(kind, command, size) for kind, command, _result, size in spy.calls] == [
        ("command", EndTurnCommand(), 1), ("rollback", EndTurnCommand(), 0), ("command", EndTurnCommand(), 1)]
    assert late.calls == spy.calls[:2]
    assert custom_game(Board.from_strings(["...."]), [(0, KING, (0, 0)), (1, KING, (3, 0))],
                       listeners=[late]).submit(EndTurnCommand()).ok and len(late.calls) == 3


def test_listeners_follow_the_log_through_searches_and_get_the_logged_command():
    """Strategies try attacks and undo them: the listener's log is still the GSM's. An attack that moved first is
    logged with the tile it moved to (the GSM picked it)."""
    spy = Spy()
    gsm = play_random_game(new_game(2, seed=5, width=10, height=10, listeners=[spy]), 5, whole_turns=False)
    assert spy.log() == gsm.command_log
    assert any(kind == "rollback" for kind, *_ in spy.calls)
    moved_attacks = [(command, result) for kind, command, result, _ in spy.calls
                     if kind == "command" and isinstance(command, AttackCommand) and any(isinstance(e, UnitMoved) for e in result.events)]
    assert moved_attacks
    for command, result in moved_attacks:
        assert command.move_to == next(e for e in result.events if isinstance(e, UnitMoved)).path[-1]


def test_a_wrapper_rejected_part_way_is_logged_with_the_part_that_ran():
    spy = Spy()
    gsm = new_game(2, seed=6, width=10, height=10, listeners=[spy])
    recorder = GameRecorder(gsm)
    unit = gsm.active_unit
    start = gsm.unit_position(unit)
    destination = next(iter(gsm.reachable_tiles(unit)))
    result = gsm.submit(WrapperCommand([MoveCommand(unit, destination), MoveCommand(unit, P(99, 99))]))
    [(kind, command, logged_result, _size)] = spy.calls
    assert not result.ok and logged_result is result and command == WrapperCommand((MoveCommand(unit, destination),))
    assert gsm.unit_position(unit) == destination != start and gsm.unit_position(12345) is None
    assert str(recorder.record().entries[0]) == f"{tile_name(start)}:({tile_name(destination)})"
    assert replay(recorder.record()).command_log == gsm.command_log


# ---------------------------------------------------------------- recording and replaying
@pytest.mark.parametrize("whole_turns", [True, False], ids=["bot-like turns", "single commands"])
def test_a_recorded_game_replays_exactly(tmp_path, whole_turns):
    gsm, record = recorded_game(seed=3, whole_turns=whole_turns)
    assert gsm.phase is Phase.GAME_OVER  # a whole game, up to its winner
    assert record.seed == 3 and (record.width, record.height) == (10, 10)
    assert record.player_names == ("Learner", "Opponent") and len(record.entries) == len(gsm.command_log)

    path = tmp_path / "game.bgn"
    append_bgn(path, record)
    [loaded] = read_bgn(path)
    assert loaded == record

    copy = loaded.new_game()  # through Interaction, as a frontend watches it
    ui = Interaction(copy, ReplayController.for_players(loaded))
    while ui.tick():
        pass
    assert copy.command_log == gsm.command_log
    assert same_game(copy.view(), gsm.view()) and copy.view().winner == gsm.view().winner
    assert ui.log[-1].endswith("wins!")
    assert replay(loaded).command_log == gsm.command_log


def test_the_record_follows_rollbacks_and_ignores_bot_searches():
    gsm = new_game(2, seed=8, width=10, height=10, player_names=["Bot", "Random"])
    recorder = GameRecorder(gsm)
    bot = BotPlayerController(depth=2, workers=1, rng=random.Random(1),
                              find_action_strategy=RandomAllFindActionStrategy(random.Random(1)))
    for _ in range(6):  # each search submits and rolls back many commands on this very GSM
        assert gsm.submit(bot.choose_command(gsm)).ok
    gsm.submit(EndTurnCommand())
    gsm.rollback()  # undone for real: not in the record either
    record = recorder.record()
    assert len(record.entries) == len(gsm.command_log) == 6
    assert replay(record).command_log == gsm.command_log


def test_the_recorder_needs_a_new_game_made_by_new_game():
    gsm = new_game(2, seed=1, width=10, height=10)
    gsm.submit(EndTurnCommand())
    with pytest.raises(ValueError):
        GameRecorder(gsm)  # it would miss the first command
    with pytest.raises(ValueError):
        GameRecorder(new_game(2, seed=1, start=False))
    with pytest.raises(ValueError):  # no seed: the map cannot be built again
        GameRecorder(custom_game(Board.from_strings(["...."]), [(0, KING, (0, 0)), (1, KING, (3, 0))]))
    gsm = new_game(2, seed=1, width=10, height=10)
    recorder = GameRecorder(gsm)
    assert recorder.game_id > 1_700_000_000_000  # milliseconds since the epoch
    gsm.remove_listener(recorder)
    gsm.submit(EndTurnCommand())
    with pytest.raises(RuntimeError):
        recorder.record()


def test_a_record_that_does_not_fit_the_game_is_reported():
    _, record = recorded_game(seed=4)
    entry = record.entries[2]
    moved = dataclasses.replace(record, entries=(*record.entries[:2], dataclasses.replace(entry, origin=P(5, 5))))
    with pytest.raises(BgnError, match="stands on"):
        replay(moved)
    attack = next(i for i, e in enumerate(record.entries) if any(a.kind is ATTACK for a in e.actions))
    nobody = tuple(dataclasses.replace(a, tile=P(4, 4)) if a.kind is ATTACK else a
                   for a in record.entries[attack].actions)
    broken = dataclasses.replace(record, entries=(
        *record.entries[:attack], dataclasses.replace(record.entries[attack], actions=nobody)))
    with pytest.raises(BgnError):
        replay(broken)
    too_long = dataclasses.replace(record, entries=record.entries + (BgnEntry.parse("a1:-"),))
    with pytest.raises(BgnError, match="over"):
        replay(too_long)


def test_the_replay_controller_stops_after_the_last_entry():
    _, record = recorded_game(seed=2)
    short = dataclasses.replace(record, entries=record.entries[:3])
    gsm = short.new_game()
    controllers = ReplayController.for_players(short)
    assert set(controllers) == {0, 1} and controllers[0] is controllers[1]
    ui = Interaction(gsm, controllers)
    assert ui.watching
    assert [ui.tick() for _ in range(4)] == [True, True, True, False]
    assert controllers[0].choose_command(gsm) is None
    gsm.rollback()  # going back: the controller plays the same entry again
    assert controllers[0].choose_command(gsm) == record.entries[2].to_command(gsm)


# ---------------------------------------------------------------- watching (ui/replay.py)
def test_watching_a_game_forwards_and_backwards():
    gsm, record = recorded_game(seed=3)
    watch = Replay(record)
    assert (watch.position, watch.length, watch.at_end) == (0, len(record.entries), False)
    assert watch.result_text() == f"{record.player_names[gsm.view().winner]} wins"
    assert watch.scores() == [0, 0] and not watch.back()
    assert watch.step() and watch.position == 1
    watch.goto(10)
    log_at_10, view_at_10 = watch.log, watch.view
    watch.goto(watch.length)
    assert watch.at_end and not watch.step() and watch.error is None
    assert same_game(watch.view, gsm.view()) and watch.scores() == [gsm.score(0), gsm.score(1)]
    watch.goto(10)  # back: played again from the start, the very same game
    assert watch.position == 10 and watch.log == log_at_10 and same_game(watch.view, view_at_10)
    assert watch.back() and watch.position == 9
    watch.goto(-5)
    assert watch.position == 0 and watch.last_actions == []


def test_watching_shows_what_the_last_step_did():
    _, record = recorded_game(seed=3)
    watch = Replay(record)
    while watch.step():
        if any(a.kind is ATTACK for a in record.entries[watch.position - 1].actions):
            break
    assert any(step.__class__.__name__ == "AttackAnimation" for step in watch.last_actions)


def test_watching_a_stopped_or_broken_game():
    _, record = recorded_game(seed=3)
    stopped = dataclasses.replace(record, entries=record.entries[:20])
    assert Replay(stopped).result_text() == "Stopped after 20 commands: no winner"
    broken = dataclasses.replace(record, entries=(*record.entries[:5], BgnEntry.parse("p16:-")))
    watch = Replay(broken)
    watch.goto(watch.length)
    assert watch.position == 5 and watch.at_end and "Entry 6" in watch.error
    watch.goto(2)
    assert watch.error is None and watch.position == 2


# ---------------------------------------------------------------- training script (tools/train_ai_bot.py)
def test_training_records_every_game(tmp_path):
    pytest.importorskip("tinygrad")
    from tools.train_ai_bot import LEARNER_NAME, OPPONENT_NAME, train

    bgn = tmp_path / "train.bgn"
    bgn.write_text("old games\n", encoding="utf-8")
    train(games=2, max_turns=8, out=tmp_path / "ai.safetensors", log_file=None, seed=5, epochs=1, bgn_file=bgn)
    records = read_bgn(bgn)  # the old content is gone
    assert [r.player_names for r in records] == [(LEARNER_NAME, OPPONENT_NAME), (OPPONENT_NAME, LEARNER_NAME)]
    assert [r.seed for r in records] == [0, 1] and all(len(r.entries) == 8 for r in records)
    assert records[0].game_id <= records[1].game_id
    for record in records:
        assert len(replay(record).command_log) == 8
    train(games=1, max_turns=4, out=tmp_path / "ai.safetensors", log_file=None, seed=5, epochs=1, bgn_file=bgn)
    assert len(read_bgn(bgn)) == 1  # a new run starts a new file


# ---------------------------------------------------------------- the monitor's Games tab (tools/train_monitor.py)
def test_monitor_reads_new_games_as_they_are_written(tmp_path):
    pytest.importorskip("tkinter")
    from tools.train_monitor import BgnSource

    path = tmp_path / "train.bgn"
    path.write_text("", encoding="utf-8")
    source = BgnSource(path)
    assert not source.poll()
    first, second = GameRecord(1, 1, 10, 10, ("a", "b")), GameRecord(2, 2, 10, 10, ("a", "b"))
    append_bgn(path, first)
    with open(path, "a", encoding="utf-8") as file:
        file.write(second.to_line()[:20])  # a line still being written
    assert source.poll() and source.records == [first]
    with open(path, "a", encoding="utf-8") as file:
        file.write(second.to_line()[20:] + "\nbroken\n")
    assert source.poll() and source.records == [first, second] and source.skipped == 1
    write_bgn(path, [second, first, second])  # a new training run: a new file
    assert source.poll() and source.records == [second, first, second]


def test_monitor_games_tab(tmp_path):
    tk = pytest.importorskip("tkinter")
    from tools.train_monitor import Monitor

    _, record = recorded_game(seed=3)
    path = tmp_path / "train_ai_bot.bgn"
    write_bgn(path, [record])
    try:
        app = Monitor(path)
    except tk.TclError as error:  # no display (e.g. a headless server)
        pytest.skip(f"no display: {error}")
    try:
        app.withdraw()
        games = app.games
        [item] = games.tree.get_children()
        games.tree.selection_set(item)
        app.update()
        assert games.replay.record == record and games.replay.position == 0
        games.forward()
        games.forward()
        games.backward()
        assert games.replay.position == 1
        games.last()
        assert games.replay.at_end and "wins" in games.result.cget("text")
        games.toggle_play()  # at the end: plays again from the start
        assert games.playing and games.replay.position == 0
        games.pause()
        append_bgn(path, record)
        games.poll()
        assert len(games.tree.get_children()) == 2
    finally:
        app.destroy()
