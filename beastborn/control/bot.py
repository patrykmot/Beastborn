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

Transposition table (TT): every searched position is stored as (position, depth left) -> (value, flag), with the
flag EXACT / LOWER (value is at least this) / UPPER (value is at most this), and position -> best action. A position
met again with the same depth left reuses the value (EXACT) or narrows alpha/beta (LOWER/UPPER); at any depth its
stored best action is tried first. Only the same depth left is reused ("=="), so every value - and so the chosen
move - is the same as without the table. Each bot has its own table for its whole game; a worker process keeps one
per bot too (for the last few bots), so worker tasks of later turns reuse it. The table is never sent to workers.
"""
from __future__ import annotations

import atexit
import logging
import multiprocessing
import os
import random
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from typing import Any

from beastborn import constant as C
from beastborn.control.bot_voice import BotVoice, Mood
from beastborn.control.controller import PlayerController
from beastborn.domain.board import Board
from beastborn.domain.position import Position
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

# ---------------------------------------------------------------------- transposition table
EXACT, LOWER, UPPER = 0, 1, 2  # the stored value is exact / at least the true value's lower bound / an upper bound
PositionKey = tuple[Any, ...]


@dataclass
class TranspositionTable:
    """What the bot already knows: values per (position, depth left) and the best action per position."""

    values: dict[tuple[PositionKey, int], tuple[int, int]] = field(default_factory=dict)  # -> (value, flag)
    moves: dict[PositionKey, Command] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.values)


@dataclass
class TTStats:
    """How the table helped in one search (summed over the worker processes)."""

    exact: int = 0  # positions answered from the table without searching
    cut: int = 0  # positions cut off by a stored bound
    hint: int = 0  # positions where the stored best action was tried first

    def add(self, other: TTStats) -> None:
        self.exact, self.cut, self.hint = self.exact + other.exact, self.cut + other.cut, self.hint + other.hint


_boards: dict[tuple[Any, ...], int] = {}  # board tiles -> small number (a position key names its board cheaply)
_worker_tables: OrderedDict[str, TranspositionTable] = OrderedDict()  # in a worker process: bot token -> its table
_WORKER_TABLES_KEPT: int = 8  # tables of the most recent bots a worker keeps (one bot = one game)


def _board_id(board: Board) -> int:
    tiles: tuple[Any, ...] = tuple(board.tile(Position(x, y)) for y in range(board.height) for x in range(board.width))
    return _boards.setdefault((board.width, board.height, tiles), len(_boards))


def _worker_table(token: str) -> TranspositionTable:
    """The table of one bot in this worker process (created on first use; old bots' tables are dropped)."""
    table: TranspositionTable | None = _worker_tables.pop(token, None)
    if table is None:
        table = TranspositionTable()
    _worker_tables[token] = table  # most recently used last
    while len(_worker_tables) > _WORKER_TABLES_KEPT:
        _worker_tables.popitem(last=False)
    return table

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
) -> tuple[int, int, TTStats]:
    """Runs in a worker process: the value of one root action, the moves it took and the TT stats. Module level.

    ``state`` is the worker's own copy, so its GSM (built here, with an empty history) can play and roll back freely.
    """
    bot._search_rng = random.Random(seed)  # the worker's copy of the bot: its own repeatable action order
    bot._tt = _worker_table(bot._tt_token) if bot.use_tt else None  # this worker's table for this bot
    bot._key_prefix = (_board_id(state.board), me)
    bot.last_moves, bot.last_tt = 0, TTStats()
    value: int = bot._try(GameStateMachine(state, engine), action, me, bot.depth - 1, alpha, _INFINITY)
    return value, bot.last_moves, bot.last_tt


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
    ``tt``: use the transposition table (off = plain alpha-beta, e.g. to compare).
    A subclass (e.g. with its own ``evaluate``) must be defined at module level to run in worker processes.
    """

    def __init__(
        self, depth: int = C.BOT_SEARCH_DEPTH, voice: BotVoice | None = None, rng: random.Random | None = None,
        workers: int | None = None, tt: bool = True,
    ):
        if depth < 1:
            raise ValueError("The search depth must be at least 1")
        self.depth: int = depth
        self.workers: int = max(1, workers or C.BOT_WORKERS or os.cpu_count() or 1)
        self.last_message: str | None = None  # what the bot says about the command it chose last
        self.last_value: int | None = None  # search value of that command (for logs, tests and future moods)
        self.last_moves: int = 0  # moves calculated (submitted and rolled back) to choose that command
        self.last_tt: TTStats = TTStats()  # how the transposition table helped in that search
        self.use_tt: bool = tt
        self._tt: TranspositionTable | None = TranspositionTable() if tt else None  # this bot's, for its whole game
        self._tt_token: str = uuid.uuid4().hex  # names this bot's table in worker processes
        self._key_prefix: tuple[int, int] = (0, 0)  # (board, me) of the current search, part of every position key
        self._rng: random.Random = rng or random.Random()
        # the action order inside one search; reseeded from _rng once per search, so _rng advances the same amount
        # every turn however the search goes (TT on/off, 1 or more workers): same seed -> same game
        self._search_rng: random.Random = self._rng
        self._voice: BotVoice = voice or BotVoice(rng=self._rng)
        self._spoke: bool = False  # has the bot said its START line yet

    def __getstate__(self) -> dict[str, Any]:
        """What goes to a worker process: everything but the table (a worker keeps its own, see ``_search_task``)."""
        state: dict[str, Any] = self.__dict__.copy()
        state["_tt"] = None
        return state

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
        self.last_moves, self.last_tt = 0, TTStats()
        self._key_prefix = (_board_id(gsm.view().board), me)
        actions: list[Command] = gsm.find_all_possible_actions(self._rng)
        if not actions:
            raise RuntimeError("No possible action while the game accepts commands")
        if len(actions) == 1:
            log.info("Bot P%d unit %s: only one possible action, no search", me + 1, gsm.active_unit)
            return actions[0], None
        self._search_rng = random.Random(self._rng.randrange(_SEED_RANGE))
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
                self.last_moves, self.last_tt, workers = 0, TTStats(), 1
                best, best_value = self._search_sequential(gsm, me, ordered)
        else:
            workers = 1
            best, best_value = self._search_sequential(gsm, me, ordered)
        tt: TTStats = self.last_tt
        log.info(
            "Bot P%d unit %s: best value %d, %d moves calculated (%d possible now, depth %d, %d workers) in %.2f s"
            " | TT: %d exact, %d cut, %d best-move hints, %d positions stored here",
            me + 1, gsm.active_unit, best_value, self.last_moves, len(actions), self.depth, workers,
            time.perf_counter() - started, tt.exact, tt.cut, tt.hint, len(self._tt) if self._tt is not None else 0,
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
        seeds: list[int] = [self._search_rng.randrange(_SEED_RANGE) for _ in ordered[1:]]  # drawn first: repeatable
        alpha: int = self._try(gsm, ordered[0], me, self.depth - 1, -_INFINITY, _INFINITY)
        state: GameState = gsm._state.copy()  # read only; workers get copies of this copy
        pool: ProcessPoolExecutor = _pool(self.workers)
        futures: list[Future[tuple[int, int, TTStats]]] = [
            pool.submit(_search_task, self, state, gsm.engine, action, me, alpha, seed)
            for action, seed in zip(ordered[1:], seeds)
        ]
        try:
            results: list[tuple[int, int, TTStats]] = [future.result() for future in futures]
        finally:
            for future in futures:
                future.cancel()  # after an error: drop the tasks not started yet
        best_index: int = 0
        best_value: int = alpha
        for index, (value, moves, tt) in enumerate(results, start=1):
            self.last_moves += moves
            self.last_tt.add(tt)
            if value > best_value:
                best_index, best_value = index, value
        return ordered[best_index], best_value

    def _alphabeta(self, gsm: GameStateMachine, me: int, depth: int, alpha: int, beta: int) -> int:
        if gsm.phase is Phase.GAME_OVER:
            return self._terminal_value(gsm, me, depth)
        if depth == 0:
            return self.evaluate(gsm, me)
        table: TranspositionTable | None = self._tt
        key: PositionKey | None = None
        hint: Command | None = None
        alpha_start, beta_start = alpha, beta  # the flag of the result is decided against the window we were given
        if table is not None:
            key = self._position_key(gsm)
            stored: tuple[int, int] | None = table.values.get((key, depth))
            if stored is not None:  # only the same depth left: values stay exactly as without the table
                value, flag = stored
                if flag == EXACT:
                    self.last_tt.exact += 1
                    return value
                if flag == LOWER:
                    alpha = max(alpha, value)
                else:
                    beta = min(beta, value)
                if alpha >= beta:
                    self.last_tt.cut += 1
                    return value
            hint = table.moves.get(key)
        maximising: bool = gsm.active_player == me
        best: int = -_INFINITY if maximising else _INFINITY
        best_action: Command | None = None
        actions: list[Command] = sorted(gsm.find_all_possible_actions(self._search_rng), key=_order_key)
        if hint is not None and hint in actions:  # the action that was best here before goes first
            actions.remove(hint)
            actions.insert(0, hint)
            self.last_tt.hint += 1
        for action in actions:
            value = self._try(gsm, action, me, depth - 1, alpha, beta)
            if maximising:
                if value > best:
                    best, best_action = value, action
                alpha = max(alpha, value)
            else:
                if value < best:
                    best, best_action = value, action
                beta = min(beta, value)
            if alpha >= beta:
                break
        if table is not None and key is not None:
            flag = UPPER if best <= alpha_start else LOWER if best >= beta_start else EXACT
            table.values[(key, depth)] = (best, flag)
            if best_action is not None:
                table.moves[key] = best_action
        return best

    def _position_key(self, gsm: GameStateMachine) -> PositionKey:
        """Everything the rest of the game depends on, from the bot player's point of view.

        Not the round number (no rule uses it) and not the fallen units (they are the ids missing from the board).
        Read from the GSM's state without changing it.
        """
        state: GameState = gsm._state
        units: tuple[Any, ...] = tuple(
            (u.id, u.position, u.hp, u.energy, u.defense, u.effects) for u in state.sorted_units()
        )
        # return self._key_prefix, units, state.active_unit, frozenset(state.acted_this_round), state.phase, state.winner
        return self._key_prefix, units, state.active_unit, state.phase

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
