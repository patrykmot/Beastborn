# PLAN: AI move ordering for the bot (tinygrad afterstate network)

> Request (2026-10-22): `12_AIBoot.md`. Add `AIFindActionStrategy`, a `FindActionStrategy` that lists the same
> actions as `AllFindActionStrategy`, sorted best first by a small **tinygrad Mini-ResNet** that rates the board
> *after* each action (an **afterstate value network**). Two modes: **WORKING** (rate and sort, for the alpha-beta
> bot) and **LEARNING** (replay memory, Bellman target `reward + gamma * max V(next afterstates)`, MSE loss, Adam).
> Pure tinygrad + numpy, no PyTorch. Must work with the existing `GameStateMachine` and `Command` classes.
> Status: **plan only**, nothing implemented. Questions Q1-Q7 below; each has a proposed default.

---

## 0. Starting point and limits

| What | Where | Today |
|---|---|---|
| Strategy contract | `control/find_action_strategy.py` | `FindActionStrategy.find_all_possible_actions(gsm) -> list[Command]`, must leave the game untouched, must be picklable (worker processes), `for_worker(seed)`. |
| Base actions | same file | `AllFindActionStrategy`: every whole turn of `gsm.active_unit`, fixed order, cached per `(gsm, version)`. |
| Bot | `control/bot.py` | Alpha-beta over whole unit turns, depth `BOT_SEARCH_DEPTH = 4`, `BOT_WORKERS = 1`. **It re-sorts every list it gets** (`sorted(actions, key=_order_key)`: attacks, then moves, then waiting), at the root and at every node. |
| Board | `domain/board.py`, `constant.py` | Maps are **10x10, 12x12 or 16x16** (`MAP_SIZES`). Elevation 0..`MAX_ELEVATION` (2). Difficult terrain = mud (`DIFFICULT_TERRAINS`). |
| Units | `data/units.json` | Boss (35 HP, the game is lost when it dies), Big Rat (venom), Peasant, Archer (range 4). |
| Layer rules | `tests/test_architecture.py` | `control` may import anything except pygame, web frameworks, `ui`, `reports`. tinygrad is allowed there. |
| Dependencies | `requirements.txt`, venv (Windows, Python 3.12) | flask, pydantic, pytest. **No numpy, no tinygrad.** Your Linux VM (3.10) has numpy 2.2.6. |

Facts that shape the design:

1. **The search value never depends on the order.** Alpha-beta returns the same value for any order of the same
   actions; a better order only cuts more branches. So the AI strategy can make the bot faster or slower, but never
   play worse - as long as it returns exactly the same set of actions as its base. This is also the main test.
2. **"Best first" means best for the player who moves now.** At a node where the opponent moves, the bot wants the
   opponent's strongest reply first (that is what cuts at a min node). So every value is from the point of view of
   `gsm.active_player` *before* the action, and `_state_to_tensor(gsm, me)` is called with that player as `me`
   (after `submit`, `gsm.active_player` may already be the other side).
3. **tinygrad's CPU device needs `clang` on the PATH** (or LLVM with `DEV=CPU:LLVM`), according to the tinygrad
   runtime docs. Your Windows venv may not have it; this is checked first (M0).

**Expectation (honest, to be measured in M5).** One network call is not cheap. With 32 filters and 3 residual
blocks on a 16x16 board, one afterstate costs about 15 M multiply-adds (~30 MFLOP); a node with ~25 actions about
0.75 GFLOP, so roughly 0.05-0.2 s per node on a CPU (a guess). Today the bot does ~0.18 ms per move (plan 11: 7 648
moves in 1.35 s at depth 6), and it already tries attacks first. So **at the root the network is cheap** (one call
per bot turn), but **at every node it will very likely make the search slower**, not faster: one call costs as much
as several hundred moves. The plan builds what was asked, measures it, and lists the follow-up (Q7, section 6).

---

## 1. Questions

