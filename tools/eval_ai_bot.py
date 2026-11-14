"""Check how strong the AI bot is: play games against an opponent that does not change, with no training.

Run from the project root::

    python tools/eval_ai_bot.py                                     # the benchmark: BENCHMARK_* of constant.py
    python tools/eval_ai_bot.py --opponent random                   # the current weights vs a random player
    python tools/eval_ai_bot.py --opponent ai --opponent-weights beastborn/data/ai_bot_run3.safetensors

Every argument defaults to the BENCHMARK_* constants of ``beastborn/constant.py`` (``--weights``: AI_WEIGHTS_FILE),
the same benchmark ``tools/train_monitor.py`` plays every BENCHMARK_EVERY_EPOCHS training epochs.

The bot plays as in a real game: Proportional Top-K Sampling (``AIFindActionStrategy.choose``, AI_TOP_K,
plain softmax), no learning. The opponent is ``random`` (a random legal turn) or ``ai`` (another weights file,
also by Top-K Sampling): a saved older copy shows whether training still helps.
The bot plays P1 in even games and P2 in odd ones. The maps start at ``--map-seed`` (BENCHMARK_MAP_SEED). The same
arguments always give the same games.

Compare the win rate (and the stopped rate) between runs; the self-play numbers of the training log cannot show
progress, because there the opponent gets better at the same speed.

Every run appends its arguments and its ``Result:`` line to ``--log`` (default eval_ai_bot.log), so runs can be
compared later. ``--csv FILE`` also appends one row with the result (``--run`` and ``--epoch`` label it), and
``--bgn FILE`` appends every game in Beastborn Game Notation when it ends (players named Bot and the opponent), so the
Games tab of ``tools/train_monitor.py`` can replay them. Neither file is emptied.

Tip: while a training run is going, evaluate a copy of the weights file: the training saves it every few games.
"""
from __future__ import annotations

import argparse
import csv
import logging
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

if not __package__:  # run as a file: make the 'beastborn' package importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402  (imports below the sys.path fix on purpose)

from beastborn import constant as C  # noqa: E402
from beastborn.control import (  # noqa: E402
    AIFindActionStrategy, AIMode, AllFindActionStrategy, RandomAllFindActionStrategy,
)
from beastborn.control.ai_find_action_strategy import score_difference  # noqa: E402
from beastborn.game import GameRecorder, Phase, new_game  # noqa: E402
from beastborn.game.bgn import append_bgn  # noqa: E402
from beastborn.game.commands import Command  # noqa: E402
from beastborn.game.state_machine import GameStateMachine  # noqa: E402

log: logging.Logger = logging.getLogger("eval_ai_bot")

OPPONENTS: tuple[str, ...] = ("random", "ai")
BOT_NAME: str = "Bot"  # player names in the recorded games (--bgn); the opponent: RANDOM_NAME or its weights file
RANDOM_NAME: str = "Random"
CSV_COLUMNS: tuple[str, ...] = (  # one row per run in --csv (read by tools/train_monitor.py)
    "run", "epoch", "started", "time", "games", "wins", "losses", "draws", "stopped",
    "unit_turns", "win_turns", "loss_turns", "score", "seconds", "weights", "opponent",
)


@dataclass
class Evaluation:
    """What an evaluation run found."""

    games: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    stopped: int = 0  # games stopped at --max-turns
    results: list[str] = field(default_factory=list)  # per game: win, loss, draw or stopped
    turns: list[int] = field(default_factory=list)
    scores: list[int] = field(default_factory=list)  # the bot's final score difference
    started: str = ""  # local time the run started, "YYYY-MM-DD HH:MM:SS"
    seconds: float = 0.0

    @property
    def win_rate(self) -> float:
        return self.wins / self.games if self.games else 0.0


