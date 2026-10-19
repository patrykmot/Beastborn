"""Bot player: alpha-beta search (ALG) over whole unit turns, and its voice. Simple happy paths + the critical rule:
the search never changes the game."""
import random
from dataclasses import replace

import pytest

from beastborn import constant as C
from beastborn.control import BotPlayerController, BotVoice, Mood, load_bot_lines
from beastborn.domain import Board
from beastborn.game import Phase, custom_game, new_game
from beastborn.game.commands import AttackCommand, CommandType, WrapperCommand
from tests.conftest import KING, SOLDIER

pytestmark = pytest.mark.usefixtures("scenario_rules")  # tests/conftest.py

FLAT = Board.from_strings([".....", ".....", "....."])
LINES = {Mood.START: ["start!"], Mood.ATTACK: ["attack!"], Mood.DEFENSE: ["defense!"]}


def bot(depth=C.BOT_SEARCH_DEPTH):
    return BotPlayerController(depth, BotVoice(LINES, random.Random(0)))


def snapshot(gsm):
    """Everything a search could change by mistake."""
    return list(gsm.command_log), gsm.view(), gsm.score(0), gsm.score(1), gsm.find_all_possible_actions()


def attacks(command):
    parts = command.commands if isinstance(command, WrapperCommand) else (command,)
    return [c for c in parts if c.kind is CommandType.ATTACK]


def winning_hit_game():
    """P1 Soldier (1) next to a nearly dead P2 King (3): one hit wins. Unit 1 is to move."""
    weak_king = replace(KING, hp=3)
    return custom_game(FLAT, [(0, SOLDIER, (1, 1)), (0, KING, (0, 0)), (1, weak_king, (2, 1)), (1, SOLDIER, (4, 2))])


# ---------------------------------------------------------------- search
def test_bot_returns_a_possible_action():
    gsm = new_game(2, seed=7)
    command = bot().choose_command(gsm)
    assert command in gsm.find_all_possible_actions()
    assert gsm.submit(command).ok


def test_search_leaves_state_untouched():
    gsm = new_game(2, seed=7)
    for _ in range(3):  # a few turns into the game
        before = snapshot(gsm)
        command = bot().choose_command(gsm)
        assert snapshot(gsm) == before
        assert gsm.submit(command).ok


def test_state_untouched_after_error_in_search():
    class Broken(BotPlayerController):
        calls = 0

        def evaluate(self, gsm, me):
            self.calls += 1
            if self.calls > 3:
                raise ZeroDivisionError("bug in the scoring")
            return super().evaluate(gsm, me)

    gsm = new_game(2, seed=7)
    before = snapshot(gsm)
    with pytest.raises(ZeroDivisionError):
        Broken(3, BotVoice(LINES)).choose_command(gsm)
    assert snapshot(gsm) == before  # every trial move was rolled back on the way out


def minimax(gsm, me, depth, seen_twice):
    """Plain minimax with the bot's own values (the reference ALG must agree with)."""
    if gsm.phase is Phase.GAME_OVER:
        winner = gsm.view().winner
        return C.BOT_WIN_SCORE + depth if winner == me else -C.BOT_WIN_SCORE - depth
    if depth == 0:
        return gsm.score(me) - gsm.score(1 - me)
    player = gsm.active_player
    values = [_value_after(gsm, action, me, depth, seen_twice) for action in gsm.find_all_possible_actions()]
    return max(values) if player == me else min(values)


def _value_after(gsm, action, me, depth, seen_twice):
    player = gsm.active_player
    assert gsm.submit(action).ok
    try:
        if gsm.phase is not Phase.GAME_OVER and gsm.active_player == player:
            seen_twice.append(True)  # this player moves again right away
        return minimax(gsm, me, depth - 1, seen_twice)
    finally:
        gsm.rollback()


