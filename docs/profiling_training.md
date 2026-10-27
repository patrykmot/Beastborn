# Profiling: where training spends its time

> Measured 2026-10-27 on your PC (AMD Ryzen 5 5600X) in the Linux VM, Python 3.10, while your Windows training run
> was going. Training code as of that evening (`tools/train_ai_bot.py`, new rewards, `--opponent ai`,
> `--max-turns 120`). No code was changed for this.

> **Done (2026-10-27 evening): #1 (`reachable` cache in the GSM) and #3 (faster `GameState.copy`, one `view()` per
> action in `rank` instead of two).** Same 20 games before / after: identical game lines (moves, results, scores);
> game logic **0.64 s → 0.27 s per game (2.4x faster)**. Still open: #2 (measure the network on Windows) and #4.

## How it was measured

- The Linux VM has no `clang`, so tinygrad cannot run the network there. The network was replaced by a stand-in:
  `predict` returns zeros and `train_step` does nothing. **This profile covers all the game logic** (move lists,
  trying every action, encoding, score); the network part is estimated from your Windows log (section 3).
- `train(games=20, epochs=1, opponent="ai", max_turns=120, seed=12)` under `cProfile`: 20 games, 113 unit turns per
  game on average (Windows log: 103).
- Without the profiler: **0.66 s per game** of game logic (5.8 ms per unit turn). Under the profiler: 1.5 s
  (the profiler makes small, often-called functions look more expensive).

## 1. The big one: pathfinding is recomputed for every candidate move (~55%)

