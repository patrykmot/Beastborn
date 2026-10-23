"""AIFindActionStrategy: the same actions as its base, best first by a tinygrad afterstate network; never changes the
game. The order never changes the bot's search value. LEARNING mode: replay memory, training, save / load."""
import os
import pickle
import random
import threading
from collections import Counter
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from tinygrad.nn.state import safe_load

from beastborn import constant as C
from beastborn.control import AIFindActionStrategy, AIMode, AllFindActionStrategy, BotPlayerController, BotVoice, Mood
from beastborn.control import ai_find_action_strategy as ai
from beastborn.domain import Board
from beastborn.game import custom_game, new_game
from beastborn.game.commands import AttackCommand, WrapperCommand
from tests.conftest import KING, SOLDIER

pytestmark = pytest.mark.usefixtures("scenario_rules")  # tests/conftest.py

FLAT = Board.from_strings(["." * 10] * 10)
LINES = {mood: ["..."] for mood in Mood}


def snapshot(gsm):
    """Everything a strategy could change by mistake (as in test_bot.py)."""
    return list(gsm.command_log), gsm.view(), gsm.score(0), gsm.score(1), gsm.version


def game_after(seed, turns):
    """A 10x10 game after ``turns`` random unit turns."""
    gsm = new_game(2, seed=seed, width=10, height=10)
    rng = random.Random(seed)
    for _ in range(turns):
        gsm.submit(rng.choice(AllFindActionStrategy().find_all_possible_actions(gsm)))
    return gsm


def winning_hit_game():
    """P1 Soldier (1) next to a nearly dead P2 King (3): one hit wins. Unit 1 is to move."""
    return custom_game(FLAT, [(0, SOLDIER, (1, 1)), (0, KING, (0, 0)), (1, replace(KING, hp=3), (2, 1)),
                              (1, SOLDIER, (8, 8))])


# ---------------------------------------------------------------- encoding
def test_encoding_planes():
    board = Board.from_strings(["m" + "." * 9] + ["." * 10] * 9, ["2" + "1" + "0" * 8] + ["0" * 10] * 9)
    gsm = custom_game(board, [(0, SOLDIER, (3, 1)), (0, KING, (0, 2)), (1, SOLDIER, (5, 5)), (1, KING, (9, 9))])
    gsm._state.units[3].hp = 5
    planes = AIFindActionStrategy().state_to_tensor(gsm, 0)
    assert planes.shape == (C.AI_CHANNELS, 10, 10) and planes.dtype == np.float32
    assert planes[ai.CH_ELEVATION, 0, 0] == 1.0 and planes[ai.CH_ELEVATION, 0, 1] == 1 / C.MAX_ELEVATION
    assert planes[ai.CH_DIFFICULT, 0, 0] == 1.0 and planes[ai.CH_DIFFICULT].sum() == 1.0
    assert planes[ai.CH_MY_UNITS, 1, 3] == 1.0 and planes[ai.CH_MY_UNITS].sum() == 2.0  # [channel, y, x]
    assert planes[ai.CH_ENEMY_HP, 5, 5] == 0.5
    soldier = gsm.view().units[0]
    assert planes[ai.CH_MY_ENERGY, 1, 3] == soldier.energy / SOLDIER.max_en
    assert planes[ai.CH_MY_BOSS, 2, 0] == 1.0 and planes[ai.CH_MY_BOSS].sum() == 1.0
    assert planes[ai.CH_ENEMY_BOSS, 9, 9] == 1.0 and planes[ai.CH_ENEMY_BOSS].sum() == 1.0


def test_encoding_from_the_other_side_swaps_my_and_enemy_planes():
    gsm = game_after(3, 4)
    strategy = AIFindActionStrategy()
    mine, theirs = strategy.state_to_tensor(gsm, 0), strategy._state_to_tensor(gsm, 1)
    assert np.array_equal(theirs, mine[[0, 1, 5, 6, 7, 2, 3, 4, 9, 8]])


def test_only_10x10_boards():
    with pytest.raises(ValueError, match="10x10"):
        AIFindActionStrategy().rank(new_game(2, seed=1, width=12, height=12))


# ---------------------------------------------------------------- ranking (WORKING)
def test_same_actions_and_the_game_untouched():
    for seed, turns in ((7, 0), (11, 6), (5, 15)):
        gsm = game_after(seed, turns)
        before = snapshot(gsm)
        actions = AIFindActionStrategy().find_all_possible_actions(gsm)
        assert snapshot(gsm) == before
        assert Counter(actions) == Counter(AllFindActionStrategy().find_all_possible_actions(gsm))


def test_new_network_gives_the_exact_score_order():
    gsm = game_after(11, 10)
    ranked = AIFindActionStrategy().rank(gsm)
    assert all(r.value == r.score / C.AI_SCORE_SCALE for r in ranked if abs(r.value) != float("inf"))
    assert [r.value for r in ranked] == sorted((r.value for r in ranked), reverse=True)
    me = gsm.active_player
    for r in ranked[:3]:  # the score is the mover's score difference right after that action
        gsm.submit(r.action)
        assert r.score == ai.score_difference(gsm, me)
        gsm.rollback()


def test_a_winning_hit_comes_first():
    best = AIFindActionStrategy().rank(winning_hit_game())[0]
    parts = best.action.commands if isinstance(best.action, WrapperCommand) else (best.action,)
    assert best.value == float("inf") and isinstance(parts[0], AttackCommand) and parts[0].target_id == 3


def test_ranking_is_cached_per_state():
    gsm = game_after(7, 2)
    strategy = AIFindActionStrategy()
    calls = []
    real = strategy.predict
    strategy.predict = lambda states: calls.append(len(states)) or real(states)
    first = strategy.find_all_possible_actions(gsm)
    assert strategy.find_all_possible_actions(gsm) == first and len(calls) == 1