@pytest.mark.parametrize("depth", [1, 2, 3])
def test_alphabeta_equals_minimax(depth):
    # P1 has 2 units, P2 only 1: in the turn order P1 Soldier (2) and then (new round) P1 King (1) play in a row.
    gsm = custom_game(FLAT, [(0, KING, (0, 0)), (0, SOLDIER, (1, 1)), (1, KING, (4, 2))])
    gsm.submit(gsm.find_all_possible_actions()[0])  # P1 King waits
    gsm.submit(gsm.find_all_possible_actions()[0])  # P2 King waits: now P1 Soldier
    seen_twice = []
    expected = minimax(gsm, gsm.active_player, depth, seen_twice)
    player = bot(depth)
    player.choose_command(gsm)
    assert player.last_value == expected
    if depth >= 2:  # P1 Soldier, then P1 King at the start of round 2
        assert seen_twice, "the scenario must contain a player moving twice in a row"


@pytest.mark.parametrize("depth", [1, C.BOT_SEARCH_DEPTH])
def test_bot_takes_the_winning_hit(depth):
    gsm = winning_hit_game()
    command = bot(depth).choose_command(gsm)
    assert [a.target_id for a in attacks(command)] == [3]
    gsm.submit(command)
    assert gsm.phase is Phase.GAME_OVER and gsm.view().winner == 0


def test_bot_prefers_attack_to_waiting():
    # P1 Soldier (2) next to the P2 Soldier (4); the Kings are far away. Unit 1 (P1 King) waits first.
    gsm = custom_game(FLAT, [(0, KING, (0, 0)), (0, SOLDIER, (2, 1)), (1, KING, (4, 2)), (1, SOLDIER, (3, 1))])
    gsm.submit(gsm.find_all_possible_actions()[0])
    gsm.submit(gsm.find_all_possible_actions()[0])
    assert gsm.active_unit == 2
    command = bot(2).choose_command(gsm)
    assert attacks(command) and all(isinstance(a, AttackCommand) for a in attacks(command))


def test_equally_good_actions_are_picked_at_random():
    # The opening: nothing can be hit, so many moves are worth the same. The rng decides which one the bot plays.
    def opening(seed):
        return BotPlayerController(2, BotVoice(LINES), random.Random(seed)).choose_command(new_game(2, seed=7))

    assert opening(1) == opening(1)  # the same rng seed repeats the same game
    assert len({opening(seed) for seed in range(6)}) > 1


def test_search_is_logged(caplog):
    gsm = winning_hit_game()
    player = bot(2)
    with caplog.at_level("INFO", logger="beastborn.control.bot"):
        player.choose_command(gsm)
    (record,) = caplog.records
    assert player.last_moves >= len(gsm.find_all_possible_actions())  # every root action was tried at least
    assert f"best value {player.last_value}," in record.message
    assert f"{player.last_moves} moves calculated" in record.message


def test_bot_does_nothing_after_game_over():
    gsm = winning_hit_game()
    gsm.submit(AttackCommand(1, 3))
    assert bot().choose_command(gsm) is None


# ---------------------------------------------------------------- voice
def test_voice_moods():
    gsm = winning_hit_game()
    player = bot(1)
    player.choose_command(gsm)
    assert player.last_message == "start!"  # the first turn: START, whatever it does
    player.choose_command(gsm)
    assert player.last_message == "attack!"  # it attacks the weak King
    calm = custom_game(FLAT, [(0, KING, (0, 0)), (1, KING, (4, 2))])  # nothing in reach: move or wait
    player.choose_command(calm)
    assert player.last_message == "defense!"


def test_voice_never_repeats_the_last_line():
    voice = BotVoice({mood: ["one", "two"] for mood in Mood}, random.Random(1))
    said = [voice.line(Mood.ATTACK) for _ in range(30)]
    assert all(a != b for a, b in zip(said, said[1:]))
    assert BotVoice({mood: ["only"] for mood in Mood}).line(Mood.START) == "only"  # one line: repeating is fine


def test_bot_lines_file_has_every_mood(tmp_path):
    lines = load_bot_lines()
    assert set(lines) == set(Mood) and all(lines[mood] for mood in Mood)
    broken = tmp_path / "lines.json"
    broken.write_text('{"start": ["hi"], "attack": ["bonk"]}', encoding="utf-8")
    with pytest.raises(ValueError, match="defense"):
        load_bot_lines(broken)