| # | Question | Proposed default |
|---|---|---|
| Q1 | Maps are 10, 12 or 16 tiles wide, but `Flatten -> Linear(128)` needs one fixed board size. | **Pad every board to 16x16** (`max(MAP_SIZES)`), board in the top-left corner, and add channel 8 **"tile is on the board"** (1.0 / 0.0). Without it, padding looks exactly like flat grass with nobody on it. *Alternatives:* exactly 8 channels with zero padding; or one network per map size. |
| Q2 | The 8 channels do not show **which unit is the Boss**, and the game is won or lost on the Boss. | **Add channel 9 "my Boss" and channel 10 "enemy Boss"** (1.0 where it stands). 11 channels in total, the count lives in `constant.py`. DEF, Venom/Acid and attack range are left out for now (section 6). *Alternative:* exactly the 8 channels you listed (+ Q1). |
| Q3 | The bot re-sorts every list (attacks first), so most of the AI order would be thrown away. | **Small bot change:** a class attribute `FindActionStrategy.keeps_order: bool = False`; the bot sorts only when it is False; `AIFindActionStrategy` sets it True. Every existing strategy keeps today's behaviour. *Alternative:* no bot change (the AI order only counts within attacks / moves / waiting). |
| Q4 | What the network predicts. Read literally ("sort by predicted value", reward = score change), a network must also learn to count HP, and a new network sorts randomly. | **Value of an afterstate = its exact score now + the network's guess of how the score will change later:** `value(A) = evaluate(A) / AI_SCORE_SCALE + net(A)`. The last layer starts at zero, so a new network predicts 0 and the order is the exact score order (already useful). Training teaches only the "later" part. *Alternative:* the network predicts the whole value. |
| Q5 | The request stores one `next_state_tensor`, but the target needs `max` over all next afterstates. | **`next_state` = the best next afterstate** (by value, Q4) of the same player at their next turn; it is computed anyway when that player moves. At train time its value is recomputed with the current network. Memory stays one tensor per side (~5 KB). *Alternative:* store all next afterstates (exact `max` at train time, ~25x the memory). |
| Q6 | Who plays the training games, and with what rewards? | **Self-play script `tools/train_ai_bot.py`:** both players use the AI strategy greedily (1 ply, no alpha-beta: fast), with epsilon-greedy exploration (`epsilon = 0.1`). Reward = change of `evaluate` / `AI_SCORE_SCALE` (100) between a player's two afterstates, `+-AI_WIN_REWARD` (5) on a win / loss, `gamma = 0.95`. *Alternative:* learn from alpha-beta bot games (better teacher, much slower). |
| Q7 | tinygrad as a dependency, and where the AI is used. | **Optional:** `requirements-ai.txt` (tinygrad + numpy, exact versions; tinygrad 0.12.x was the newest listed when I checked, pinned at M0). The new module is **not** imported by `control/__init__.py`, so the game and the PythonAnywhere server run without tinygrad. The web bot keeps `RandomAllFindActionStrategy`; the AI is used through `BotPlayerController(find_action_strategy=...)` and the training script. Whether the web bot uses it is decided after M5. |

---

## 2. Design

New module `beastborn/control/ai_find_action_strategy.py`; the only file that imports tinygrad and numpy.

### 2.1 State encoding

```python
def _state_to_tensor(self, gsm: GameStateMachine, me: int) -> np.ndarray:   # (AI_CHANNELS, 16, 16), float32
```

Built from `gsm.view()` (public, read only). Off-board tiles are all zero.

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
| 8 | Tile on the board (Q1) | 1.0 |
| 9 | My Boss (Q2) | 1.0 |
| 10 | Enemy Boss (Q2) | 1.0 |

"My" = player `me` (the player who made the move, fact 2), never `gsm.active_player` after the move.
The terrain channels (0, 1, 8) never change in a game: computed once per board and reused.

### 2.2 The network (`AfterstateNet`)

```
input (B, 11, 16, 16)
stem:   Conv2d(11 -> 32, 3x3, padding=1) -> ReLU
body:   AI_BLOCKS (3) x ResBlock: Conv2d(32,32,3,p=1) -> ReLU -> Conv2d(32,32,3,p=1); + input; ReLU
head:   Conv2d(32 -> 2, 1x1) -> ReLU -> flatten (2*16*16 = 512) -> Linear(512, 128) -> ReLU -> Linear(128, 1)
output (B,) one float per afterstate
```

