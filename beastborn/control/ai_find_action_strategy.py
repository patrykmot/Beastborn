"""AI move ordering: a small tinygrad network rates the board after each action; the actions come best first.

``AIFindActionStrategy`` lists the same actions as its base strategy (default ``AllFindActionStrategy``), sorted by
the **value of the afterstate** (the board right after the action) for the player who moves now:

    value(A) = evaluate(A) / AI_SCORE_SCALE + net(A)

``evaluate`` is the exact score difference (my score minus my opponent's, as the bot's ``evaluate``); the network
(``AfterstateNet``, a Mini-ResNet) only guesses how that score will change later. Its last layer starts at zero, so a
new network predicts 0 and the order is the exact score order. A won game is ``+inf``, a lost one ``-inf``.
The alpha-beta bot finds the same value with any order of the same actions; a better order only cuts more branches.

Modes (``AIMode``): WORKING rates and sorts. LEARNING also keeps a replay memory (``remember``) and trains the network
(``train_step``): target = reward + gamma * net(best next afterstate of the same player), MSE loss, Adam.
Training games: ``tools/train_ai_bot.py``.

Only 10x10 boards (``constant.AI_BOARD_SIZE``): any other size raises ``ValueError``. 2-player games only.

Read only, like every strategy: each action is tried with ``submit`` and always undone with ``rollback``.

tinygrad picks its device itself (its CPU device compiles with ``clang``: on the PATH, or named by ``CC``).

tinygrad is not thread-safe (its compile cache is one SQLite connection, usable only by the thread that opened it), but
the web server answers each request on its own thread. So every tinygrad call of this module runs on one thread of its
own (``_on_tinygrad_thread``), whatever thread asks.
"""
from __future__ import annotations

import functools
import json
import os
import random
import threading
import weakref
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, NamedTuple, ParamSpec, TypeVar

import numpy as np
from tinygrad import Tensor, TinyJit, nn
from tinygrad.nn.state import get_parameters, get_state_dict, load_state_dict

from beastborn import constant as C
from beastborn.control.find_action_strategy import AllFindActionStrategy, FindActionStrategy
from beastborn.game.commands import Command, CommandResult
from beastborn.game.state import Phase
from beastborn.game.state_machine import GameStateMachine
from beastborn.game.view import GameView

# Input planes (AI_CHANNELS). "My" = the player who chooses the action, see ``AIFindActionStrategy.rank``.
CH_ELEVATION, CH_DIFFICULT = 0, 1
CH_MY_UNITS, CH_MY_HP, CH_MY_ENERGY = 2, 3, 4
CH_ENEMY_UNITS, CH_ENEMY_HP, CH_ENEMY_ENERGY = 5, 6, 7
CH_MY_BOSS, CH_ENEMY_BOSS = 8, 9


class AIMode(str, Enum):
    WORKING = "WORKING"  # rate and sort the actions
    LEARNING = "LEARNING"  # also remember transitions and train the network


@dataclass(frozen=True, eq=False)
class Ranked:
    """One action with the board after it, from the point of view of the player who chose it."""

    action: Command
    value: float  # score / AI_SCORE_SCALE + net, or +inf / -inf for a won / lost game
    score: int  # score difference after the action (my score minus my opponent's)
    afterstate: np.ndarray  # (AI_CHANNELS, AI_BOARD_SIZE, AI_BOARD_SIZE), float32


class Transition(NamedTuple):
    state: np.ndarray  # float16
    reward: float
    next_state: np.ndarray  # float16
    done: bool


def score_difference(gsm: GameStateMachine, me: int) -> int:
    """My score minus my opponent's (the bot's ``evaluate``). Only for 2-player games."""
    players: list[int] = [p.index for p in gsm.view().players]
    if len(players) != C.BOT_PLAYERS:
        raise ValueError(f"The AI strategy needs a {C.BOT_PLAYERS}-player game, got {len(players)} players")
    opponent: int = next(p for p in players if p != me)
    return gsm.score(me) - gsm.score(opponent)


