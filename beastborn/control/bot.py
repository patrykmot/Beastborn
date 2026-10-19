"""The bot player: alpha-beta search (ALG) over whole unit turns.

One ply = one unit turn: every action from ``GameStateMachine.find_all_possible_actions`` plays the active unit's whole
turn, then the GSM gives the next unit its turn. The search tries an action with ``submit`` and always undoes it with
``rollback`` (in a ``finally``), so the game is exactly the same after the bot has chosen; ``choose_command`` checks
this every time.

Turns do not strictly alternate between players (a side with more units left plays several units in a row), so a
node maximises when the bot's player is to move and minimises otherwise - never by depth parity.

The bot asks for the actions in random order (its ``rng``), so among equally good actions it picks one at random
(e.g. its opening moves); attacks are still tried before moves and waiting.

Every search is logged (logger ``beastborn.control.bot``, level INFO): the value of the best action and how many
moves were calculated. ``python main.py`` and the WSGI file show INFO logs on the console / in the server log.

Several CPU cores (``workers``, default ``constant.BOT_WORKERS`` = all): the bot searches the first root action
itself, which gives the value to beat (alpha). The other root actions go to a pool of worker processes, one task
each, searched with the window (alpha, +inf), as the one-process search would: a result > alpha is exact, a result
<= alpha can at best tie the first action, and ties go to the first action. Between equal exact values the first in
the root order wins, never the first to finish, so the move is the same as with one process (and the same for the
same seed). Workers get a copy of the game state, never the GSM, so the game is untouched.
If processes cannot be used, the bot logs a warning and searches with one process.
"""
from __future__ import annotations

import atexit
import logging
import multiprocessing
import os
import random
import threading
import time
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

from beastborn import constant as C
from beastborn.control.bot_voice import BotVoice, Mood
from beastborn.control.controller import PlayerController
from beastborn.engine.interface import CalculationEngine
from beastborn.game.commands import Command, CommandResult, CommandType, WrapperCommand
from beastborn.game.state import GameState, Phase
from beastborn.game.state_machine import GameStateMachine
from beastborn.game.view import GameView

log: logging.Logger = logging.getLogger(__name__)

_INFINITY: int = C.BOT_WIN_SCORE * 2  # beyond any search value
_SEED_RANGE: int = 2**32
# Errors meaning "processes cannot be used here" (not a bug in the search): fall back to one process.
_POOL_ERRORS: tuple[type[BaseException], ...] = (BrokenProcessPool, OSError, ImportError, NotImplementedError)

# ---------------------------------------------------------------------- worker processes
# One pool per worker count, created on the first search that needs it and kept for the life of the process
# (starting a worker costs a fraction of a second). "spawn" everywhere: Windows has nothing else, and "fork" in a
# threaded web server can deadlock.
_pools: dict[int, ProcessPoolExecutor] = {}
_pools_lock: threading.Lock = threading.Lock()


def _pool(workers: int) -> ProcessPoolExecutor:
    with _pools_lock:
        if workers not in _pools:
            if not _pools:
                atexit.register(_shutdown_pools)
            _pools[workers] = ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("spawn"))
        return _pools[workers]


def _drop_pool(workers: int) -> None:
    """Forget a pool that failed; the next search creates a new one."""
    with _pools_lock:
        pool: ProcessPoolExecutor | None = _pools.pop(workers, None)
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


def _shutdown_pools() -> None:
    with _pools_lock:
        pools: list[ProcessPoolExecutor] = list(_pools.values())
        _pools.clear()
    for pool in pools:
        pool.shutdown(wait=False, cancel_futures=True)


def _search_task(
    bot: BotPlayerController, state: GameState, engine: CalculationEngine, action: Command, me: int, alpha: int,
    seed: int,
) -> tuple[int, int]:
    """Runs in a worker process: the value of one root action and the moves it took. Module level = picklable.

    ``state`` is the worker's own copy, so its GSM (built here, with an empty history) can play and roll back freely.
    """
    bot._rng = random.Random(seed)  # the worker's copy of the bot: its own repeatable action order
    bot.last_moves = 0
    value: int = bot._try(GameStateMachine(state, engine), action, me, bot.depth - 1, alpha, _INFINITY)
    return value, bot.last_moves


def _parts(command: Command) -> tuple[Command, ...]:
    """The plain commands of an action (a ``WrapperCommand`` is unpacked)."""
    return command.commands if isinstance(command, WrapperCommand) else (command,)


