# PLAN: AI move ordering for the bot (tinygrad afterstate network)

> Request (2026-10-22): `12_AIBoot.md`. Add `AIFindActionStrategy`, a `FindActionStrategy` that lists the same
> actions as `AllFindActionStrategy`, sorted best first by a small **tinygrad Mini-ResNet** that rates the board
> *after* each action (an **afterstate value network**). Two modes: **WORKING** (rate and sort, for the alpha-beta
> bot) and **LEARNING** (replay memory, Bellman target `reward + gamma * max V(next afterstates)`, MSE loss, Adam).
> Pure tinygrad + numpy, no PyTorch. Must work with the existing `GameStateMachine` and `Command` classes.
> Status: **implemented** (M0-M3, M4 without the training run, M5 with a new network, M6), 2026-10-22.
> 300 tests pass on the Linux VM (14 new; 1 skipped: tkinter is not on the VM). `test_smart_strategy.py` left out
> (see the notes at the end). **No training was run** (your request): there is no `ai_bot.safetensors` yet.
> Notes from the implementation:
> - tinygrad is pinned to **0.11.0**: 0.12 and newer need Python 3.11, and the project (and the VM) use 3.10.
> - **M0:** tinygrad's CPU device runs `clang` (or the program named by `CC`) with clang-only flags; `gcc` cannot
>   replace it. The VM has no clang, and `LLVM=1` (tinygrad's LLVM device) produces code LLVM 15 rejects. The tests ran
>   on the VM with a clang stand-in made from the `ziglang` pip package (`CC=~/bin/zigclang`, VM only, not in the
>   project). **On Windows: install LLVM (it includes clang) so `clang` is on the PATH**, then check M0 there.
> - The training script's learner uses `RandomAllFindActionStrategy` as its base: with a fixed order, a new network
>   (every move worth 0 later) always picks the first action, waiting, and never walks towards the enemy.
> - **Fix 2026-10-23 (Windows, WinError 32 on the second save):** tinygrad's `safe_save` / `safe_load` memory-map
>   the file and keep it open, and Windows cannot delete or replace an open file. Weights are now read and written
>   with plain file I/O (`write_weights` / `read_weights`, same `.safetensors` format; written to `<file>.tmp`, then
>   `os.replace`). New test: the file is not kept open after save and load.
> - "tests force `DEV=CPU`" was dropped: tinygrad 0.11 has no `DEV` variable, and its default device is CPU.
> - **M5** (VM, 2 cores, new network, the bot unchanged as decided in Q3, 10x10 positions):
>
>   | Position (actions) | Depth | `RandomAll` | `All` | AI (new network) |
>   |---|---|---|---|---|
>   | seed 7, start (16) | 4 | 186 moves, 0.03 s | 186, 0.03 s | 186, 2.07 s (106 network calls) |
>   | seed 11, turn 8 (3) | 4 | 287, 0.13 s | 287, 0.12 s | 287, 3.92 s (120) |
>   | seed 5, turn 14 (7) | 4 | 80, 0.01 s | 80, 0.01 s | 80, 0.53 s (45) |
>   | seed 3, turn 20 (16) | 4 | 329, 0.06 s | 329, 0.06 s | 329, 2.16 s (173) |
>   | seed 7, start (16) | 6 | 1 149, 0.24 s | 1 149, 0.23 s | 1 149, 7.49 s (523) |
>   | seed 11, turn 8 (3) | 6 | 4 579, 1.49 s | 4 579, 1.50 s | 4 579, 34.79 s (2 041) |
>   | seed 5, turn 14 (7) | 6 | 1 672, 0.82 s | 1 672, 0.78 s | 1 672, 19.77 s (638) |
>   | seed 3, turn 20 (16) | 6 | 1 367, 0.22 s | 1 367, 0.22 s | 1 367, 9.35 s (785) |
>
>   The same value everywhere (as expected) and **the same number of moves**: with the bot's attacks-first sort
>   kept (Q3), the order inside each group did not change any cut-off in these positions. Every node costs 12-33 ms
>   more (trying each action + one network call), so the search is 20-70x slower. A trained network could not be
>   measured (no training). Decision for section 6: do not use the AI strategy in the web bot for now; it can only pay
>   off with the bot keeping its order (`keeps_order`) and the network used near the root only (`ai_plies`).

---

## 0. Starting point and limits

| What | Where | Today |
|---|---|---|
| Strategy contract | `control/find_action_strategy.py` | `FindActionStrategy.find_all_possible_actions(gsm) -> list[Command]`, must leave the game untouched, must be picklable (worker processes), `for_worker(seed)`. |
| Base actions | same file | `AllFindActionStrategy`: every whole turn of `gsm.active_unit`, fixed order, cached per `(gsm, version)`. |
| Bot | `control/bot.py` | Alpha-beta over whole unit turns, depth `BOT_SEARCH_DEPTH = 4`, `BOT_WORKERS = 1`. **It re-sorts every list it gets** (`sorted(actions, key=_order_key)`: attacks, then moves, then waiting), at the root and at every node. |
| Board | `domain/board.py`, `constant.py` | Maps are 10x10, 12x12 or 16x16 (`MAP_SIZES`). Elevation 0..`MAX_ELEVATION` (2). Difficult terrain = mud (`DIFFICULT_TERRAINS`). |
| Units | `data/units.json` | Boss (35 HP, the game is lost when it dies), Big Rat (venom), Peasant, Archer (range 4). |
| Layer rules | `tests/test_architecture.py` | `control` may import anything except pygame, web frameworks, `ui`, `reports`. tinygrad is allowed there. |
| Dependencies | `requirements.txt`, venv (Windows, Python 3.12) | flask, pydantic, pytest. **No numpy, no tinygrad.** Your Linux VM (3.10) has numpy 2.2.6. |

Facts that shape the design:
1. **"Best first" means best for the player who moves now.** At a node where the opponent moves, the bot wants the
   opponent's strongest reply first (that is what cuts at a min node). So every value is from the point of view of
   `gsm.active_player` *before* the action, and `_state_to_tensor(gsm, me)` is called with that player as `me`
   (after `submit`, `gsm.active_player` is already the next player to move; see 2.1).
2. **The order never changes the bot's choice of value.** Alpha-beta returns the same value for any order of the
   same actions; the order only changes how many branches are cut. So the AI strategy must return exactly the same
   actions as its base, only in another order (the main test, section 4).
3. **tinygrad's CPU device needs `clang` on the PATH** (or LLVM with `DEV=CPU:LLVM`), according to the tinygrad
   runtime docs. Your Windows venv may not have it; this is checked first (M0).

---

## 1. Decisions

| # | Question | Decision |
|---|---|---|
| Q1 | Maps are 10, 12 or 16 tiles wide, but `Flatten -> Linear(128)` needs one fixed board size. | **10x10 only.** The network input is 10x10, so no padding and no "on the board" mask. On a 12x12 or 16x16 game the AI strategy raises a clear `ValueError` (never a silent fallback). |
| Q2 | The 8 requested channels do not show **which unit is the Boss**, and the game is won or lost on the Boss. | **Add channel 8 "my Boss" and channel 9 "enemy Boss"** (1.0 where it stands): **10 channels**. The count lives in `constant.py`. DEF, Venom / Acid and attack range are left out for now (section 6). |
| Q3 | The bot re-sorts every list (attacks first), so most of the AI order is not used. | **No bot change.** The bot still tries attacks, then moves, then waiting; the AI order decides the order *inside* each of these groups. `bot.py` and `find_action_strategy.py` are not changed. |
| Q4 | What the network predicts. | **Value of an afterstate = its exact score now + the network's guess of how the score will change later:** `value(A) = evaluate(A) / AI_SCORE_SCALE + net(A)`. The last layer starts at zero, so a new network predicts 0 and the order is the exact score order (already useful). Training teaches only the "later" part: the network predicts the change only. |
| Q5 | The request stores one `next_state_tensor`, but the target needs `max` over all next afterstates. | **`next_state` = the best next afterstate** (by value, Q4) of the same player at their next turn; it is computed anyway when that player moves. At train time its value is recomputed with the current network. Memory stays one tensor per side (~2 KB as float16). |
| Q6 | How training runs. | **A separate script, `tools/train_ai_bot.py`, with logging** (console + log file) to follow progress and results. No UI. The learner plays 1 ply (no alpha-beta: fast) against `--opponent random` (a random legal turn) or `--opponent ai` (a frozen copy of an AI loaded from a weights file). Weights are saved to a file and loaded again with `--load`, so you choose what to train and continue where you stopped (details 2.4). |
| Q7 | tinygrad as a dependency. | **A normal dependency:** tinygrad and numpy pinned in `requirements.txt` and `pyproject.toml`; `AIFindActionStrategy` is exported from `beastborn.control`, so the whole application imports them. The PythonAnywhere server is not handled here (you install the packages there later). |

---

## 2. Design

New module `beastborn/control/ai_find_action_strategy.py`: the encoding, the network and the strategy.

### 2.1 State encoding

```python
def _state_to_tensor(self, gsm: GameStateMachine, me: int) -> np.ndarray:   # (AI_CHANNELS, 10, 10), float32
```

Built from `gsm.view()` (public, read only). A board that is not `AI_BOARD_SIZE` x `AI_BOARD_SIZE` (10x10) raises
`ValueError("The AI strategy supports only 10x10 boards, got 12x12")`.

| Ch | Content | Value |
|---|---|---|
| 0 | Terrain elevation | `elevation / MAX_ELEVATION` |
| 1 | Difficult terrain | 1.0 if `terrain.value in DIFFICULT_TERRAINS` |
| 2 | My units | 1.0 |
| 3 | My units' HP | `hp / stats.hp` |
| 4 | My units' energy | `energy / stats.max_en` (normalised, like HP; the request says raw energy) |
| 5 | Enemy units | 1.0 |
| 6 | Enemy units' HP | `hp / stats.hp` |
| 7 | Enemy units' energy | `energy / stats.max_en` |
| 8 | My Boss (Q2) | 1.0 |
| 9 | Enemy Boss (Q2) | 1.0 |

"My" = `me` = `gsm.active_player` read **before** the actions are tried (the player choosing now). Do not read
`gsm.active_player` between `submit` and `rollback`: it is already the next player to move. (Checked on 400 game
states: it was the other player in 396, and in 1 state it differed between actions of the same list.)
The terrain channels (0, 1) never change in a game: computed once per board and reused.

### 2.2 The network (`AfterstateNet`)

```
input (B, 10, 10, 10)
stem:   Conv2d(10 -> 32, 3x3, padding=1) -> ReLU
body:   AI_BLOCKS (3) x ResBlock: Conv2d(32,32,3,p=1) -> ReLU -> Conv2d(32,32,3,p=1); + input; ReLU
head:   Conv2d(32 -> 2, 1x1) -> ReLU -> flatten (2*10*10 = 200) -> Linear(200, 128) -> ReLU -> Linear(128, 1)
output (B,) one float per afterstate
```

- A plain class with `__call__` (tinygrad style); `nn.state.get_parameters(net)` for the optimiser.
- The last `Linear` starts with zero weights and bias (Q4: a new network predicts 0). Only the last layer: it
  still learns from the first step, because its inputs are not zero.
- A ReLU after the 1x1 head conv (AlphaZero does this; the request does not say).
- `Tensor.manual_seed(seed)` before building: the same seed gives the same starting weights.
- About 84 000 parameters. Weights: `safe_save(get_state_dict(net), path)` / `load_state_dict(net, safe_load(path))`,
  a `.safetensors` file (~0.35 MB). Default path `constant.AI_WEIGHTS_FILE = data/ai_bot.safetensors`.
- Filters, blocks and channels are constants (`AI_FILTERS`, `AI_BLOCKS`, `AI_CHANNELS`): M5 may make the network
  smaller.

### 2.3 The strategy

```python
class AIMode(str, Enum):
    WORKING = "WORKING"
    LEARNING = "LEARNING"

class AIFindActionStrategy(FindActionStrategy):
    def __init__(self, mode: AIMode | str = AIMode.WORKING, weights: Path | None = None,
                 base: FindActionStrategy | None = None,     # default AllFindActionStrategy()
                 seed: int = 0, gamma: float = AI_GAMMA, learning_rate: float = AI_LEARNING_RATE,
                 memory_size: int = AI_MEMORY_SIZE) -> None: ...

    def find_all_possible_actions(self, gsm) -> list[Command]   # the base's actions, best first
    def rank(self, gsm) -> list[Ranked]                          # best first
    def remember(self, state: np.ndarray, reward: float, next_state: np.ndarray, done: bool) -> None   # LEARNING
    def train_step(self, batch_size: int = AI_BATCH_SIZE) -> float | None   # LEARNING: the loss; None while memory < batch
    def save(self, path: Path) -> None

@dataclass(frozen=True)
class Ranked:
    action: Command
    value: float           # evaluate / AI_SCORE_SCALE + net, or +-inf for a won / lost game
    score: int             # evaluate(afterstate) for `me` (the training script needs it for rewards)
    afterstate: np.ndarray
```

`rank(gsm)` (both modes):

1. `actions = base.find_all_possible_actions(gsm)`; `me = gsm.active_player` (read once, here).
2. For each action: `submit` -> `_state_to_tensor(gsm, me)` + `evaluate` + game over? -> `rollback`, always in a
   `finally`, with the same "rejected = RuntimeError" check as `BotPlayerController._try`.
3. One batched forward pass over all afterstates (`Tensor.no_grad`), `value = evaluate / AI_SCORE_SCALE + net`.
4. An action that wins the game gets `+inf`, one that loses (e.g. own Boss killed by Venom at the next turn start)
   `-inf`: exact, no guessing.
5. Stable sort by value, highest first; equal values keep the base order (repeatable).
6. Cached for `(gsm, version)` like the other strategies, so a node visited again costs nothing.

`evaluate` = `gsm.score(me) - gsm.score(opponent)`, the same definition as the bot (a small shared helper, not a
copy of the bot).

**Mode A - WORKING:** `find_all_possible_actions` = the actions of `rank`. Loads `weights` if given (missing file =
`FileNotFoundError`, never a silent fallback); `weights=None` = a new network (exact score order, Q4).

**Mode B - LEARNING:** the same ranking, plus:

- `memory: deque[Transition]` with `maxlen = memory_size`; `Transition = (state, reward, next_state, done)`,
  tensors stored as float16 numpy arrays (half the memory: 20 000 transitions = ~80 MB).
- `train_step(batch_size)`: `random.sample` a batch; `target = reward + gamma * net(next_state)` (`0` instead of
  `net(next_state)` when `done`), computed with `Tensor.no_grad`; `loss = ((net(states) - target) ** 2).mean()`;
  `opt.zero_grad(); loss.backward(); opt.step()` with `nn.optim.Adam`, inside `Tensor.train()`.
- Both sides of the loss use `net` only, never `evaluate + net` (Q4): the exact score part is already in the reward.

### 2.4 Training script (`tools/train_ai_bot.py`)

```
python tools/train_ai_bot.py --games 200 --opponent random --seed 1
python tools/train_ai_bot.py --games 500 --opponent ai --opponent-weights old.safetensors --load beastborn/data/ai_bot.safetensors --seed 201
```

| Option | Default | Meaning |
|---|---|---|
| `--games` | 100 | games to play in this run |
| `--opponent` | `random` | `random` = a random legal turn; `ai` = a frozen AI (WORKING mode, greedy, no learning) |
| `--opponent-weights` | the `--load` file | weights of the `ai` opponent |
| `--load` | none (new network) | weights to continue from |
| `--out` | `AI_WEIGHTS_FILE` | where the weights are saved |
| `--seed` | 1 | seed of the first game (game *i* uses `seed + i`) |
| `--save-every` | 10 | save the weights every N games |
| `--max-turns` | 300 | unit turns before a game is stopped (counted as a draw) |
| `--log` | `train_ai_bot.log` | log file (`*.log` is already in `.gitignore`) |

The simplest loop. Only the learner's turns go into memory; the learner plays P1 in even games and P2 in odd games,
so it learns both sides. Every game: `new_game(2, seed=..., width=10, height=10)`.

```
every unit turn:
    if the opponent moves: play its turn (random, or the frozen AI's ranked[0]); continue
    ranked = learner.rank(gsm)                         # all afterstates of the learner, best first
    if pending (A_prev, score_prev):                   # the learner's previous afterstate
        remember(A_prev, (ranked[0].score - score_prev) / AI_SCORE_SCALE, ranked[0].afterstate, done=False)
    play ranked[0], or a random action with probability AI_EPSILON (0.1)
    pending = (its afterstate, its score)
    train_step()                                       # does nothing until the memory holds a batch
at game over / max turns:
    remember(A_prev, (final score - score_prev) / AI_SCORE_SCALE +- AI_WIN_REWARD (0 for a draw), zeros, done=True)
```

**Logging** (`logging`, INFO, to the console and the `--log` file; the same `LOG_FORMAT` as the server):

- at start: the options, the weights loaded (or "new network"), the device;
- after every game: game number, seed, learner's seat, result (win / loss / draw / stopped), unit turns, final score
  difference, mean loss of the game, memory size, seconds;
- every 10 games: win / loss / draw counts and the mean loss of the last 10 games;
- every save: the file name; at the end (or **Ctrl+C**, which saves first): totals and the line to continue,
  e.g. `continue with: --load beastborn/data/ai_bot.safetensors --seed 201`.

Continuing a run loads the weights only. The Adam state and the replay memory start empty again (simplest; the
memory refills within a few games).

The script is a thin `main(argv)` over a `train(...)` function, so a test can run one short game.

### 2.5 Worker processes

`BOT_WORKERS = 1` today, but the strategy must still pickle (contract). `__getstate__` drops the network, optimiser,
memory and cache and keeps the settings + weights path; a worker loads the weights on its first call. A LEARNING
strategy, or one with unsaved weights, raises a clear error when pickled. `for_worker(seed)` returns itself (no
randomness in WORKING mode).

### 2.6 Speed details

- One batched call per node, never one call per action.
- Terrain channels built once per board (2.1); afterstates written into one preallocated numpy array.
- If M5 shows Python overhead matters: `TinyJit` on the forward pass with the batch padded to a few fixed sizes
  (16, 32, 64, 128), because a JIT needs the same shapes every time.
- The device is tinygrad's default (`DEV` environment variable to change it); tests force `DEV=CPU`.

### 2.7 Constants (new AI section in `constant.py`)

`AI_BOARD_SIZE = 10`, `AI_CHANNELS = 10`, `AI_FILTERS = 32`, `AI_BLOCKS = 3`, `AI_SCORE_SCALE = 100`,
`AI_WIN_REWARD = 5.0`, `AI_GAMMA = 0.95`, `AI_EPSILON = 0.1`, `AI_LEARNING_RATE = 1e-3`, `AI_MEMORY_SIZE = 20_000`,
`AI_BATCH_SIZE = 64`, `AI_WEIGHTS_FILE = PACKAGE_DIR / "data" / "ai_bot.safetensors"`.

---

## 3. Changes outside the new module

- `beastborn/control/__init__.py`: export `AIFindActionStrategy` and `AIMode` (Q7).
- `requirements.txt` and `pyproject.toml`: `tinygrad==<0.12.x, exact version checked at M0>` and `numpy==2.2.6`
  (supports both your 3.12 venv and the 3.10 VM).
- `beastborn/constant.py`: the AI section (2.7).

`bot.py`, `find_action_strategy.py`, `game/`, `engine/`, `domain/`, `ui/` and `reports/` are not touched (Q3).

---

## 4. Tests (`tests/test_ai_strategy.py`, 10x10 boards, `DEV=CPU`)

- **Encoding:** shape `(10, 10, 10)`, float32; on a `custom_game` with known units the right cells hold the right
  values; `me = 1` swaps channels 2-4 with 5-7 and 8 with 9; a 12x12 game raises `ValueError`.
- **Game untouched:** `snapshot(gsm)` (as in `test_bot.py`) is the same after `find_all_possible_actions` and `rank`.
- **Same actions:** the result is a permutation of `AllFindActionStrategy`'s list.
- **New network = score order:** with `weights=None` the order follows the exact score after each action; a winning
  hit is first.
- **The bot's value does not change:** `BotPlayerController(depth=3, find_action_strategy=AI)` gives the same
  `last_value` as with `AllFindActionStrategy` on 3 fixed positions (fact 2).
- **Cache:** the same state twice = one forward pass.
- **Learning:** `memory_size` is respected; `train_step` returns None while memory < batch; the loss falls over
  ~50 steps on a tiny fixed batch; a `done` transition has target = reward.
- **Save / load:** the same predictions after a round trip; pickling a WORKING strategy gives the same order.
- **Training script:** `train(games=1, max_turns=20, ...)` into `tmp_path` writes a weights file and a log with a
  "game 1" line.

---

## 5. Milestones

| # | Step | Done when |
|---|---|---|
| M0 | tinygrad + numpy pinned in `requirements.txt` / `pyproject.toml`; install in your venv and on the VM; check `clang` | `python -c "from tinygrad import Tensor; print((Tensor([1, 2]) + 1).numpy())"` prints `[2 3]` on both |
| M1 | Encoding (2.1) + constants (2.7) + their tests | encoding tests green |
| M2 | Network + WORKING mode (2.2, 2.3) + export + tests | all tests green, old ones unchanged |
| M3 | LEARNING mode: memory, `remember`, `train_step`, save / load + tests | learning tests green |
| M4 | Training script (2.4) + a first run: 200 games vs `random` | weights file saved; results from the log written here (win rate, loss) |
| M5 | Measure at depth 4 and 6 on fixed 10x10 positions: `RandomAll` vs AI (new net) vs AI (trained): moves, seconds, value | numbers written here, with a decision on where to use the network (section 6) |
| M6 | `docs/architecture.md` Bots section + README: the AI strategy, how to train, how to continue a run | text matches the code |

**Files changed:** new `beastborn/control/ai_find_action_strategy.py`, `tests/test_ai_strategy.py`,
`tools/train_ai_bot.py`, `beastborn/data/ai_bot.safetensors` (M4); `beastborn/control/__init__.py`,
`beastborn/constant.py` (AI section), `requirements.txt`, `pyproject.toml`, `docs/architecture.md`, `README.md`.
**Not changed:** `control/bot.py`, `control/find_action_strategy.py`, `game/`, `engine/`, `domain/`, `ui/`,
`reports/`, `main.py`, the WSGI file.

---

## 6. Not in this task (for later, if you want)

- **Use the network only where it pays** (likely after M5): a bot setting such as `ai_plies = 2`, so the AI order is
  used near the root and the cheap attacks-first order deeper down.
- **Let the bot keep the AI order** (the `keeps_order` change from the first version of this plan, Q3).
- **Use the network as the bot's `evaluate`** at depth 0 instead of the score difference. That is where a value
  network usually helps most, but it can change the bot's moves (ordering never does).
- 12x12 and 16x16 boards (padding + an "on the board" channel, or one network per size).
- More channels: DEF, Venom / Acid, attack range, unit type, who moves next.
- Saving the Adam state and the replay memory, to continue a run exactly.
- A target network or double DQN, if training is unstable.
- Training from alpha-beta games.

---

## Notes found while planning (not part of this task)

- `tests/test_smart_strategy.py` is not in git and imports `SmartFindActionStrategy` and `StrategyMode` from
  `beastborn.control`, which do not exist in the code yet, so pytest fails on that file today.
- `docs/architecture.md` says the bot's depth is 5; `constant.BOT_SEARCH_DEPTH` is 4.