| Function | Calls (20 games) | Time incl. callees | Share |
|---|---|---|---|
| `AIFindActionStrategy.rank` (learner + opponent) | 2,262 | 28.4 s | 95% |
| ↳ `GameStateMachine.submit` (trying each action) | 39,969 | 24.3 s | 81% |
| ↳↳ `pathfinding.reachable` (Dijkstra) | **39,027** | **16.8 s** | **56%** |
| ↳ `get_opponent_move` (the opponent's rank) | 1,142 | 14.3 s | 48% |

Why: `rank` lists the actions (`AllFindActionStrategy`, which runs Dijkstra once for the unit) and then submits
every one of them to see the board after it. Each `MoveCommand` goes through `GameStateMachine._move`, which calls
`reachable_tiles` again and runs a **full Dijkstra flood** just to check that the destination is reachable. Same
unit, same position, same energy, same blocked tiles, so the same result about 17 times per `rank` (39,027 Dijkstra
runs for 2,262 rankings; ~15.6 actions per ranking).

**Fix idea:** cache `reachable` results in the GSM, keyed by what they depend on:
`(unit stats, start position, budget, frozenset(blocked))` (the board never changes during a game). A cache hit
returns the same dict, so the rules don't change. Expected: ~17 → ~1-2 Dijkstra runs per `rank`, so roughly
**half of the game-logic time saved** (game logic ~2x faster).

Inside Dijkstra the cost is many tiny calls; worth tuning only if the cache is not enough:

| Function | Calls | Own time | Where from |
|---|---|---|---|
| `Board.in_bounds` + `Position.inside` | 7.5 M each | 2.8 s | `Board.neighbours`, `Board.tile` |
| `Board.tile` | 4.2 M | 1.1 s (2.8 s incl.) | Dijkstra, `step_cost` |
| `StandardCalculationEngine.step_cost` | 2.6 M | 1.3 s (3.1 s incl.) | Dijkstra |
| `Position.neighbours` + `Board.neighbours` | 0.8 M | 1.4 s (3.9 s incl.) | Dijkstra |
| Enum `.value` (`types.py __get__`) | 3.1 M | 1.1 s | `_is_difficult`: `tile.terrain.value in DIFFICULT_TERRAINS` |

Ideas: precompute per board the neighbour list of every tile, and the step cost of every (tile, neighbour) pair
(per `move_penalty`); compare terrain enum members (or a precomputed "difficult" flag on `Tile`) instead of
`.value`; `Board.tile` checks bounds again even where the caller already did.

## 2. Smaller hot spots (each 5-10%)

| What | Calls | Time incl. | Share | Idea |
|---|---|---|---|---|
| `GameStateMachine.view` | 73 k | 2.6 s | 9% | `rank` builds a full `GameView` (all units, current defense) after every action, for `_encode` and the phase check; it could read the state directly. |
| `GameState.copy` (`copy.copy`, 440 k shallow copies) | 40 k | 2.2 s | 7% | One copy per `submit`. `copy.copy` goes through `__reduce_ex__`; a hand-written copy (or `__slots__` + direct constructor) is several times faster. |
| `score_difference` → `engine.score` | 35 k | 1.9 s | 6% | Two scores per action; could be one pass. |
| `find_all_possible_actions` | 2.3 k | 2.0 s | 7% | Already once per ranking (cached); fine. |
| Combat (`resolve_attack`, `Fraction`) | 24 k | 1.2 s | 4% | Exact fractions are part of the rules; leave it. |
| `_encode` (network input) | 35 k | 0.3 s | 1% | Cheap; nothing to do. |

## 3. The network (not measured here, estimated)

- Windows log of the run going now (last 2,458 games): **1.21 s per game**, 103 unit turns, 13 training steps per
  game, device GPU. That's 11.8 ms per unit turn against 5.8 ms of game logic here: **roughly half of the
  training time is the network** (about 113 `predict` calls per game with ~16 states each, plus the 13
  `train_step`s with batch 128). Rough: Windows runs Python 3.12 and the VM shared the CPU with your training.
- With such small batches, a GPU call probably costs mostly per call (launch, copying to and from the GPU), not per
  state. To check on Windows:
  `python -m cProfile -o train.prof tools/train_ai_bot.py --games 20 --epochs 1 --opponent ai --opponent-weights ... --max-turns 120 --log x.log`,
  then `python -c "import pstats; pstats.Stats('train.prof').sort_stats('cumtime').print_stats(30)"`. The time in
  `_on_tinygrad_thread.<locals>.wrapper` is the network (cProfile only sees the main thread, which waits there).
  Also worth trying: `DEV=CPU` (or tinygrad's CPU device) against the GPU for this tiny network.

## 4. What to do first

| # | Change | Expected gain | Effort |
|---|---|---|---|
| 1 | Cache `reachable` in the GSM (section 1) | Game logic ~2x faster, **whole training ~25% faster** | Small |
| 2 | Measure the network on Windows (section 3), then CPU vs GPU | Up to ~50% of the time is there | Measuring only |
| 3 | Faster `GameState.copy` and fewer `view()` calls in `rank` | ~10-15% of game logic | Small to medium |
| 4 | Precomputed neighbours / step costs / terrain flag in Dijkstra | Big if #1 is not done; small after it | Medium |

All game-logic changes (1, 3, 4) also make the web bot and the alpha-beta search faster, because they use the same
`submit` / `rollback` path. The existing tests in `tests/test_state_machine.py` cover the rules they touch.

## Reproduce

Stand-in network, then profile one epoch (run from the project root, after `mkdir prof`):

```python
import cProfile, numpy as np
from pathlib import Path
from beastborn.control import ai_find_action_strategy as ai
import tools.train_ai_bot as t

ai.AIFindActionStrategy.predict = lambda self, s: np.zeros(len(s), np.float32)
ai.AIFindActionStrategy.train_step = lambda self, batch_size=None: None
ai.AIFindActionStrategy.save = lambda self, p: (Path(p).write_bytes(b""), Path(p))[1]
cProfile.run('t.train(games=20, epochs=1, opponent="ai", out=Path("prof/ai.safetensors"), '
             'log_file=None, max_turns=120, seed=12)', "prof/train.prof")
```
