"""Train the AI move-ordering network (``AIFindActionStrategy``) by playing games, with logs to follow progress.

Run from the project root::

    python tools/train_ai_bot.py --games 200 --opponent random --seed 1
    python tools/train_ai_bot.py --games 200 --opponent ai --load beastborn/data/ai_bot.safetensors --seed 201

The learner (a LEARNING ``AIFindActionStrategy``) plays one side of 10x10 games: P1 in even games, P2 in odd ones,
so it learns both sides. It looks one unit turn ahead (no alpha-beta): it plays its best afterstate, or a random
action with probability ``AI_EPSILON``. Equally good actions come in random order (base strategy
``RandomAllFindActionStrategy``, repeatable for the seed): with a fixed order the first one, waiting, would always win
the tie, and a new network (all moves worth 0) would never walk towards the enemy. The opponent is ``random`` (a
random legal turn) or ``ai`` (a frozen WORKING copy loaded from ``--opponent-weights``, default the ``--load`` file;
without a file: a new network = best score).

After every learner turn it remembers (previous afterstate, score change / AI_SCORE_SCALE, best afterstate now) and
does one training step. At the end of a game: (last afterstate, final score change +- AI_WIN_REWARD, done).

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

if not __package__:  # run as a file: make the 'beastborn' package importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402  (imports below the sys.path fix on purpose)
from tinygrad import Device  # noqa: E402

from beastborn import constant as C  # noqa: E402
from beastborn.control import (  # noqa: E402
    AIFindActionStrategy, AIMode, AllFindActionStrategy, RandomAllFindActionStrategy,
)
from beastborn.control.ai_find_action_strategy import Ranked, score_difference  # noqa: E402
from beastborn.game import Phase, new_game  # noqa: E402
from beastborn.game.commands import Command  # noqa: E402
from beastborn.game.state_machine import GameStateMachine  # noqa: E402

log: logging.Logger = logging.getLogger("train_ai_bot")

OPPONENTS: tuple[str, ...] = ("random", "ai")
SUMMARY_EVERY: int = 10  # games per summary line


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
    log_file: Path | None = Path("train_ai_bot.log"), epsilon: float = C.AI_EPSILON,
) -> Summary:
    """Play ``games`` training games and save the learner's weights to ``out``. Ctrl+C saves and stops."""
    if opponent not in OPPONENTS:
        raise ValueError(f"Unknown opponent {opponent!r}; use one of {OPPONENTS}")
    handler: logging.Handler | None = _log_to_file(log_file)
    try:
        rng = random.Random(seed)
        learner = AIFindActionStrategy(AIMode.LEARNING, weights=load, seed=seed,
                                       base=RandomAllFindActionStrategy(random.Random(rng.random())))
        frozen: AIFindActionStrategy | None = None
        if opponent == "ai":
            frozen = AIFindActionStrategy(AIMode.WORKING, weights=opponent_weights or load, seed=seed,
                                          base=RandomAllFindActionStrategy(random.Random(rng.random())))
        log.info("Training: %d games from seed %d vs %s%s, max %d unit turns, epsilon %.2f, save every %d to %s",
                 games, seed, opponent,
                 f" ({frozen.weights or 'new network'})" if frozen is not None else "", max_turns, epsilon,
                 save_every, out)
        log.info("Learner weights: %s; device: %s", load or "new network", Device.DEFAULT)
        summary = Summary(next_seed=seed)
        started: float = time.perf_counter()
        try:
            for index in range(games):
                _play(index, games, (index % 10) + 1, learner, frozen, rng, max_turns, epsilon, summary)
                summary.next_seed = seed + index + 1
                if summary.games % SUMMARY_EVERY == 0:
                    _log_summary(summary)
                if summary.games % save_every == 0:
                    log.info("Saved %s", learner.save(out))
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


