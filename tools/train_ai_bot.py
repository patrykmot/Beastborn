"""Train the AI move-ordering network (``AIFindActionStrategy``) by playing games, with logs to follow progress.

Run from the project root::

    python tools/train_ai_bot.py --games 200 --opponent random --seed 1
    python tools/train_ai_bot.py --games 200 --opponent ai --load beastborn/data/ai_bot.safetensors --seed 201
    python tools/train_ai_bot.py --games 100 --epochs 20 --opponent ai --opponent-weights beastborn/data/ai_bot.opponent.safetensors

Training runs in ``--epochs`` rounds (default 10) of ``--games`` games each, so ``--games 100 --epochs 20`` plays
2000 games. After every epoch the learner's weights are written to the ``ai`` opponent's file (``--opponent-weights``,
default ``<out>.opponent.safetensors`` next to ``--out``) and the opponent is loaded again from that file, so it
gets smarter epoch by epoch. Each epoch ends with one summary line in the log.

The learner (a LEARNING ``AIFindActionStrategy``) plays one side of 10x10 games: P1 in even games, P2 in odd ones,
so it learns both sides. It looks one unit turn ahead (no alpha-beta): it plays its best afterstate, or a random
action with probability ``AI_EPSILON``. Equally good actions come in random order (base strategy
``RandomAllFindActionStrategy``, repeatable for the seed): with a fixed order the first one, waiting, would always win
the tie, and a new network (all moves worth 0) would never walk towards the enemy. The opponent is ``random`` (a
random legal turn) or ``ai`` (a frozen WORKING copy loaded from ``--opponent-weights``, default the ``--load`` file;
without a file: a new network = best score).

After every learner turn it collects (previous afterstate, reward, best afterstate now); at the end of a game
(last afterstate, final reward, done). reward = score change / AI_SCORE_SCALE - AI_STEP_COST (every turn costs a
little: quicker is better) + AI_PROGRESS_REWARD x how much closer its unit nearest to the enemy Boss got (not in the
last transition); the last one also gets +- AI_WIN_REWARD for a win / loss. A game stopped at ``--max-turns`` did
not really end, so its last afterstate (it has no next one) is left out and there is no end reward: the turn cost
already makes stalling worse than moving on. After each game the learner does one ``train_step`` per
AI_TRANSITIONS_PER_TRAIN_STEP transitions it stored (batches of AI_BATCH_SIZE).

The weights are saved every ``--save-every`` games, at the end, and on Ctrl+C. Continue a run with ``--load`` (only
the weights are loaded; the replay memory and Adam start empty). Logs: console and ``--log`` (default
train_ai_bot.log). tinygrad on CPU needs ``clang`` on the PATH (or named by ``CC``).
"""
from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from random import Random
from typing import Tuple

if not __package__:  # run as a file: make the 'beastborn' package importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402  (imports below the sys.path fix on purpose)
from tinygrad import Device  # noqa: E402

from beastborn import constant as C  # noqa: E402
from beastborn.control import (  # noqa: E402
    AIFindActionStrategy, AIMode, AllFindActionStrategy, RandomAllFindActionStrategy,
)
from beastborn.control import ai_find_action_strategy as ai  # noqa: E402
from beastborn.control.ai_find_action_strategy import Ranked, score_difference, scale_score_for_ai  # noqa: E402
from beastborn.game import Phase, new_game, Command, GameStateMachine  # noqa: E402
from beastborn.game.commands import Command  # noqa: E402
from beastborn.game.state_machine import GameStateMachine  # noqa: E402

log: logging.Logger = logging.getLogger("train_ai_bot")

OPPONENTS: tuple[str, ...] = ("random", "ai")
SUMMARY_EVERY: int = 10  # games per summary line
DEFAULT_EPOCHS: int = 10  # rounds of ``games`` games; the ai opponent is refreshed after each one


@dataclass(frozen=True)
class GameResult:
    """One game, as the learner saw it (for the epoch summary)."""

    result: str  # win, loss, draw or stopped
    turns: int
    score: int  # the learner's final score difference
    loss: float | None  # mean training loss of the game (None = no training step)