def evaluate(
    games: int = C.BENCHMARK_GAMES, weights: Path = C.AI_WEIGHTS_FILE, opponent: str = C.BENCHMARK_OPPONENT,
    opponent_weights: Path | None = C.BENCHMARK_OPPONENT_WEIGHTS, map_seed: int = C.BENCHMARK_MAP_SEED,
    max_turns: int = C.BENCHMARK_MAX_TURNS, seed: int = C.BENCHMARK_SEED, bgn_file: Path | None = None,
) -> Evaluation:
    """Play ``games`` games of the bot (``weights``) against ``opponent`` on maps ``map_seed``, ``map_seed + 1``...

    ``bgn_file``: every game is appended to it in BGN when it ends (None = no file).
    """
    if opponent not in OPPONENTS:
        raise ValueError(f"Unknown opponent {opponent!r}; use one of {OPPONENTS}")
    if opponent == "ai" and opponent_weights is None:
        raise ValueError("--opponent ai needs --opponent-weights (a weights file to play against)")
    rng = random.Random(seed)
    bot = AIFindActionStrategy(AIMode.WORKING, weights=weights,
                               base=RandomAllFindActionStrategy(random.Random(rng.random())),
                               rng=random.Random(rng.random()))
    other: AIFindActionStrategy | None = None
    if opponent == "ai":
        other = AIFindActionStrategy(AIMode.WORKING, weights=opponent_weights,
                                     base=RandomAllFindActionStrategy(random.Random(rng.random())),
                                     rng=random.Random(rng.random()))
    log.info("Evaluation: %s vs %s, %d games on maps %d-%d, max %d unit turns",
             weights, opponent_weights if other is not None else "random", games, map_seed, map_seed + games - 1,
             max_turns)
    result = Evaluation(started=time.strftime("%Y-%m-%d %H:%M:%S"))
    everything = AllFindActionStrategy()
    started: float = time.perf_counter()
    opponent_name: str = weights_name(opponent_weights) if other is not None and opponent_weights else RANDOM_NAME
    for index in range(games):
        seat: int = index % 2
        names: list[str] = [BOT_NAME, opponent_name] if seat == 0 else [opponent_name, BOT_NAME]
        gsm: GameStateMachine = new_game(2, seed=map_seed + index, width=C.AI_BOARD_SIZE, height=C.AI_BOARD_SIZE,
                                         player_names=names)
        recorder: GameRecorder | None = GameRecorder(gsm) if bgn_file is not None else None
        turns: int = 0
        while gsm.phase is Phase.AWAITING_COMMAND and turns < max_turns:
            turns += 1
            action: Command
            if gsm.active_player == seat:
                action = bot.choose(bot.rank(gsm), 0).action
            elif other is not None:
                action = other.choose(other.rank(gsm), 0).action
            else:
                action = rng.choice(everything.find_all_possible_actions(gsm))
            if not gsm.submit(action).ok:  # cannot happen: strategies list only what submit accepts
                raise RuntimeError(f"Action {action!r} was rejected")
        if recorder is not None and bgn_file is not None:
            _append_bgn(bgn_file, recorder)
        outcome: str
        if gsm.phase is not Phase.GAME_OVER:
            outcome = "stopped"
            result.stopped += 1
        elif gsm.view().winner is None:
            outcome = "draw"
            result.draws += 1
        elif gsm.view().winner == seat:
            outcome = "win"
            result.wins += 1
        else:
            outcome = "loss"
            result.losses += 1
        final: int = score_difference(gsm, seat)
        result.games += 1
        result.results.append(outcome)
        result.turns.append(turns)
        result.scores.append(final)
        log.info("game %d/%d map %d as P%d: %s after %d unit turns, score %+d",
                 index + 1, games, map_seed + index, seat + 1, outcome, turns, final)
    result.seconds = time.perf_counter() - started
    log.info("Result: %d games, %d wins (%.0f%%), %d losses, %d draws, %d stopped (%.0f%%), %.1f unit turns, "
             "score %+.1f, %.0f s",
             result.games, result.wins, 100 * result.win_rate, result.losses, result.draws, result.stopped,
             100 * result.stopped / max(1, result.games), np.mean(result.turns) if result.turns else 0.0,
             np.mean(result.scores) if result.scores else 0.0, result.seconds)
    return result


def weights_name(weights: Path) -> str:
    """A short name of a weights file: ``ai_bot.safetensors.good1`` -> ``ai_bot.good1``."""
    return Path(weights).name.replace(".safetensors", "")