def test_the_bot_finds_the_same_value_with_the_ai_order():
    """Alpha-beta gives the same value for any order of the same actions; the order only changes the cut-offs."""
    for seed, turns in ((7, 0), (11, 8), (5, 14)):
        gsm = game_after(seed, turns)
        plain = BotPlayerController(3, BotVoice(LINES), random.Random(0), 1, AllFindActionStrategy())
        smart = BotPlayerController(3, BotVoice(LINES), random.Random(0), 1, AIFindActionStrategy())
        before = snapshot(gsm)
        plain.choose_command(gsm)
        command = smart.choose_command(gsm)
        assert smart.last_value == plain.last_value
        assert snapshot(gsm) == before and gsm.submit(command).ok


def test_missing_weights_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        AIFindActionStrategy(weights=tmp_path / "none.safetensors")


# ---------------------------------------------------------------- learning
def planes(seed):
    return np.random.default_rng(seed).random((C.AI_CHANNELS, 10, 10), dtype=np.float32)


def test_memory_size_and_only_learning_mode_remembers():
    strategy = AIFindActionStrategy(AIMode.LEARNING, memory_size=5)
    for i in range(7):
        strategy.remember(planes(i), 0.1, planes(i + 1), False)
    assert len(strategy.memory) == 5 and strategy.train_step(8) is None
    with pytest.raises(RuntimeError):
        AIFindActionStrategy().remember(planes(0), 0.0, planes(1), True)


def test_targets():
    strategy = AIFindActionStrategy("LEARNING", gamma=0.5)
    strategy.remember(planes(0), 0.3, planes(1), True)
    strategy.remember(planes(2), 0.3, planes(3), False)
    done, going_on = strategy.targets(list(strategy.memory))
    assert done == pytest.approx(0.3)  # the game ended: just the reward
    assert going_on == pytest.approx(0.3 + 0.5 * strategy.predict(planes(3)[None].astype(np.float16)
                                                                  .astype(np.float32))[0], abs=1e-3)


def test_training_lowers_the_loss():
    strategy = AIFindActionStrategy(AIMode.LEARNING, seed=1)
    for i in range(16):
        strategy.remember(planes(i), (-1) ** i * 0.5, planes(i), True)
    losses = [strategy.train_step(16) for _ in range(40)]
    assert losses[-1] < losses[0] / 2


def test_save_load_and_pickle(tmp_path):
    learner = AIFindActionStrategy(AIMode.LEARNING, seed=2)
    for i in range(16):
        learner.remember(planes(i), 0.2 * i, planes(i), True)
    for _ in range(5):
        learner.train_step(16)
    path = learner.save(tmp_path / "net.safetensors")
    batch = np.stack([planes(i) for i in range(3)])
    worker = AIFindActionStrategy(weights=path)
    assert np.allclose(worker.predict(batch), learner.predict(batch), atol=1e-5)
    assert np.abs(worker.predict(batch)).max() > 0  # really trained, not the zero start
    copy = pickle.loads(pickle.dumps(worker))
    gsm = game_after(11, 5)
    assert copy.find_all_possible_actions(gsm) == worker.find_all_possible_actions(gsm)
    with pytest.raises(TypeError):
        pickle.dumps(learner)


@pytest.mark.skipif(not Path("/proc/self/maps").exists(), reason="checks open files through Linux /proc")
def test_weights_files_are_not_kept_open(tmp_path):
    """Windows cannot replace a file that is still open or mapped (the error of --load X --out X, or a second save)."""
    path = tmp_path / "net.safetensors"
    learner = AIFindActionStrategy(AIMode.LEARNING, seed=3)
    learner.save(path)
    loaded = AIFindActionStrategy(weights=path)
    loaded.predict(np.stack([planes(0)]))  # loads the file
    learner.save(path)  # the same file again
    open_files = {os.path.realpath(f"/proc/self/fd/{fd}") for fd in os.listdir("/proc/self/fd")}
    maps = Path("/proc/self/maps").read_text()
    assert str(path) not in open_files and str(path) not in maps
    assert not path.with_name(path.name + ".tmp").exists()
    # still a normal .safetensors file (tinygrad calls only on the tinygrad thread, as in the module)
    names = ai._on_tinygrad_thread(lambda: (set(safe_load(str(path))), set(ai.get_state_dict(ai.AfterstateNet()))))()
    assert names[0] == names[1]


def test_works_from_any_thread():
    """The web server asks from a new thread per request; tinygrad may only be used by one thread."""
    strategy = AIFindActionStrategy()
    batch = np.stack([planes(i) for i in range(70)])  # a batch size (128) no other test compiles
    here = strategy.predict(batch[:5])
    results, errors = [], []

    def request():
        try:
            results.append(strategy.predict(batch))
        except Exception as error:  # pragma: no cover - only when the fix is missing
            errors.append(error)

    thread = threading.Thread(target=request)
    thread.start()
    thread.join()
    assert not errors and np.allclose(results[0][:5], here)


# ---------------------------------------------------------------- training script (tools/train_ai_bot.py)
def test_training_script_plays_saves_and_logs(tmp_path):
    from tools.train_ai_bot import train

    out, log_file = tmp_path / "ai.safetensors", tmp_path / "train.log"
    summary = train(games=2, max_turns=12, out=out, log_file=log_file, seed=5)
    assert summary.games == 2 and summary.next_seed == 7 and out.is_file()
    text = log_file.read_text(encoding="utf-8")
    assert "game 1/2 seed 5 as P1" in text and "game 2/2 seed 6 as P2" in text and "--load" in text
    again = train(games=1, opponent="ai", load=out, out=out, log_file=None, max_turns=6)  # continue from the file
    assert again.games == 1
