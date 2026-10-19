# PLAN: Bot search on all CPU cores

> Request (2026-10-19, updated 15:14): run the bot's move search (`BotPlayerController.choose_command`) on several
> CPU cores at once, as many as the system has. **Change only the bot** (`beastborn/control/bot.py` and its tests).
> No change to any other code (`find_all_possible_actions`, the GSM, pathfinding, the web app...). Keep it as simple
> as possible. No special handling for any hosting platform: the game may move to another server or become a
> desktop application; where parallel search is not wanted, set the bot to 1 worker.
> Status: **implemented** (M1-M4). 281 tests pass (3 new). Notes from the implementation:
> - Q1: `BOT_WORKERS` went to the bot section of `constant.py` (the default).
> - Workers get `(GameState copy, engine)` and build their own GSM, instead of a pickled GSM: a GSM holds an
>   `itertools.count`, whose pickling is deprecated in Python 3.12 and removed in 3.14.
> - **Window changed from (alpha - 1, +inf) to (alpha, +inf).** With `alpha - 1`, positions where most actions are
>   worth the same (value 0 early in the game) lost almost all pruning: 130 368 moves instead of 7 648. With
>   `alpha` the tasks search exactly like the one-process search; ties still go to the first action, so the chosen
>   move is the same as with one process.
> - M3 (your PC's Linux VM, **2 cores**, seed 11, round 2, 27 root actions): depth 6: 1.35 s -> 1.06 s;
>   depth 7: 2.54 s -> 1.98 s; the same moves (7 648 / 15 715) and the same choice. A position with only 3 root
>   actions gains little (3.40 s -> 3.19 s), because the first action is always searched alone.

---

## 0. Starting point and limits

- Today `_best_action` runs alpha-beta in one process: for each root action, `submit` → search → `rollback`.
- Measured on your PC (Linux VM, 2 cores, depth 8, mid-game): one search tried **111 460 moves**, with **5
  actions at the root**. 63 % of the time is in pathfinding (not part of this task).
- **Processes, not threads.** Python 3.12 runs Python code in one thread at a time (the GIL), so threads would not
  make this search faster. Processes do.
- **What to expect:** the speed-up is at most the number of root actions minus one (the first one is searched
  before the others, see 2.2), and the slowest root action sets the time. With 5 root actions, at best ~4x on a
  4+ core PC. The search ends sooner, but total CPU use goes **up** a little (workers share less pruning).

---

## 1. Questions

| # | Question | Proposed default |
|---|---|---|
| Q1 | Where does the worker setting live? | In the **bot section of `constant.py`**: `BOT_WORKERS: int \| None = None` (None = all cores, 1 = today's single-process search), as your project keeps all constants there. That section belongs to the bot; nothing else in the file changes. If you want `constant.py` untouched, it goes at the top of `bot.py` instead. `BotPlayerController(workers=...)` can override it per bot (tests use this). |

---

## 2. Design (all in `control/bot.py`)

### 2.1 The worker pool

- One `concurrent.futures.ProcessPoolExecutor` per server process, created on the **first** bot search and reused
  (starting a worker costs ~0.2-0.5 s on Windows, because it imports `beastborn`). A module-level
  `_pool()` function creates it; `atexit` shuts it down.
- `spawn` start method on every OS (Windows only has `spawn`; `fork` inside the threaded Flask server can deadlock).
- Workers = `BotPlayerController(workers=...)`, else `BOT_WORKERS`; `None` = all cores (`os.cpu_count()`).
  `1` = no pool: exactly today's code (e.g. on a server where parallel search is not wanted).
- No locks and no shared memory: tasks share nothing (2.2), so two games searching at the same time just share
  the workers.

### 2.2 How the search is split

```
_best_action(gsm, me):
    actions = root actions, ordered as today (rng shuffle, attacks first)
    if workers == 1 or len(actions) < 3:  -> today's sequential loop (unchanged)
    1. search actions[0] here, as today              -> exact value v0 = alpha
    2. send actions[1:] to the pool, one task each, all with the same window (alpha - 1, +inf)
    3. collect (value, moves) from every task
    4. best = highest value; between equal values the LOWEST index in `actions`
```

- **Why the first action is searched here first:** it gives a real alpha. Without one, every worker would search
  its whole subtree and almost nothing would be cut.
- **Why `alpha - 1`:** a task result `> alpha - 1` (that is `>= alpha`) is an **exact** value; a smaller one is
  only an upper bound, and it is always lower than `v0`, so it can never win. So the chosen move is always truly
  among the best. Ties resolve by index, never by which worker finished first, so **the same seed gives the same
  move**.
- **No shared alpha between workers** (simplest). Possible later: a shared `multiprocessing.Value` updated as
  results come in. It would cut more, but adds shared state.

### 2.3 What a task gets

```python
def _search_task(bot: BotPlayerController, gsm: GameStateMachine, action: Command, me: int,
                 alpha: int, seed: int) -> tuple[int, int]:      # module-level: picklable
    bot._rng = random.Random(seed)       # its own rng: inner action order is repeatable
    bot.last_moves = 0
    value = bot._try(gsm, action, me, bot.depth - 1, alpha, C.BOT_WIN_SCORE * 2)
    return value, bot.last_moves
```

- `gsm` = a **light copy** made by the bot: `GameStateMachine(gsm._state.copy(), gsm.engine)`, built **once per
  search**. It holds the same position without the rollback history (pickled: ~5 KB instead of 32 KB and growing)
  or caches. It only *reads* the GSM's internal `_state` and changes nothing outside the bot. *Alternative if you
  prefer no private access:* pickle the whole `gsm` (simpler; larger, and it grows during the game).
- **No `copy.deepcopy`:** pickling already gives every task its own copy. The parent's `gsm` is never sent or
  changed, so "the state is unchanged after the search" holds by construction. The existing check stays.
- `bot` is the controller itself (depth, `evaluate`). A subclass with its own `evaluate` works in workers if it is
  defined at module level (classes defined inside a function cannot be pickled).
- Seeds are drawn from the bot's `rng` in root order before submitting, so a run repeats.

### 2.4 Logging

Same line as today. Moves = the first action's moves + the moves of all tasks, plus the worker count:
`Bot P2 unit 6: best value 12, 84 310 moves calculated (19 possible now, depth 8, 8 workers) in 9.80 s`.
Workers do not log: with `spawn` on Windows their logs would not reach your console.

### 2.5 Errors and fallback

| Case | What happens |
|---|---|
| A task raises (a bug, e.g. in `evaluate`) | `future.result()` re-raises it in the parent; the other pending tasks are cancelled; the exception goes up as today. |
| The pool cannot start (the platform does not allow processes, limits) or a worker dies (`BrokenProcessPool`) | Log a warning, drop the pool, search **this** turn sequentially. The next search tries once more to create a pool. |
| Server reload / exit | `atexit` → `shutdown(cancel_futures=True)`. |

### 2.6 Multiprocessing edge cases (review)

| Risk | Handling |
|---|---|
| `deepcopy` overhead | Not used; one light copy per search, pickled per task (~5 KB, well under 1 ms). |
| Pickling | Only module-level functions and plain data (frozen dataclasses, `random.Random`, the engine). No lambdas, bound pool objects or local classes. |
| Pool management | One lazy pool for the server's life, never one per search; `spawn` everywhere; `atexit` shutdown; broken pool → fallback. |
| Memory leaks | Tasks keep nothing in the worker (the GSM and its caches die with the task); the parent keeps no per-search data. |
| Wrong move from bounds | `alpha - 1` window: only exact values can win (2.2). |
| Repeatability | Fixed task seeds + ties by index. |

---

## 3. Tests (`tests/test_bot.py`, simple)

- **Same value:** with `workers=2` (real processes), `last_value` equals the sequential search on 3 fixed positions
  (depth 3).
- **Repeatable:** the same seed gives the same move twice with `workers=2`.
- **Parent untouched:** the `snapshot(gsm)` check from the existing tests.
- **Error:** a module-level subclass whose `evaluate` raises → the same exception comes out, and the next search works.
- **Fallback:** the pool factory patched to fail → the search runs sequentially, gives the same value, and logs one warning.
- **`workers=1`:** today's code path; all existing tests unchanged.

---

## 4. Milestones

| # | Step | Done when |
|---|---|---|
| M1 | Pool + worker count + `_search_task` + parallel branch in `_best_action` | all existing tests green |
| M2 | Tests (section 3) | green on your PC (Windows, 3.12) and the VM (3.10) |
| M3 | Measure: same positions with 1 worker and with all cores (moves, seconds) | numbers added here |
| M4 | `docs/architecture.md` Bots section: 3 lines on workers (`BOT_WORKERS`, 1 = single process) | text matches the code |

**Files changed:** `beastborn/control/bot.py`, `tests/test_bot.py`, the bot section of `beastborn/constant.py`
(Q1) and `docs/architecture.md`. **Not changed:** `game/`, `engine/`, `domain/`, `ui/`, `main.py`, the WSGI file.

---

## 5. Not in this task (for later, if you want)

These need changes outside the bot, so they are only listed:
- faster pathfinding (63 % of the search time; would not change any move);
- a transposition table (needs a state key from the GSM);
- iterative deepening with a time limit, and better move ordering (killer moves).