def _play(index: int, games: int, game_seed: int, learner: AIFindActionStrategy, frozen: AIFindActionStrategy | None,
          rng: random.Random, max_turns: int, epsilon: float, summary: Summary) -> None:
    """One game; the learner remembers its transitions and trains after each of its turns."""
    started: float = time.perf_counter()
    gsm: GameStateMachine = new_game(2, seed=game_seed, width=C.AI_BOARD_SIZE, height=C.AI_BOARD_SIZE)
    seat: int = index % 2  # the learner: P1 in even games, P2 in odd ones
    everything = AllFindActionStrategy()
    pending: Ranked | None = None  # the learner's last afterstate
    losses: list[float] = []
    turns: int = 0
    while gsm.phase is Phase.AWAITING_COMMAND and turns < max_turns:
        turns += 1
        if gsm.active_player != seat:
            action: Command = (frozen.rank(gsm)[0].action if frozen is not None
                               else rng.choice(everything.find_all_possible_actions(gsm)))
            _submit(gsm, action)
            continue
        ranked: list[Ranked] = learner.rank(gsm)
        if pending is not None:  # the best afterstate now is the "max" of the Bellman target
            learner.remember(pending.afterstate, (ranked[0].score - pending.score) / C.AI_SCORE_SCALE,
                             ranked[0].afterstate, done=False)
        pending = ranked[0] if rng.random() >= epsilon else rng.choice(ranked)
        _submit(gsm, pending.action)
        loss: float | None = learner.train_step()
        if loss is not None:
            losses.append(loss)
            summary.steps += 1

    winner: int | None = gsm.view().winner if gsm.phase is Phase.GAME_OVER else None
    result: str
    bonus: float
    if gsm.phase is not Phase.GAME_OVER:
        result, bonus = "stopped", 0.0
        summary.stopped += 1
    elif winner is None:
        result, bonus = "draw", 0.0
        summary.draws += 1
    elif winner == seat:
        result, bonus = "win", C.AI_WIN_REWARD
        summary.wins += 1
    else:
        result, bonus = "loss", -C.AI_WIN_REWARD
        summary.losses += 1
    final: int = score_difference(gsm, seat)
    if pending is not None:
        learner.remember(pending.afterstate, (final - pending.score) / C.AI_SCORE_SCALE + bonus,
                         np.zeros_like(pending.afterstate), done=True)
    summary.games += 1
    summary.results.append(result)
    mean_loss: str = f"{np.mean(losses):.4f}" if losses else "-"
    log.info("game %d/%d seed %d as P%d: %s after %d unit turns, score %+d, loss %s, memory %d, %.1f s",
             index + 1, games, game_seed, seat + 1, result, turns, final, mean_loss, len(learner.memory),
             time.perf_counter() - started)


def _submit(gsm: GameStateMachine, action: Command) -> None:
    if not gsm.submit(action).ok:  # cannot happen: strategies list only what submit accepts
        raise RuntimeError(f"Action {action!r} was rejected")


def _log_summary(summary: Summary) -> None:
    last: list[str] = summary.results[-SUMMARY_EVERY:]
    log.info("last %d games: %d wins, %d losses, %d draws, %d stopped (total %d games, %d wins)",
             len(last), last.count("win"), last.count("loss"), last.count("draw"), last.count("stopped"),
             summary.games, summary.wins)


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
    parser.add_argument("--games", type=int, default=100, help="games to play in this run")
    parser.add_argument("--opponent", choices=OPPONENTS, default="random")
    parser.add_argument("--opponent-weights", type=Path, help="weights of the 'ai' opponent (default: --load)")
    parser.add_argument("--load", type=Path, help="weights to continue from (default: a new network)")
    parser.add_argument("--out", type=Path, default=C.AI_WEIGHTS_FILE, help="where the weights are saved")
    parser.add_argument("--seed", type=int, default=1, help="seed of the first game (game i uses seed + i)")
    parser.add_argument("--save-every", type=int, default=10, help="save the weights every N games")
    parser.add_argument("--max-turns", type=int, default=300, help="unit turns before a game is stopped")
    parser.add_argument("--log", type=Path, default=Path("train_ai_bot.log"), help="log file")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format=C.LOG_FORMAT)
    train(args.games, args.opponent, args.opponent_weights, args.load, args.out, args.seed, args.save_every,
          args.max_turns, args.log)


if __name__ == "__main__":
    main()