# ---------------------------------------------------------------------- one thread for tinygrad
_P = ParamSpec("_P")
_R = TypeVar("_R")
_tinygrad_pool: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tinygrad")
_tinygrad_thread: threading.local = threading.local()  # .inside = True on the tinygrad thread


def _on_tinygrad_thread(function: Callable[_P, _R]) -> Callable[_P, _R]:
    """Run ``function`` on the one tinygrad thread and wait for it (directly when already there)."""
    def run(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        _tinygrad_thread.inside = True
        return function(*args, **kwargs)

    @functools.wraps(function)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        if getattr(_tinygrad_thread, "inside", False):
            return function(*args, **kwargs)
        return _tinygrad_pool.submit(run, *args, **kwargs).result()

    return wrapper


# ---------------------------------------------------------------------- weights files (.safetensors)
# Read and written with plain file I/O, not tinygrad's safe_load / safe_save: those go through tinygrad's "disk"
# device, which memory-maps the file and keeps it open, so on Windows the next save of the same file fails
# (WinError 32, "the file is used by another process").
_SAFETENSORS_F32: str = "F32"


def write_weights(path: Path, tensors: dict[str, Tensor]) -> None:
    """Save float32 tensors as a ``.safetensors`` file: written next to it first, then put in its place."""
    arrays: dict[str, np.ndarray] = {name: t.numpy().astype("<f4") for name, t in tensors.items()}
    header: dict[str, Any] = {}
    offset: int = 0
    for name, array in arrays.items():
        header[name] = {"dtype": _SAFETENSORS_F32, "shape": list(array.shape),
                        "data_offsets": [offset, offset + array.nbytes]}
        offset += array.nbytes
    text: bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")
    text += b" " * (-len(text) % 8)  # the format pads the header to 8 bytes
    temporary: Path = path.with_name(path.name + ".tmp")
    with open(temporary, "wb") as file:
        file.write(len(text).to_bytes(8, "little"))
        file.write(text)
        for array in arrays.values():
            file.write(array.tobytes())
    os.replace(temporary, path)  # a save stopped half-way never leaves a broken weights file


def read_weights(path: Path) -> dict[str, Tensor]:
    """Load a ``.safetensors`` file of float32 tensors into memory (the file is closed afterwards)."""
    data: bytes = path.read_bytes()
    size: int = int.from_bytes(data[:8], "little")
    header: dict[str, Any] = json.loads(data[8:8 + size])
    body: memoryview = memoryview(data)[8 + size:]
    tensors: dict[str, Tensor] = {}
    for name, info in header.items():
        if name == "__metadata__":
            continue
        if info["dtype"] != _SAFETENSORS_F32:
            raise ValueError(f"{path}: {name} is {info['dtype']}, only {_SAFETENSORS_F32} weights are supported")
        start, end = info["data_offsets"]
        tensors[name] = Tensor(np.frombuffer(body[start:end], dtype="<f4").reshape(info["shape"]).copy())
    return tensors


# ---------------------------------------------------------------------- the network
class ResBlock:
    def __init__(self, filters: int) -> None:
        self.conv1: nn.Conv2d = nn.Conv2d(filters, filters, 3, padding=1)
        self.conv2: nn.Conv2d = nn.Conv2d(filters, filters, 3, padding=1)

    def __call__(self, x: Tensor) -> Tensor:
        return (self.conv2(self.conv1(x).relu()) + x).relu()


class AfterstateNet:
    """Mini-ResNet: (B, AI_CHANNELS, 10, 10) afterstates -> (B,) how the score will change later (for the mover)."""

    def __init__(self, channels: int = C.AI_CHANNELS, filters: int = C.AI_FILTERS, blocks: int = C.AI_BLOCKS,
                 size: int = C.AI_BOARD_SIZE) -> None:
        self.stem: nn.Conv2d = nn.Conv2d(channels, filters, 3, padding=1)
        self.blocks: list[ResBlock] = [ResBlock(filters) for _ in range(blocks)]
        self.head: nn.Conv2d = nn.Conv2d(filters, 2, 1)
        self.hidden: nn.Linear = nn.Linear(2 * size * size, 128)
        self.value: nn.Linear = nn.Linear(128, 1)
        # A new network predicts exactly 0 (the order is then the exact score order). Only the last layer: its
        # inputs are not zero, so it still learns from the first step.
        self.value.weight = Tensor.zeros(1, 128).contiguous().realize()
        assert self.value.bias is not None
        self.value.bias = Tensor.zeros(1).contiguous().realize()

    def __call__(self, x: Tensor) -> Tensor:
        x = self.stem(x).relu()
        for block in self.blocks:
            x = block(x)
        x = self.head(x).relu().flatten(1)
        return self.value(self.hidden(x).relu()).flatten()


# ---------------------------------------------------------------------- the strategy
class AIFindActionStrategy(FindActionStrategy):
    """The base strategy's actions, best first by the afterstate value (see the module docstring).

    ``weights``: a ``.safetensors`` file to load (a missing file raises ``FileNotFoundError``); None = a new network
    (built from ``seed``, so it is the same every time). The network is built on first use.
    The ranking is cached for the last state asked about (one GSM, one ``version``).
    A WORKING strategy can be sent to the bot's worker processes (they load ``weights`` again); a LEARNING one cannot.
    """

    def __init__(
        self, mode: AIMode | str = AIMode.WORKING, weights: Path | str | None = None,
        base: FindActionStrategy | None = None, seed: int = 0, gamma: float = C.AI_GAMMA,
        learning_rate: float = C.AI_LEARNING_RATE, memory_size: int = C.AI_MEMORY_SIZE,
    ) -> None:
        self.mode: AIMode = AIMode(mode)
        self.weights: Path | None = Path(weights) if weights is not None else None
        if self.weights is not None and not self.weights.is_file():
            raise FileNotFoundError(f"AI weights file not found: {self.weights}")
        self.base: FindActionStrategy = base or AllFindActionStrategy()
        self.seed: int = seed
        self.gamma: float = gamma
        self.learning_rate: float = learning_rate
        self.memory: deque[Transition] = deque(maxlen=memory_size)
        self._rng: random.Random = random.Random(seed)
        self._net: AfterstateNet | None = None
        self._optimizer: nn.optim.Optimizer | None = None
        # compiled (TinyJit) network calls, one per batch size: ~2x faster than plain calls
        self._forward: dict[int, Callable[[Tensor], Tensor]] = {}
        self._train: dict[int, Callable[[Tensor, Tensor], Tensor]] = {}
        self._terrain: tuple[object, np.ndarray] | None = None  # (board, its two terrain planes)
        self._cache: tuple[weakref.ref[GameStateMachine], int, tuple[Ranked, ...]] | None = None

    # ------------------------------------------------------------------ FindActionStrategy
    def find_all_possible_actions(self, gsm: GameStateMachine) -> list[Command]:
        return [r.action for r in self.rank(gsm)]

    def __getstate__(self) -> dict[str, Any]:
        """Settings and the weights path only; a worker process builds or loads the network again."""
        if self.mode is AIMode.LEARNING:
            raise TypeError("A LEARNING AIFindActionStrategy cannot be sent to worker processes (save it, then use "
                            "a WORKING one with the saved weights)")
        state: dict[str, Any] = self.__dict__.copy()
        state.update(_net=None, _optimizer=None, _forward={}, _train={}, _terrain=None, _cache=None,
                     memory=deque(maxlen=self.memory.maxlen))
        return state

    # ------------------------------------------------------------------ ranking
    def rank(self, gsm: GameStateMachine) -> list[Ranked]:
        """Every action of the base strategy with its afterstate and value, best first (ties keep the base order).

        Empty when the game is over. Values are for ``me`` = the player to move now, read before any action is
        tried (between ``submit`` and ``rollback`` ``gsm.active_player`` is already the next player to move).
        """
        if gsm.phase is not Phase.AWAITING_COMMAND or gsm.active_unit is None:
            return []
        cache = self._cache
        if cache is not None and cache[0]() is gsm and cache[1] == gsm.version:
            return list(cache[2])
        me: int = gsm.active_player
        self._check_board(gsm.view())
        actions: list[Command] = self.base.find_all_possible_actions(gsm)
        states: np.ndarray = np.zeros((len(actions), C.AI_CHANNELS, C.AI_BOARD_SIZE, C.AI_BOARD_SIZE), np.float32)
        scores: list[int] = []
        exact: list[float | None] = []  # +inf / -inf for a finished game, else None
        for index, action in enumerate(actions):
            log_size: int = len(gsm.command_log)
            result: CommandResult = gsm.submit(action)
            if not result.ok:  # cannot happen: the base lists only what submit accepts
                if len(gsm.command_log) > log_size:  # a wrapper rejected part-way is logged: undo it too
                    gsm.rollback()
                raise RuntimeError(f"Possible action {action!r} was rejected: {result.error}")
            try:
                view: GameView = gsm.view()
                self._encode(view, me, states[index])
                scores.append(score_difference(gsm, me))
                exact.append(None if view.phase is not Phase.GAME_OVER or view.winner is None
                             else float("inf") if view.winner == me else float("-inf"))
            finally:
                gsm.rollback()
        predicted: np.ndarray = self.predict(states)
        values: list[float] = [
            e if e is not None else s / C.AI_SCORE_SCALE + float(p) for e, s, p in zip(exact, scores, predicted)
        ]
        order: list[int] = sorted(range(len(actions)), key=lambda i: -values[i])  # stable: ties keep the base order
        ranked: tuple[Ranked, ...] = tuple(Ranked(actions[i], values[i], scores[i], states[i]) for i in order)
        self._cache = (weakref.ref(gsm), gsm.version, ranked)
        return list(ranked)

    def state_to_tensor(self, gsm: GameStateMachine, me: int) -> np.ndarray:
        """The board now as the network sees it, for player ``me``: (AI_CHANNELS, 10, 10), float32."""
        view: GameView = gsm.view()
        self._check_board(view)
        out: np.ndarray = np.zeros((C.AI_CHANNELS, C.AI_BOARD_SIZE, C.AI_BOARD_SIZE), np.float32)
        self._encode(view, me, out)
        return out

    _state_to_tensor = state_to_tensor  # the name used in the request (12_AIBoot.md)

    @_on_tinygrad_thread
    def predict(self, states: np.ndarray) -> np.ndarray:
        """The network's part of the value for each afterstate (no training). The batch is padded to 16, 32, 64...
        so only a few shapes are ever compiled."""
        count: int = len(states)
        if count == 0:
            return np.zeros(0, np.float32)
        size: int = max(C.AI_MIN_BATCH, 1 << (count - 1).bit_length())
        padded: np.ndarray = np.zeros((size, *states.shape[1:]), np.float32)
        padded[:count] = states
        forward: Callable[[Tensor], Tensor] | None = self._forward.get(size)
        if forward is None:
            net: AfterstateNet = self._model()
            forward = self._forward[size] = TinyJit(lambda x: net(x).realize())
        return forward(Tensor(padded)).numpy()[:count]

    @staticmethod
    def _check_board(view: GameView) -> None:
        if (view.board.width, view.board.height) != (C.AI_BOARD_SIZE, C.AI_BOARD_SIZE):
            raise ValueError(f"The AI strategy supports only {C.AI_BOARD_SIZE}x{C.AI_BOARD_SIZE} boards, "
                             f"got {view.board.width}x{view.board.height}")

    def _encode(self, view: GameView, me: int, out: np.ndarray) -> None:
        """Write the planes into ``out`` (zeros). Terrain never changes in a game: built once per board."""
        if self._terrain is None or self._terrain[0] is not view.board:
            terrain: np.ndarray = np.zeros((2, C.AI_BOARD_SIZE, C.AI_BOARD_SIZE), np.float32)
            for pos in view.board.positions():
                tile = view.board.tile(pos)
                terrain[0, pos.y, pos.x] = tile.elevation / C.MAX_ELEVATION
                terrain[1, pos.y, pos.x] = 1.0 if tile.terrain.value in C.DIFFICULT_TERRAINS else 0.0
            self._terrain = (view.board, terrain)
        out[CH_ELEVATION:CH_DIFFICULT + 1] = self._terrain[1]
        for unit in view.units:
            mine: bool = unit.owner == me
            x, y = unit.position.x, unit.position.y
            out[CH_MY_UNITS if mine else CH_ENEMY_UNITS, y, x] = 1.0
            out[CH_MY_HP if mine else CH_ENEMY_HP, y, x] = unit.hp / unit.stats.hp
            out[CH_MY_ENERGY if mine else CH_ENEMY_ENERGY, y, x] = unit.energy / unit.stats.max_en
            if unit.stats.is_boss:
                out[CH_MY_BOSS if mine else CH_ENEMY_BOSS, y, x] = 1.0

    @_on_tinygrad_thread
    def _model(self) -> AfterstateNet:
        if self._net is None:
            Tensor.manual_seed(self.seed)
            net: AfterstateNet = AfterstateNet()
            if self.weights is not None:
                load_state_dict(net, read_weights(self.weights), verbose=False)
            self._net = net
        return self._net

    # ------------------------------------------------------------------ learning
    def remember(self, state: np.ndarray, reward: float, next_state: np.ndarray, done: bool) -> None:
        """Store one transition of a player: afterstate -> (reward) -> that player's best next afterstate."""
        self._require_learning()
        self.memory.append(Transition(state.astype(np.float16), float(reward), next_state.astype(np.float16),
                                      bool(done)))

    @_on_tinygrad_thread
    def train_step(self, batch_size: int = C.AI_BATCH_SIZE) -> float | None:
        """One Adam step on a random batch from memory; the loss (MSE), or None while the memory is too small."""
        self._require_learning()
        if len(self.memory) < batch_size:
            return None
        batch: list[Transition] = self._rng.sample(self.memory, batch_size)
        states: np.ndarray = np.stack([t.state for t in batch]).astype(np.float32)
        targets: np.ndarray = self.targets(batch)
        step: Callable[[Tensor, Tensor], Tensor] | None = self._train.get(batch_size)
        if step is None:
            step = self._train[batch_size] = TinyJit(self._step)
        loss: float = step(Tensor(states), Tensor(targets)).item()
        self._cache = None  # the network changed
        return loss

    def _step(self, states: Tensor, targets: Tensor) -> Tensor:
        """One Adam step (MSE of net(states) against targets); compiled by ``TinyJit`` in ``train_step``."""
        net: AfterstateNet = self._model()
        if self._optimizer is None:
            self._optimizer = nn.optim.Adam(get_parameters(net), lr=self.learning_rate)
        with Tensor.train():
            self._optimizer.zero_grad()
            loss: Tensor = ((net(states) - targets) ** 2).mean().backward()
            return loss.realize(*self._optimizer.schedule_step())

    def targets(self, batch: list[Transition]) -> np.ndarray:
        """reward + gamma * net(next_state); just the reward when the game ended (``done``)."""
        rewards: np.ndarray = np.array([t.reward for t in batch], np.float32)
        done: np.ndarray = np.array([t.done for t in batch], np.float32)
        following: np.ndarray = self.predict(np.stack([t.next_state for t in batch]).astype(np.float32))
        return (rewards + self.gamma * following * (1.0 - done)).astype(np.float32)

    @_on_tinygrad_thread
    def save(self, path: Path | str) -> Path:
        """Write the network's weights (``.safetensors``); a later WORKING strategy can load them."""
        target: Path = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        write_weights(target, get_state_dict(self._model()))
        return target

    def _require_learning(self) -> None:
        if self.mode is not AIMode.LEARNING:
            raise RuntimeError("Only a LEARNING AIFindActionStrategy remembers and trains")