def _order_key(command: Command) -> tuple[int, int]:
    """Strong actions first (alpha-beta cuts more): more attacks, then moves, then waiting.

    Used with a stable sort, so actions of the same kind keep the random order the GSM returned them in.
    """
    kinds: list[CommandType] = [c.kind for c in _parts(command)]
    return -kinds.count(CommandType.ATTACK), -kinds.count(CommandType.MOVE)


class BotPlayerController(PlayerController):
    """Chooses the active unit's whole turn with alpha-beta search; says something after every turn.

    ``evaluate`` (score of the bot minus score of its opponent) is the one place that defines a good board.
    ``rng`` orders the actions at random (ties go to the first one) and picks the voice lines when no ``voice`` is
    given: ``random.Random(seed)`` makes the bot repeat itself for the same game, None = a fresh random one.
    ``workers``: processes for one search (None = ``constant.BOT_WORKERS``; that None = all CPU cores; 1 = only this one).
    A subclass (e.g. with its own ``evaluate``) must be defined at module level to run in worker processes.
    """

    def __init__(
        self, depth: int = C.BOT_SEARCH_DEPTH, voice: BotVoice | None = None, rng: random.Random | None = None,
        workers: int | None = None,
    ):
        if depth < 1:
            raise ValueError("The search depth must be at least 1")
        self.depth: int = depth
        self.workers: int = max(1, workers or C.BOT_WORKERS or os.cpu_count() or 1)
        self.last_message: str | None = None  # what the bot says about the command it chose last
        self.last_value: int | None = None  # search value of that command (for logs, tests and future moods)
        self.last_moves: int = 0  # moves calculated (submitted and rolled back) to choose that command
        self._rng: random.Random = rng or random.Random()
        self._voice: BotVoice = voice or BotVoice(rng=self._rng)
        self._spoke: bool = False  # has the bot said its START line yet

    # ------------------------------------------------------------------ PlayerController
    def choose_command(self, gsm: GameStateMachine) -> Command | None:
        if gsm.phase is not Phase.AWAITING_COMMAND:
            return None
        me: int = gsm.active_player
        log_size: int = len(gsm.command_log)
        before: GameView = gsm.view()
        command, self.last_value = self._best_action(gsm, me)
        if len(gsm.command_log) != log_size or gsm.view() != before:  # the critical requirement, checked every time
            raise RuntimeError("Bot search changed the game state")
        self.last_message = self._voice.line(self._mood(command))
        return command

    # ------------------------------------------------------------------ evaluation
    def evaluate(self, gsm: GameStateMachine, me: int) -> int:
        """How good the board is for player ``me``: my score minus my opponent's (positive = good for me)."""
        return gsm.score(me) - gsm.score(self._opponent(gsm, me))

    def _terminal_value(self, gsm: GameStateMachine, me: int, depth: int) -> int:
        """A won game is worth more the sooner it comes (a lost one costs more the sooner it comes)."""
        winner: int | None = gsm.view().winner
        if winner is None:
            return self.evaluate(gsm, me)
        return C.BOT_WIN_SCORE + depth if winner == me else -C.BOT_WIN_SCORE - depth

    @staticmethod
    def _opponent(gsm: GameStateMachine, me: int) -> int:
        """The other player (a bot plays only in 2-player games, ``constant.BOT_PLAYERS``)."""
        return next(p.index for p in gsm.view().players if p.index != me)

    # ------------------------------------------------------------------ search (ALG)
    def _best_action(self, gsm: GameStateMachine, me: int) -> tuple[Command, int | None]:
        """The best action at the root (the first one of the best value); no search if there is only one.

        With more than one worker (and at least 3 actions) the root actions are shared out to worker processes.
        Logs the value of the best action and how many moves were calculated.
        """
        started: float = time.perf_counter()
        self.last_moves = 0
        actions: list[Command] = gsm.find_all_possible_actions(self._rng)
        if not actions:
            raise RuntimeError("No possible action while the game accepts commands")
        if len(actions) == 1:
            log.info("Bot P%d unit %s: only one possible action, no search", me + 1, gsm.active_unit)
            return actions[0], None
        ordered: list[Command] = sorted(actions, key=_order_key)
        workers: int = min(self.workers, len(ordered) - 1)  # the first action is searched here
        best: Command
        best_value: int
        if workers > 1:
            try:
                best, best_value = self._search_parallel(gsm, me, ordered, workers)
            except _POOL_ERRORS as error:
                log.warning("Bot: worker processes failed (%r); searching with one process", error)
                _drop_pool(self.workers)
                self.last_moves, workers = 0, 1
                best, best_value = self._search_sequential(gsm, me, ordered)
        else:
            workers = 1
            best, best_value = self._search_sequential(gsm, me, ordered)
        log.info(
            "Bot P%d unit %s: best value %d, %d moves calculated (%d possible now, depth %d, %d workers) in %.2f s",
            me + 1, gsm.active_unit, best_value, self.last_moves, len(actions), self.depth, workers,
            time.perf_counter() - started,
        )
        return best, best_value

    def _search_sequential(self, gsm: GameStateMachine, me: int, ordered: list[Command]) -> tuple[Command, int]:
        """All root actions in this process: the first one of the best value."""
        best: Command = ordered[0]
        best_value: int = -_INFINITY
        alpha: int = best_value
        for action in ordered:
            value: int = self._try(gsm, action, me, self.depth - 1, alpha, _INFINITY)
            if value > best_value:
                best, best_value = action, value
                alpha = max(alpha, value)
        return best, best_value

    def _search_parallel(
        self, gsm: GameStateMachine, me: int, ordered: list[Command], workers: int
    ) -> tuple[Command, int]:
        """The first root action here (its exact value is alpha), the others in worker processes.

        Tasks search with (alpha, +inf): a value > alpha is exact; a value <= alpha never wins (at best it ties the
        first action, and ties go to the first). Between equal exact values the first in ``ordered`` wins, as in
        ``_search_sequential``. A task does not see better values found by other tasks (no shared state).
        """
        alpha: int = self._try(gsm, ordered[0], me, self.depth - 1, -_INFINITY, _INFINITY)
        seeds: list[int] = [self._rng.randrange(_SEED_RANGE) for _ in ordered[1:]]  # drawn in order: repeatable
        state: GameState = gsm._state.copy()  # read only; workers get copies of this copy
        pool: ProcessPoolExecutor = _pool(self.workers)
        futures: list[Future[tuple[int, int]]] = [
            pool.submit(_search_task, self, state, gsm.engine, action, me, alpha, seed)
            for action, seed in zip(ordered[1:], seeds)
        ]
        try:
            results: list[tuple[int, int]] = [future.result() for future in futures]
        finally:
            for future in futures:
                future.cancel()  # after an error: drop the tasks not started yet
        best_index: int = 0
        best_value: int = alpha
        for index, (value, moves) in enumerate(results, start=1):
            self.last_moves += moves
            if value > best_value:
                best_index, best_value = index, value
        return ordered[best_index], best_value

    def _alphabeta(self, gsm: GameStateMachine, me: int, depth: int, alpha: int, beta: int) -> int:
        if gsm.phase is Phase.GAME_OVER:
            return self._terminal_value(gsm, me, depth)
        if depth == 0:
            return self.evaluate(gsm, me)
        maximising: bool = gsm.active_player == me
        best: int = -C.BOT_WIN_SCORE * 2 if maximising else C.BOT_WIN_SCORE * 2
        for action in sorted(gsm.find_all_possible_actions(self._rng), key=_order_key):
            value: int = self._try(gsm, action, me, depth - 1, alpha, beta)
            if maximising:
                best = max(best, value)
                alpha = max(alpha, value)
            else:
                best = min(best, value)
                beta = min(beta, value)
            if alpha >= beta:
                break
        return best

    def _try(self, gsm: GameStateMachine, action: Command, me: int, depth: int, alpha: int, beta: int) -> int:
        """Value of ``action``: submit it, search on, and always roll it back."""
        log_size: int = len(gsm.command_log)
        self.last_moves += 1
        result: CommandResult = gsm.submit(action)
        if not result.ok:  # cannot happen: possible actions are exactly what submit accepts
            if len(gsm.command_log) > log_size:  # a wrapper rejected part-way is logged: undo it too
                gsm.rollback()
            raise RuntimeError(f"Possible action {action!r} was rejected: {result.error}")
        try:
            return self._alphabeta(gsm, me, depth, alpha, beta)
        finally:
            gsm.rollback()

    # ------------------------------------------------------------------ voice
    def _mood(self, command: Command) -> Mood:
        if not self._spoke:
            self._spoke = True
            return Mood.START
        if any(c.kind is CommandType.ATTACK for c in _parts(command)):
            return Mood.ATTACK
        return Mood.DEFENSE