def _append_bgn(bgn_file: Path, recorder: GameRecorder) -> None:
    """Append the recorder's game to ``bgn_file``. Never stops the evaluation."""
    try:
        append_bgn(bgn_file, recorder.record())
    except OSError as error:  # e.g. the file is locked: skip this game
        log.warning("Could not write %s: %s", bgn_file, error)


def append_csv(csv_file: Path, result: Evaluation, run: str = "", epoch: int | None = None,
               weights: Path | None = None, opponent: str = "") -> None:
    """Append one row with ``result`` to ``csv_file`` (with the header first when the file is new).

    ``run`` (default: the time the evaluation started) and ``epoch`` label the row, e.g. the training run and epoch
    whose weights were evaluated (tools/train_monitor.py).
    """
    def mean(values: list[int]) -> str:
        return f"{sum(values) / len(values):.2f}" if values else ""

    def turns_of(outcome: str) -> list[int]:
        return [turns for turns, played in zip(result.turns, result.results) if played == outcome]

    row: tuple = (
        run or result.started, "" if epoch is None else epoch, result.started, time.strftime("%Y-%m-%d %H:%M:%S"),
        result.games, result.wins, result.losses, result.draws, result.stopped,
        mean(result.turns), mean(turns_of("win")), mean(turns_of("loss")), mean(result.scores),
        f"{result.seconds:.1f}", "" if weights is None else weights_name(weights), opponent,
    )
    try:
        new: bool = not csv_file.exists() or csv_file.stat().st_size == 0
        with open(csv_file, "a", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            if new:
                writer.writerow(CSV_COLUMNS)
            writer.writerow(row)
    except OSError as error:  # e.g. the file is open in Excel
        log.warning("Could not write %s: %s", csv_file, error)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--games", type=int, default=C.BENCHMARK_GAMES, help="games to play")
    parser.add_argument("--weights", type=Path, default=C.AI_WEIGHTS_FILE, help="the bot's weights")
    parser.add_argument("--opponent", choices=OPPONENTS, default=C.BENCHMARK_OPPONENT)
    parser.add_argument("--opponent-weights", type=Path, default=C.BENCHMARK_OPPONENT_WEIGHTS,
                        help="weights of the 'ai' opponent (e.g. an older copy)")
    parser.add_argument("--map-seed", type=int, default=C.BENCHMARK_MAP_SEED,
                        help="seed of the first map (game i uses map-seed + i)")
    parser.add_argument("--max-turns", type=int, default=C.BENCHMARK_MAX_TURNS,
                        help="unit turns before a game is stopped")
    parser.add_argument("--seed", type=int, default=C.BENCHMARK_SEED,
                        help="seed of the random choices (random opponent, ties)")
    parser.add_argument("--log", type=Path, default=Path("eval_ai_bot.log"),
                        help="log file: arguments and result of every run are appended")
    parser.add_argument("--csv", default="", help='one row with the result is appended here ("" = none)')
    parser.add_argument("--bgn", default="", help='every game is appended here in BGN ("" = none)')
    parser.add_argument("--run", default="", help="label of the --csv row (default: the start time)")
    parser.add_argument("--epoch", type=int, help="training epoch of the weights, written to the --csv row")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format=C.LOG_FORMAT)
    handler = logging.FileHandler(args.log, mode="a", encoding="utf-8")  # append: one block per run
    handler.setFormatter(logging.Formatter(C.LOG_FORMAT))
    handler.addFilter(lambda record: record.getMessage().startswith(("Arguments:", "Result:")))
    log.addHandler(handler)
    log.info("Arguments: %s", " ".join(f"--{name.replace('_', '-')} {value}" for name, value in vars(args).items()))
    result = evaluate(args.games, args.weights, args.opponent, args.opponent_weights, args.map_seed, args.max_turns,
                      args.seed, bgn_file=Path(args.bgn) if args.bgn else None)
    if args.csv:
        opponent: str = args.opponent if args.opponent == "random" else weights_name(args.opponent_weights)
        append_csv(Path(args.csv), result, args.run, args.epoch, args.weights, opponent)


if __name__ == "__main__":
    main()
