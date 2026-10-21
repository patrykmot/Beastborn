# PLAN: Reuse what the bot already calculated (transposition table and more)

> Request (2026-10-19 17:43): reuse earlier calculations so CPU time is not spent twice on the same thing.
> Proposed: a transposition table (TT) `state hash -> (score, flag, best move)` with EXACT / LOWERBOUND / UPPERBOUND,
> and trying the cached best move first. Memory does not matter; speed does. **Ideas and a plan, no code.**
> Rule from plan 11: only the bot may change (`control/bot.py`), unless you allow more (question Q1).
> Status: **TT implemented** (Idea A, bot only; no iterative deepening, no change outside the bot - your answer).
> 284 tests pass (3 new). Results, bot vs bot from the same seed, TT off vs on (2-core VM):
> - seed 11, depth 5, 24 turns: 45 090 vs 45 090 moves (1 worker: 13.12 s vs 13.01 s; 2 workers: 9.02 s vs 9.27 s);
> - seed 7, depth 6, 14 turns: 60 073 vs 60 073 moves, 16.59 s vs 17.03 s.
> - TT hits: 0 exact, 0 cut, 140-1 576 best-move hints - **no saving in real play**, as section 1 predicted; the
>   key costs ~2-3 % time. The table does pay off when the same position is searched again (test: exact hits,
>   fewer moves, same value).
> - Found and fixed on the way: the bot's rng was also used inside the search, so a different search shape
>   (TT on/off, number of workers) changed later tie-breaks and so the game. Now one seed per search is drawn from
>   the bot's rng: the same seed plays the same game in every setup (test added).
> - An earlier run showed "16 % fewer moves" - that was two different games (the rng issue above), not the TT.

---

## 1. First, the facts (measured on your code, 2-core VM, depth 6-7, seeds 7 and 11)

I counted what a TT could save before designing one (counting subclass in a script, no project change):

| Measurement | Result |
|---|---|
| Same position searched again **within one search** (any depth) | **0 %** (3 positions, 1 135 - 3 707 nodes each) |
| Positions of the next search already searched in the **previous search** | **2-3 %**, and **none** with enough depth left to reuse the value |
| Run the same search again with the **remembered best move first** at every position (= the best a TT move can do for ordering) | **0 %, 0 %, 16 %** fewer moves |
| Where the time goes (one search, ~7.5 s) | `submit` 5.2 s, of which **re-validating moves (`_move` -> pathfinding) 3.8 s**; `find_all_possible_actions` 2.3 s; the bot itself (evaluate, ordering) ~2 % |

Why there are no transpositions: the GSM picks the unit, and one ply is a whole unit turn. So two different move
sequences almost never meet in the same position: unit A then unit B is the only order there is, and each unit's
turn leaves its own energy, position and HP behind.

**Conclusion:** a classic TT is cheap to add and correct, but here it saves little (0-16 %, all from move
ordering). The real repeated work is lower down: in a position with 20 move actions, the GSM computes the unit's
reachable tiles once to list the actions, and then **20 more times** to validate each move on `submit`. That is
about half of all the time, and it lives outside the bot.

---

## 2. Idea A: transposition table in the bot (bot-only, allowed today)

### 2.1 The key (built by the bot from the state; the GSM is not changed)

```
key = (board fingerprint,               # tuple of tiles, computed once per search (maps differ between games)
       me,                              # values are from the bot player's point of view
       units: (id, position, hp, energy, defense, effects) for every unit, by id,
       active unit, units that already acted this round, phase, winner)
```

- **Not in the key:** the round number (no rule uses it, so leaving it out lets more positions match), the seed,
  the command log. Fallen units are the ids missing from the board.
- `attacks_this_turn` is always 0 at a node (every action is a whole turn), so it can be checked, not stored.
- Read from `gsm._state` (read only, like the parallel search already does). Cost measured: **0.002 ms** per key,
  against ~0.8 ms per node.

### 2.2 The entry and the probe (your pseudocode, plus what it needs to be correct)

```
entry = (depth_left, value, flag, best_move)

probe at a node (not at the root: the root must return a move):
    if entry.depth_left == depth_left:          # see 2.3: "==", not ">="
        EXACT -> return value
        LOWER -> alpha = max(alpha, value)
        UPPER -> beta  = min(beta, value)
        if alpha >= beta -> return value
    try entry.best_move first (even if the depth does not match: it is only a hint)

store after the search of a node:
    flag from the ORIGINAL alpha/beta (kept before any change):
        value <= original alpha -> UPPER     value >= original beta -> LOWER     else EXACT
    best_move = the action that gave the value (or that caused the cut)
```