- A plain class with `__call__` (tinygrad style); `nn.state.get_parameters(net)` for the optimiser.
- The last `Linear` starts with zero weights and bias (Q4: a new network predicts 0).
- A ReLU after the 1x1 head conv (AlphaZero does this; the request does not say).
- `Tensor.manual_seed(seed)` before building: the same seed gives the same starting weights.
- Weights: `safe_save(get_state_dict(net), path)` / `load_state_dict(net, safe_load(path))`, a `.safetensors` file
  (~0.5 MB). Default path `constant.AI_WEIGHTS_FILE = data/ai_bot.safetensors`.
- Filters, blocks and channels are constants (`AI_FILTERS`, `AI_BLOCKS`, `AI_CHANNELS`): M5 may make the network
  smaller.

### 2.3 The strategy

```python
class AIMode(str, Enum):
    WORKING = "WORKING"
    LEARNING = "LEARNING"

class AIFindActionStrategy(FindActionStrategy):
    keeps_order: ClassVar[bool] = True                       # Q3: the bot uses this order as it is

    def __init__(self, mode: AIMode | str = AIMode.WORKING, weights: Path | None = None,
                 base: FindActionStrategy | None = None,     # default AllFindActionStrategy()
                 seed: int = 0, gamma: float = 0.95, learning_rate: float = 1e-3,
                 memory_size: int = 20_000) -> None: ...

    def find_all_possible_actions(self, gsm) -> list[Command]   # the base's actions, best first
    def rank(self, gsm) -> list[Ranked]                          # (action, value, afterstate tensor), best first
    def remember(self, state: np.ndarray, reward: float, next_state: np.ndarray, done: bool) -> None   # LEARNING
    def train_step(self, batch_size: int = 64) -> float | None   # LEARNING: the loss; None while memory < batch
    def save(self, path: Path) -> None
```

`rank(gsm)` (both modes):

1. `actions = base.find_all_possible_actions(gsm)`; `me = gsm.active_player`.
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
  tensors stored as float16 numpy arrays (half the memory).
- `train_step(batch_size)`: `random.sample` a batch; `target = reward + gamma * V(next_state)` (`0` instead of
  `V(next_state)` when `done`), computed with `Tensor.no_grad`; `loss = ((net(states) - target) ** 2).mean()`;
  `opt.zero_grad(); loss.backward(); opt.step()` with `nn.optim.Adam`, inside `Tensor.train()`.
- `V` here is the network's part only (Q4): the exact score part is already in the reward.

### 2.4 Training script (`tools/train_ai_bot.py`)

```
python tools/train_ai_bot.py --games 200 --size 12 --seed 1 --out beastborn/data/ai_bot.safetensors
```

For each game (`new_game(2, seed=...)`), every unit turn:

```
p = gsm.active_player
ranked = strategy.rank(gsm)                    # all afterstates of p, best first
if p has a pending (A_prev, score_prev):       # p's previous afterstate
    remember(A_prev, (score(best) - score_prev) / SCALE, ranked[0].tensor, done=False)   # Q5: the best next afterstate
play ranked[0] (or a random action with probability epsilon)
pending[p] = (its afterstate, its score)
every K turns: train_step(64)
at game over: remember(pending[p], final score change +- AI_WIN_REWARD, zeros, done=True) for both players
```

Prints games, loss and win rate against `RandomAllFindActionStrategy` (1 ply) every N games; saves the weights at
the end and every N games. Rounds are capped (`--max-turns`) so a stalled game ends.

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

---

## 3. Small change outside the new module (Q3)

```python
# find_action_strategy.py, FindActionStrategy
keeps_order: ClassVar[bool] = False   # True = the bot searches the actions in exactly this order

# bot.py, _best_action and _alphabeta
ordered = actions if self.find_action_strategy.keeps_order else sorted(actions, key=_order_key)
```

Nothing else in the bot changes. `game/`, `engine/`, `domain/`, `ui/` are not touched.

---

