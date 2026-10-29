"""Check how strong the AI bot is: play games against an opponent that does not change, with no training.

Run from the project root::

    python tools/eval_ai_bot.py                                     # the current weights vs a random player
    python tools/eval_ai_bot.py --opponent ai --opponent-weights beastborn/data/ai_bot_run3.safetensors

The bot always plays its best move (no random moves, no learning). The opponent is ``random`` (a random legal turn)
or ``ai`` (another weights file, also playing its best move): a saved older copy shows whether training still helps.
The bot plays P1 in even games and P2 in odd ones. The maps start at ``--map-seed`` (default 1000), so they are not
the training maps (0 .. --games - 1 of train_ai_bot.py). The same arguments always give the same games.

Compare the win rate (and the stopped rate) between runs; the self-play numbers of the training log cannot show
progress, because there the opponent gets better at the same speed.

Tip: while a training run is going, evaluate a copy of the weights file: the training saves it every few games.
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

from beastborn import constant as C  # noqa: E402
from beastborn.control import (  # noqa: E402
    AIFindActionStrategy, AIMode, AllFindActionStrategy, RandomAllFindActionStrategy,
)
from beastborn.control.ai_find_action_strategy import score_difference  # noqa: E402
from beastborn.game import Phase, new_game  # noqa: E402
from beastborn.game.commands import Command  # noqa: E402
from beastborn.game.state_machine import GameStateMachine  # noqa: E402

log: logging.Logger = logging.getLogger("eval_ai_bot")

OPPONENTS: tuple[str, ...] = ("random", "ai")


@dataclass
class Evaluation:
    """What an evaluation run found."""

    games: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    stopped: int = 0  # games stopped at --max-turns
    turns: list[int] = field(default_factory=list)
    scores: list[int] = field(default_factory=list)  # the bot's final score difference

    @property
    def win_rate(self) -> float:
        return self.wins / self.games if self.games else 0.0


def evaluate(
    games: int = 100, weights: Path = C.AI_WEIGHTS_FILE, opponent: str = "random",
    opponent_weights: Path | None = None, map_seed: int = 1, max_turns: int = 120, seed: int = 1,
) -> Evaluation:
    """Play ``games`` games of the bot (``weights``) against ``opponent`` on maps ``map_seed``, ``map_seed + 1``..."""
    if opponent not in OPPONENTS:
        raise ValueError(f"Unknown opponent {opponent!r}; use one of {OPPONENTS}")
    if opponent == "ai" and opponent_weights is None:
        raise ValueError("--opponent ai needs --opponent-weights (a weights file to play against)")
    rng = random.Random(seed)
    bot = AIFindActionStrategy(AIMode.WORKING, weights=weights,
                               base=RandomAllFindActionStrategy(random.Random(rng.random())))
    other: AIFindActionStrategy | None = None
    if opponent == "ai":
        other = AIFindActionStrategy(AIMode.WORKING, weights=opponent_weights,
                                     base=RandomAllFindActionStrategy(random.Random(rng.random())))
    log.info("Evaluation: %s vs %s, %d games on maps %d-%d, max %d unit turns",
             weights, opponent_weights if other is not None else "random", games, map_seed, map_seed + games - 1,
             max_turns)
    result = Evaluation()
    everything = AllFindActionStrategy()
    started: float = time.perf_counter()
    for index in range(games):
        seat: int = index % 2
        gsm: GameStateMachine = new_game(2, seed=map_seed + index, width=C.AI_BOARD_SIZE, height=C.AI_BOARD_SIZE)
        turns: int = 0
        while gsm.phase is Phase.AWAITING_COMMAND and turns < max_turns:
            turns += 1
            action: Command
            if gsm.active_player == seat:
                action = bot.rank(gsm)[0].action
            elif other is not None:
                action = other.rank(gsm)[0].action
            else:
                action = rng.choice(everything.find_all_possible_actions(gsm))
            if not gsm.submit(action).ok:  # cannot happen: strategies list only what submit accepts
                raise RuntimeError(f"Action {action!r} was rejected")
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
        result.turns.append(turns)
        result.scores.append(final)
        log.info("game %d/%d map %d as P%d: %s after %d unit turns, score %+d",
                 index + 1, games, map_seed + index, seat + 1, outcome, turns, final)
    log.info("Result: %d games, %d wins (%.0f%%), %d losses, %d draws, %d stopped (%.0f%%), %.1f unit turns, "
             "score %+.1f, %.0f s",
             result.games, result.wins, 100 * result.win_rate, result.losses, result.draws, result.stopped,
             100 * result.stopped / max(1, result.games), np.mean(result.turns) if result.turns else 0.0,
             np.mean(result.scores) if result.scores else 0.0, time.perf_counter() - started)
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--games", type=int, default=100, help="games to play")
    parser.add_argument("--weights", type=Path, default=C.AI_WEIGHTS_FILE, help="the bot's weights")
    parser.add_argument("--opponent", choices=OPPONENTS, default="random")
    parser.add_argument("--opponent-weights", type=Path, help="weights of the 'ai' opponent (e.g. an older copy)")
    parser.add_argument("--map-seed", type=int, default=1, help="seed of the first map (game i uses map-seed + i)")
    parser.add_argument("--max-turns", type=int, default=120, help="unit turns before a game is stopped")
    parser.add_argument("--seed", type=int, default=1, help="seed of the random choices (random opponent, ties)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format=C.LOG_FORMAT)
    evaluate(args.games, args.weights, args.opponent, args.opponent_weights, args.map_seed, args.max_turns, args.seed)


if __name__ == "__main__":
    main()