Points that are easy to get wrong:
1. **Depth must be in the entry.** A value from a 2-ply search is not valid for a 5-ply search.
2. **The flag uses the window from *before* the probe changed it.** Otherwise bounds become wrong values.
3. **Win/loss values depend on the depth left** (`BOT_WIN_SCORE + depth`, so faster wins are preferred). With the
   "==" rule below they can be stored as they are. With ">=" they must be stored relative to the node and
   converted back on probe.
4. **No TT cut at the root,** or the bot has a value but no move.

### 2.3 "==" or ">=" on depth?

- `>=` is the textbook rule: a deeper result may answer a shallower question. But then the chosen move depends on
  what is in the table (which earlier searches ran, and in which worker), so **the same seed may give a different
  move**.
- `==` keeps every value exactly what the plain search would return, so moves stay repeatable and equal to today.
  The measurement (no reusable entries with enough depth anyway) says `==` loses nothing here. **Proposed: `==`.**

### 2.4 Lifetime and processes

- **One TT per process, kept for the life of the process** (memory does not matter): the parent's for its own
  searches, and each worker's for the tasks it runs (workers live as long as the pool). Entries never go stale:
  the key fully describes the position, and scoring only changes with a code change (= a restart).
- **No TT shared between processes.** A shared dict (`multiprocessing.Manager`) costs a round trip per lookup, about
  as much as a node, and there are almost no hits to share. Shared memory is possible but complex, with no
  measured gain.
- Log line: add `TT hits` (exact / bound / best-move-first), so the gain shows in real games.

### 2.5 Expected gain: **0-16 % fewer moves**, almost all from "best move first".

---

## 3. Idea B: iterative deepening + TT (bot-only)

Search depth 1, 2, ... N; each iteration stores best moves that order the next one. This is where a TT normally
pays off, because every iteration revisits the previous one's positions.

- Measured ordering gain is small here (0-16 %), and the shallow iterations cost extra (roughly +10-30 % moves). So
  **for speed alone it is about even, or a loss.**
- It is worth it only for a **time limit** ("think at most 3 s, play the best move of the deepest finished depth").
  Not proposed for speed.

---

## 4. Idea C: stop repeating the expensive calculation (outside the bot: needs your OK)

This is where "the same thing calculated again" really happens (section 1). Options, from small to large:

| # | Change | Where | Expected gain | Changes moves? |
|---|---|---|---|---|
| C1 | **Cache the reachable tiles per state** in the GSM: `find_all_possible_actions` and the validation in `submit` for the same position share one pathfinding result (keyed by state version + unit + energy). | `game/state_machine.py` (private cache, like the action cache) | **~40-50 % of the search time** (the 3.8 s of move validation, measured) | No |
| C2 | Keep the action lists of **more than one state** (today only the last one is cached; memory does not matter). Then rolling back to a parent does not recompute its list. | `game/state_machine.py` | small within a search (positions are not revisited); helps with iterative deepening | No |
| C3 | Precompute the board's neighbours and step costs once (the board never changes). | `game/pathfinding.py` | every pathfinding call faster (~2x on that part) | No |

C1 alone is probably worth more than everything in sections 2 and 3 together, and it changes no decision.

---

## 5. Questions

| # | Question | Proposed default |
|---|---|---|
| Q1 | May I change `game/state_machine.py` for **C1** (a private cache; no behaviour change, all GSM tests must stay green)? | **Yes, C1 first.** It is the biggest measured saving. |
| Q2 | Do the TT anyway (Idea A) even though it measures 0-16 %? | **Yes, but after C1**, with `==` on depth (repeatable moves) and TT hits in the log. It is small and bot-only, and it becomes worth more if you ever add iterative deepening or a time limit. |
| Q3 | Iterative deepening (Idea B)? | **Not for speed.** Later, only if you want a time limit per bot turn. |

## 6. Plan (after your answers)

| # | Step | Done when |
|---|---|---|
| T0 | `tools/bench_bot.py` (or a test-only script): fixed seeds/positions; prints moves, seconds, value, chosen move | baseline recorded here |
| T1 | C1 (if Q1 = yes) | all tests green; same moves and values on the benchmark; time measured |
| T2 | TT in the bot (2.1-2.4) | same root value **and** move with TT on/off on the benchmark positions; game unchanged after the search; TT hits logged |
| T3 | Measure again; update `docs/architecture.md` | numbers in this plan |