## 4. Tests (`tests/test_ai_strategy.py`, `pytest.importorskip("tinygrad")`, small boards, `DEV=CPU`)

- **Encoding:** shape `(11, 16, 16)`, float32; on a `custom_game` with known units the right cells hold the right
  values; `me = 1` swaps channels 2-4 with 5-7 and 9 with 10; off-board tiles are 0 and channel 8 is the board mask.
- **Game untouched:** `snapshot(gsm)` (as in `test_bot.py`) is the same after `find_all_possible_actions` and `rank`.
- **Same actions:** the result is a permutation of `AllFindActionStrategy`'s list.
- **New network = score order:** with `weights=None` the order follows the exact score after each action; a winning
  hit is first.
- **The bot's value does not change:** `BotPlayerController(depth=3, find_action_strategy=AI)` gives the same
  `last_value` as with `AllFindActionStrategy` on 3 fixed positions (fact 1).
- **`keeps_order`:** the bot uses the AI order as it is; the existing bot tests (other strategies) still pass.
- **Cache:** the same state twice = one forward pass.
- **Learning:** `memory_size` is respected; `train_step` returns None while memory < batch; the loss falls over
  ~50 steps on a tiny fixed batch; a `done` transition has target = reward.
- **Save / load:** the same predictions after a round trip; pickling a WORKING strategy gives the same order.
- **Architecture:** `tinygrad` / `numpy` are imported only by `control/ai_find_action_strategy.py` and `tools/`
  (a new rule in `test_architecture.py`, like the tkinter rule).

---

## 5. Milestones

| # | Step | Done when |
|---|---|---|
| M0 | `requirements-ai.txt` (tinygrad + numpy pinned); install in your venv and on the VM; check `clang` | `python -c "from tinygrad import Tensor; print((Tensor([1, 2]) + 1).numpy())"` prints `[2 3]` on both |
| M1 | Encoding (2.1) + its tests | encoding tests green |
| M2 | Network + WORKING mode + `keeps_order` (2.2, 2.3, 3) + tests | all tests green, old ones unchanged |
| M3 | LEARNING mode: memory, `remember`, `train_step`, save / load + tests | learning tests green |
| M4 | Training script + a first run (e.g. 200 games, 12x12) | weights file saved; loss and win rate vs 1-ply random written here |
| M5 | Measure at depth 4 and 6 on fixed positions: `RandomAll` vs AI (new net) vs AI (trained): moves, seconds, value | numbers written here, with a decision for Q7 (web bot or not, root only or every node, smaller net) |
| M6 | `docs/architecture.md` Bots section + README: the AI strategy, how to train, the optional dependency | text matches the code |

**Files changed:** new `beastborn/control/ai_find_action_strategy.py`, `tests/test_ai_strategy.py`,
`tools/train_ai_bot.py`, `requirements-ai.txt`, `beastborn/data/ai_bot.safetensors` (M4); a new AI section in
`beastborn/constant.py`; `keeps_order` in `find_action_strategy.py` and two lines in `bot.py` (Q3);
`tests/test_architecture.py` (one rule); `docs/architecture.md`, `README.md`.
**Not changed:** `game/`, `engine/`, `domain/`, `ui/`, `main.py`, the WSGI file, `requirements.txt`.

---

## 6. Not in this task (for later, if you want)

- **Use the network only where it pays** (likely after M5): a bot setting such as `ai_plies = 2`, so the AI order is
  used near the root and the cheap attacks-first order deeper down.
- **Use the network as the bot's `evaluate`** at depth 0 instead of the score difference. That is where a value
  network usually helps most, but it can change the bot's moves (ordering never does).
- More channels: DEF, Venom / Acid, attack range, unit type, who moves next.
- A target network or double DQN, if training is unstable.
- Training from alpha-beta games (Q6 alternative).

---

## Notes found while planning (not part of this task)

- `tests/test_smart_strategy.py` is not in git and imports `SmartFindActionStrategy` and `StrategyMode` from
  `beastborn.control`, which do not exist in the code yet, so pytest fails on that file today.
- `docs/architecture.md` says the bot's depth is 5; `constant.BOT_SEARCH_DEPTH` is 4.