@dataclass
class Summary:
    """What a training run did (also returned to tests)."""

    games: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    stopped: int = 0  # games stopped at --max-turns
    steps: int = 0  # training steps
    results: list[str] = field(default_factory=list)
    next_seed: int = 0


def train(
    games: int = 100, opponent: str = "random", opponent_weights: Path | None = None, load: Path | None = None,
    out: Path = C.AI_WEIGHTS_FILE, seed: int = 1, save_every: int = 10, max_turns: int = 300,
    log_file: Path | None = Path("train_ai_bot.log"), epsilon: float = C.AI_EPSILON, epochs: int = DEFAULT_EPOCHS,
) -> Summary:
    """Play ``epochs`` x ``games`` training games and save the learner's weights to ``out``. Ctrl+C saves and stops.

    After every epoch the ``ai`` opponent gets the learner's weights: they are saved to its file and loaded again.
    """
    if opponent not in OPPONENTS:
        raise ValueError(f"Unknown opponent {opponent!r}; use one of {OPPONENTS}")
    if epochs < 1:
        raise ValueError("epochs must be at least 1")
    opponent_file: Path = (Path(opponent_weights) if opponent_weights is not None
                           else Path(out).with_name(Path(out).stem + ".opponent.safetensors"))
    handler: logging.Handler | None = _log_to_file(log_file)
    try:
        rng = random.Random(seed)
        learner = AIFindActionStrategy(AIMode.LEARNING, weights=load, seed=seed,
                                       base=RandomAllFindActionStrategy(random.Random(rng.random())))
        frozen: AIFindActionStrategy | None = None
        if opponent == "ai":
            frozen = AIFindActionStrategy(AIMode.WORKING, weights=opponent_weights or load, seed=seed,
                                          base=RandomAllFindActionStrategy(random.Random(rng.random())))
        train_from: int = C.AI_WARMUP_MEMORY
        log.info("Training: %d epochs x %d games from seed %d vs %s%s, max %d unit turns, epsilon %.2f, "
                 "save every %d to %s", epochs, games, seed, opponent,
                 f" ({frozen.weights or 'new network'})" if frozen is not None else "", max_turns, epsilon,
                 save_every, out)
        log.info(f"Training from = {train_from}")
        log.info("Learner weights: %s; device: %s", load or "new network", Device.DEFAULT)

        summary = Summary(next_seed=seed)
        started: float = time.perf_counter()
        try:
            for epoch in range(epochs):
                epoch_started: float = time.perf_counter()
                played: list[GameResult] = []
                for index in range(games):
                    warming: bool = len(learner.memory) < train_from
                    played.append(_play(epoch, epochs, index, games, index, learner, frozen, rng, max_turns, epsilon,
                                        summary, train=not warming))
                    if warming and len(learner.memory) >= train_from:
                        log.info("Warm-up done: %d transitions in memory, training starts", len(learner.memory))
                    summary.next_seed = seed + summary.games
                    if summary.games % SUMMARY_EVERY == 0:
                        _log_summary(summary)
                    if summary.games % save_every == 0:
                        log.info("Saved %s", learner.save(out))
                _log_epoch(epoch, epochs, played, len(learner.memory), time.perf_counter() - epoch_started)
                if frozen is not None:  # a smarter opponent for the next epoch: the learner as it is now
                    learner.save(opponent_file)
                    frozen = AIFindActionStrategy(AIMode.WORKING, weights=opponent_file, seed=seed,
                                                  base=RandomAllFindActionStrategy(random.Random(rng.random())))
                    log.info("epoch %d/%d: opponent updated from %s", epoch + 1, epochs, opponent_file)
        except KeyboardInterrupt:
            log.info("Stopped by Ctrl+C after %d games", summary.games)
        log.info("Saved %s", learner.save(out))
        log.info("Done: %d games (%d wins, %d losses, %d draws, %d stopped), %d training steps, %.0f s",
                 summary.games, summary.wins, summary.losses, summary.draws, summary.stopped, summary.steps,
                 time.perf_counter() - started)
        log.info("continue with: --load %s --seed %d", out, summary.next_seed)
        return summary
    finally:
        if handler is not None:
            log.removeHandler(handler)
            handler.close()


def _play(epoch: int, epochs: int, index: int, games: int, game_seed: int, learner: AIFindActionStrategy,
          opponent_strategy: AIFindActionStrategy | None, rng: random.Random, max_turns: int, epsilon: float,
          summary: Summary,  train: bool = True) -> GameResult:
    """Game ``index`` of epoch ``epoch`` (both from 0); the learner remembers its transitions and trains after it."""
    started: float = time.perf_counter()
    gsm: GameStateMachine = new_game(2, seed=game_seed, width=C.AI_BOARD_SIZE, height=C.AI_BOARD_SIZE)
    seat: int = summary.games % 2  # the learner: P1 in even games, P2 in odd ones (counted over all epochs)
    everything = AllFindActionStrategy()
    pending: Ranked | None = None  # the learner's last afterstate
    losses: list[float] = []
    results: list = []
    turns: int = 0
    while gsm.phase is Phase.AWAITING_COMMAND and turns < max_turns:
        turns += 1
        if gsm.active_player != seat:
            action = get_opponent_move(everything, gsm, opponent_strategy, rng)
            _submit(gsm, action)
            continue
        ranked: list[Ranked] = learner.rank(gsm)
        if pending is not None:  # the best afterstate now is the "max" of the Bellman target
            reward: float = (scale_score_for_ai(ranked[0].score - pending.score) - C.AI_STEP_COST
                             + C.AI_PROGRESS_REWARD * _progress(pending.afterstate, ranked[0].afterstate))
            results.append((pending.afterstate, reward, ranked[0].afterstate, False))
        pending = ranked[0] if rng.random() >= epsilon else rng.choice(ranked)
        _submit(gsm, pending.action)

    winner: int | None = gsm.view().winner if gsm.phase is Phase.GAME_OVER else None
    result: str
    bonus: float
    game_was_stopped = gsm.phase is not Phase.GAME_OVER
    if game_was_stopped:
        result, bonus = "stopped", 0.0  # no end reward: the game did not end (the turn cost pushes the bot on)
        summary.stopped += 1
    elif winner is None:
        result, bonus = "draw", 0
        summary.draws += 1
    elif winner == seat:
        result, bonus = "win", C.AI_WIN_REWARD
        summary.wins += 1
    else:
        result, bonus = "loss", -C.AI_WIN_REWARD
        summary.losses += 1
    final: int = score_difference(gsm, seat)
    score_for_network = 0
    if pending is not None and not game_was_stopped:  # a stopped game's last afterstate has no next one: left out
        score_for_network = scale_score_for_ai(final - pending.score) + bonus - C.AI_STEP_COST
        results.append((pending.afterstate, score_for_network,
                        np.zeros_like(pending.afterstate), True))

    for r in results:  # every turn cost and every step towards the enemy Boss is worth learning
        learner.remember(*r)
    for _ in range(-(-len(results) // C.AI_TRANSITIONS_PER_TRAIN_STEP) if train else 0):  # ceil: more data, more steps
        loss: float | None = learner.train_step()
        if loss is None:
            break
        losses.append(loss)
        summary.steps += 1

    summary.games += 1
    summary.results.append(result)

    mean_loss: str = f"{np.mean(losses):.4f}" if losses else "-"
    log.info("epoch %d/%d game %d/%d seed %d as P%d: %s after %d unit turns, score %+d, score for net %+.2f, "
             "loss %s (%d steps), memory %d, training = "+ str(train)+", %.1f s",
             epoch + 1, epochs, index + 1, games, game_seed, seat + 1, result, turns, final, score_for_network,
             mean_loss, len(losses), len(learner.memory), time.perf_counter() - started)
    return GameResult(result, turns, final, float(np.mean(losses)) if losses else None)


def _boss_distance(planes: np.ndarray) -> float | None:
    """Distance (Manhattan, / width + height) from my unit nearest to the enemy Boss, in the learner's planes.

    My own Boss does not count (it should not lead the attack). None when there is no such unit or no enemy Boss.
    """
    mine: np.ndarray = np.argwhere((planes[ai.CH_MY_UNITS] > 0) & (planes[ai.CH_MY_BOSS] == 0))
    boss: np.ndarray = np.argwhere(planes[ai.CH_ENEMY_BOSS] > 0)
    if len(mine) == 0 or len(boss) == 0:
        return None
    return float(np.abs(mine - boss[0]).sum(axis=1).min()) / (planes.shape[1] + planes.shape[2])


def _progress(before: np.ndarray, after: np.ndarray) -> float:
    """How much closer (0..1) my nearest unit got to the enemy Boss from one afterstate to the next (< 0 = away)."""
    start: float | None = _boss_distance(before)
    end: float | None = _boss_distance(after)
    return 0.0 if start is None or end is None else start - end


def get_opponent_move(everything: AllFindActionStrategy, gsm: GameStateMachine,
                      opponent_strategy: AIFindActionStrategy | None, rng: Random) -> Command:
    if opponent_strategy is not None:
        # Return randomly two best possible actions:
        return rng.choice(opponent_strategy.rank(gsm)[0:2]).action
    else:
        return rng.choice(everything.find_all_possible_actions(gsm))


def _submit(gsm: GameStateMachine, action: Command) -> None:
    if not gsm.submit(action).ok:  # cannot happen: strategies list only what submit accepts
        raise RuntimeError(f"Action {action!r} was rejected")


def _log_summary(summary: Summary) -> None:
    last: list[str] = summary.results[-SUMMARY_EVERY:]
    log.info("last %d games: %d wins, %d losses, %d draws, %d stopped (total %d games, %d wins)",
             len(last), last.count("win"), last.count("loss"), last.count("draw"), last.count("stopped"),
             summary.games, summary.wins)


def _log_epoch(epoch: int, epochs: int, played: list[GameResult], memory: int, seconds: float) -> None:
    """One line per epoch, like the game line: results, mean unit turns, mean score, mean loss."""
    results: list[str] = [g.result for g in played]
    losses: list[float] = [g.loss for g in played if g.loss is not None]
    mean_loss: str = f"{np.mean(losses):.4f}" if losses else "-"
    log.info("epoch %d/%d: %d games, %d wins, %d losses, %d draws, %d stopped, %.1f unit turns, score %+.1f, "
             "loss %s, memory %d, %.1f s",
             epoch + 1, epochs, len(played), results.count("win"), results.count("loss"), results.count("draw"),
             results.count("stopped"), np.mean([g.turns for g in played]) if played else 0.0,
             np.mean([g.score for g in played]) if played else 0.0, mean_loss, memory, seconds)


def _log_to_file(log_file: Path | None) -> logging.Handler | None:
    log.setLevel(logging.INFO)
    if log_file is None:
        return None
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter(C.LOG_FORMAT))
    log.addHandler(handler)
    return handler


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--games", type=int, default=100, help="games per epoch")
    parser.add_argument("--epochs", type=int, default=DEFAULT_EPOCHS,
                        help="rounds of --games games; the ai opponent gets the learner's weights after each one")
    parser.add_argument("--opponent", choices=OPPONENTS, default="random")
    parser.add_argument("--opponent-weights", type=Path,
                        help="weights of the 'ai' opponent (default: --load); overwritten after every epoch")
    parser.add_argument("--load", type=Path, help="weights to continue from (default: a new network)")
    parser.add_argument("--out", type=Path, default=C.AI_WEIGHTS_FILE, help="where the weights are saved")
    parser.add_argument("--seed", type=int, default=1, help="seed of the first game (game i uses seed + i)")
    parser.add_argument("--save-every", type=int, default=10, help="save the weights every N games")
    parser.add_argument("--max-turns", type=int, default=300, help="unit turns before a game is stopped")
    parser.add_argument("--log", type=Path, default=Path("train_ai_bot.log"), help="log file")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format=C.LOG_FORMAT)
    train(args.games, args.opponent, args.opponent_weights, args.load, args.out, args.seed, args.save_every,
          args.max_turns, args.log, epochs=args.epochs)


if __name__ == "__main__":
    main()
