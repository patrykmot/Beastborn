# PLAN: Balance report (desktop app)

> Source prompt: `Prompts/04_report.md` (updated 2026-10-11 11:27)
> Builds on: `03_flask_migration_PLAN.md` (implemented). The game rules, engine and roster are reused **as they are**.
> Status: **implemented** (R1–R4) on 2026-10-11 with the defaults from section 2. 207 tests pass (189 before + 15 in `tests/test_reports.py` + 3 architecture checks). Checked on Python 3.12 / Tk 8.6 (Linux, virtual display) with screenshots at 1280×880 and at the 1000×700 minimum size. Nothing was committed to git.
> Deviations: `report.py` is ~1000 lines, not ~500 (the GUI took more lines than estimated). In tab 2 the Distance box sits next to the step buttons, so the pickers fit at 1000 px wide. The "Next hit" line reads the next hit from the precomputed fight instead of calling `preview_attack` (the same numbers, because combat has no RNG). Space is not a step key (it would also press the focused button); the step keys are ← → Home End. F5 also refreshes. Three GUI tests instead of one.
> Still open: R0 (check `import tkinter` on your Windows Python) and Q1.
> Target stack: Python 3.12 standard library only (`tkinter` + `ttk`). **No new dependency.**
> Location: **one file**, `beastborn/reports/report.py`. Start with `python -m beastborn.reports.report` (or Run in PyCharm).
> **No changes to** `domain`, `engine`, `game`, `control`, `ui`, `units.json` or the web client.
>
> **Revision 2 (after the prompt update):**
> - Tab 1 gets a control bar on top: attacker terrain + height, defender terrain + height (plus ranged distance). They apply to all pairs.
> - One **Refresh** button above the tabs reloads `units.json` and recomputes both tabs (it replaces the per-tab Reload buttons).
> - All code goes in one file, `beastborn/reports/report.py` (no `gui/` subpackage, no `fight.py`/`matrix.py`, no `__main__.py`).
> - Tab 2 is described as the detailed "unit vs unit with stats" view.
> - The prompt line "I have " under tab 1 is unfinished. See open question Q1.

---

## 0. Starting point (what the report reuses)

| Existing piece | What the report uses it for |
|---|---|
| `load_roster()` + `beastborn/data/units.json` | The list of units. Refresh calls it again, so edits to the JSON show up without restarting. A new unit in the JSON appears in the report with no code change. |
| `custom_game(board, placements)` | A 1-row hand-made board with the two units on tiles with the chosen terrain and height. |
| `GameStateMachine.submit(AttackCommand / EndTurnCommand)` | Runs the **real** turn flow: regen at turn start, energy per hit, Venom ticks, Acid, death. No formula is copied into the report. |
| `GameStateMachine.preview_attack()` | "Next hit" line in tab 2. |
| `AttackResult.formula()` | The calculation text of every hit, e.g. `7 x1.5 height x0.8 range - 2 = 6.4 -> 6`. |
| Events (`TurnStarted`, `EnergyRegenerated`, `UnitAttacked`, `EffectTicked`, `UnitDied`) | Turned into report steps. |
| `GameView` / `UnitView` | Unit stats after each step (HP, EN, DEF, effective DEF, effects). |
| `StandardCalculationEngine` / `RulesConfig`, `DEFAULT_MAX_ELEVATION` | Default rules and the height range (0–2). |

**Prototype run (2026-10-11):** a 40-line script drove `custom_game` + `submit` for all 16 pairs and produced exact step-by-step HP traces (numbers in section 7). The approach works without touching the game code.

**Uncommitted now:** `Prompts/04_report.md`, `Prompts/04_report_PLAN.md` (new) and `beastborn/constance.py` (`GAME_VERSION` 0.2 → 0.3). Commit them first so this work has a clean diff.

---

## 1. Interpretation

1. A small **desktop window** written with `tkinter`/`ttk` (part of Python, nothing to install). On top there is a **Refresh** button, then two tabs.
2. **Tab 1 – Attack report:** every unit attacks every unit (all pairs, including the same type, e.g. Boss → Boss; 4 × 4 = 16 today). A control bar on top sets the **attacker's terrain and height** and the **defender's terrain and height** for all pairs. For each pair you see the target's HP after **every step** until it dies, plus a summary (hits, rounds).
3. **Tab 2 – Attack simulation:** the detailed "unit vs unit with stats" view. Pick unit 1 (attacker) and unit 2 (target), terrain and height for each, then step through the fight and watch both units' stats change.
4. **Refresh** reloads `units.json` and recomputes both tabs, keeping the current selections where the units still exist.
5. "Attack" means **one-sided**: the attacker stands still and hits as often as its energy allows each turn; the target does nothing (no counter-attack, no moving). That answers "how does the attack mechanic work".
6. Combat has no RNG, so each pair has exactly one result. No averaging.

---

## 2. Decisions (proposed defaults)

The implementation uses these unless you change them.

| # | Question | Default | Alternative |
|---|---|---|---|
| R1 | GUI toolkit | **`tkinter` + `ttk`** (`Notebook` for tabs, `Treeview` for tables, `Canvas` for the HP chart). Standard library. | PySide6 / matplotlib need a new library. Only if you approve it. |
| R2 | How a fight is simulated | **Drive the real `GameStateMachine`** (`custom_game` + `AttackCommand`/`EndTurnCommand`), as the prompt asks ("reuse existing game mechanic code"). Turn order, regen, Venom timing and death are exactly as in a real game. | Call `engine.resolve_attack` in a loop. That copies turn-flow rules into the report, so they could drift apart. |
| R3 | What one "step" is | **Every HP change of the target:** each hit and each Venom tick, grouped by round. Turn start (energy regen) is a grey row so you can see why there are 1, 2 or 3 hits in a round. | One row per round (shorter, but hides the per-hit detail). |
| R4 | Fight setup | Attacker = player 1, moves first. Both units start with 0 EN and regenerate at the start of their own turn (real rules). Attacker hits while EN ≥ `en_atk`, then ends the turn; the target only ends its turn. | Start with full energy (`max_en`) to show "burst" damage. |
| R5 | Tab 1 control bar | **Attacker:** terrain [grass/mud], height [0/1/2]. **Defender:** terrain [grass/mud], height [0/1/2]. **Ranged distance:** [best/1/2/3/4] (best = highest RP, ties → shortest, so 2 for the Archer). Melee always attacks from 1 tile. Defaults: grass, 0, grass, 0, best. Any change recomputes all pairs at once. | Without the ranged distance box (always "best"). |
| R6 | Same-type pairs | **Included** (Boss → Boss, Rat → Rat…). | Hide the diagonal. |
| R7 | Who is a Boss | No special handling: killing a Boss ends the game (that is just the end of the fight). Non-Boss deaths end it too. | – |
| R8 | Endless fights | Stop after **`MAX_ROUNDS = 100`** rounds and mark "alive after 100 rounds". Today this cannot happen (every hit deals ≥ 1), but it can with `RulesConfig(allow_block=True)`. | – |
| R9 | Tab 2 terrain | Each unit: **terrain** grass/mud and **height** 0…`DEFAULT_MAX_ELEVATION` (2), plus **distance** 1…attacker range (melee fixed to 1). The tiles in between are flat grass (there is no line-of-sight rule). | Free map editor. Not needed. |
| R10 | Stepping in tab 2 | The whole fight is **computed at once**, with a snapshot of both units after every step. Buttons move through it: Reset, ◀ Back, Step ▶, Run to end. So stepping back is free. | Live stepping on the GSM (no Back button). |
| R11 | Duel (target hits back) | **Not in this version** (the prompt asks for "attack unit 1 vs unit 2"). | Both units attack on their turn; the report shows who wins. |
| R12 | Refresh | **One button above the tabs.** It calls `load_roster()` again and recomputes tab 1 and tab 2. If the JSON is broken, it shows an error dialog and keeps the old data. | – |
| R13 | Summary per pair | Rounds to kill, hits, damage from hits, damage from Venom, overkill (damage past 0 HP). | Add "EN spent" or "% of HP per round". |
| R14 | Export | **None** (not asked). | CSV with the stdlib `csv` module. |
| R15 | One file | **Everything in `beastborn/reports/report.py`** (prompt: "all the code in one file only"). The few report-only constants (window size, `MAX_ROUNDS`, colours) sit at the top of that file, not in `constance.py`. No `__init__.py` is needed: Python treats `reports/` as a namespace package. | Constants in `constance.py` (project rule for the game code), at the cost of a second edited file. |
| R16 | How to start | `python -m beastborn.reports.report` from the project root. Running the file directly (PyCharm "Run", or `python beastborn/reports/report.py`) also works: the `__main__` block adds the project root to `sys.path` first. | Only `-m`. |

### Open questions

- **Q1:** under tab 1 the prompt says "I have " and stops. Is something missing there, for example a ranged-distance choice (included as R5), a units filter, or a summary table? Until you answer, the plan builds R5 as written.

---

## 3. Layout of `beastborn/reports/report.py`

One file, about 450–550 lines, in this order:

```
"""Beastborn balance report: python -m beastborn.reports.report"""
imports              stdlib (dataclasses, enum, tkinter, tkinter.ttk, tkinter.messagebox, sys, pathlib)
                     + beastborn.domain / engine / game / constance
constants            WINDOW_SIZE, MAX_ROUNDS, TERRAINS, HEIGHTS, chart and heat-map colours

# ---- simulation (no tkinter used here)
Side, Scenario       what is simulated (unit, terrain, height; distance)
StepKind, Step       one row of the report
Fight                all steps + summary
simulate()           runs one fight on the real GameStateMachine
legal_distances()    1 for melee, 1..range for ranged (via engine.in_attack_range)
best_distance()      highest RP, ties -> shortest
attack_matrix()      all pairs for given conditions

# ---- widgets
HpChart(Canvas)      target HP per step, optional marker on the current step
StatsPanel(Frame)    labels + HP/EN bars for one UnitView

# ---- tabs
ReportTab(Frame)     tab 1
SimulationTab(Frame) tab 2

# ---- window
ReportApp(Tk)        Refresh button + Notebook; refresh() reloads the roster and calls both tabs
main()
if __name__ == "__main__": add project root to sys.path; main()
```

`tkinter` is imported at the top of the file. The simulation part only uses game classes, so its tests run anywhere tkinter is installed (it is part of the standard Windows Python).

---

## 4. Simulation part

### 4.1 Data

```python
@dataclass(frozen=True)
class Side:
    stats: UnitStats
    terrain: TerrainType = TerrainType.GRASS
    elevation: int = 0

@dataclass(frozen=True)
class Scenario:
    attacker: Side
    target: Side
    distance: int                       # 1 for melee; 1..range for ranged

@dataclass(frozen=True)
class Step:
    round: int
    kind: StepKind                      # START | REGEN | HIT | VENOM | DEATH
    text: str                           # "hit", "venom", "turn start +3 EN"
    calculation: str                    # AttackResult.formula() for hits, "" otherwise
    target_hp_before: int
    target_hp_after: int                # 0 when dead
    attacker: UnitView                  # snapshot after the step
    target: UnitView | None             # None once dead
    result: AttackResult | None         # only for HIT

@dataclass(frozen=True)
class Fight:
    scenario: Scenario
    steps: tuple[Step, ...]
    killed: bool
    rounds: int
    hits: int
    hit_damage: int
    venom_damage: int
    overkill: int
```

### 4.2 Algorithm (sketch)

```python
def simulate(scenario, engine=None, max_rounds=MAX_ROUNDS) -> Fight:
    if scenario.distance not in legal_distances(scenario.attacker.stats, engine):
        raise ValueError(...)
    row = [Tile(a.terrain, a.elevation)] + [Tile()] * (distance - 1) + [Tile(t.terrain, t.elevation)]
    gsm = custom_game(Board([row]), [(0, a.stats, (0, 0)), (1, t.stats, (distance, 0))],
                      engine=engine, start=False)
    events = gsm.start()                                # keeps the round-1 regen event
    steps = [start_step]
    while True:
        steps += to_steps(events, gsm.view())           # REGEN / HIT / VENOM / DEATH rows
        if target dead or gsm.view().round > max_rounds:
            break
        if gsm.active_player == 0:
            r = gsm.submit(AttackCommand(1, 2))
            if not r.ok:                                # not enough EN: end the turn
                r = gsm.submit(EndTurnCommand())
        else:
            r = gsm.submit(EndTurnCommand())
        events = r.events
    return Fight(...)
```

- `custom_game` does not check map size, so a 1×2…1×5 board is fine (confirmed in the prototype).
- A rejected attack is the normal "out of energy" case. The report never sends a move.
- `attack_matrix(roster, attacker_tile, defender_tile, ranged_distance)` loops over all pairs. 16 fights take a few milliseconds, so every control change simply recomputes everything.

---

## 5. Window and Tab 1 – Attack report

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ [⟳ Refresh]  units.json loaded 11:42:05 · 4 units                                     │
├─[ Attack report ]─[ Attack simulation ]──────────────────────────────────────────────┤
│ Attacker: terrain [grass ▾] height [0 ▾]   Defender: terrain [mud ▾] height [0 ▾]     │
│ Ranged distance [best ▾]          (melee always attacks from 1 tile)                  │
│                                                                                       │
│  Rounds to kill (hits)     target →  Boss      Big Rat   Peasant   Archer             │
│  attacker ↓  Boss                    …         …         …         …                  │
│              Big Rat                 …                                                │
│              Peasant                 …                                                │
│              Archer                  …                                                │
│                        (cell colour: green = dies fast … red = dies slowly)           │
├───────────────────────────────────────────────┬───────────────────────────────────────┤
│ ▸ Boss → Boss       10 rounds, 10 hits        │  HP of Big Rat → Boss                 │
│ ▾ Big Rat → Boss    10 rounds, 30 hits        │  40 ┤▀▀▄▄                             │
│     R1  turn start  +6 EN            40       │     │    ▀▀▄▄                         │
│     R1  hit  5 - 4 = 1          40 → 39       │     │        ▀▀▄▄                     │
│     R1  hit  5 - 4 = 1          39 → 38       │   0 ┤            ▀▀ dead              │
│     R1  venom 1                 37 → 36       │      step 0 ……………… 40                 │
│     …                                         │  hits 30 · venom 10 · overkill 0      │
└───────────────────────────────────────────────┴───────────────────────────────────────┘
```

- **Refresh** (above the tabs): reload `units.json`, rebuild tab 1, recompute tab 2 with the same choices. The label shows when it last loaded.
- **Control bar** (`ttk.Combobox`, read-only): attacker terrain/height, defender terrain/height, ranged distance. A change recomputes all pairs.
- **Matrix**: rows = attacker, columns = target, cell = `rounds (hits)`, background on a green→red scale by rounds. Clicking a cell opens that pair in the tree below and draws its chart.
- **All-pairs tree** (`ttk.Treeview`, one parent row per pair, child rows = steps). Columns: Round, Event, Calculation, HP before → after, attacker EN. "Expand all" / "Collapse all" buttons. This is the full "every combination, every step" report.
- **HP chart** (`Canvas`): target HP (y) per step (x), dots on hits, another colour for Venom, dotted lines at round borders.

---

## 6. Tab 2 – Attack simulation (unit vs unit with stats)

```
├─[ Attack report ]─[ Attack simulation ]──────────────────────────────────────────────┤
│ Unit 1 (attacker)                              Unit 2 (target)                        │
│ Unit [Archer ▾] Terrain [grass ▾] Height [1 ▾] Unit [Boss ▾] Terrain [mud ▾] Height [0▾]│
│ Distance [3 ▾]  (1–4)                                                                 │
│ [⟲ Reset] [◀ Back] [Step ▶] [Run to end ⏭]          Round 2 · step 5 / 31            │
├──────────────────────────────┬──────────────────────────────┬────────────────────────┤
│ Archer (attacker)             │ Boss (target)                │ HP chart (as tab 1)    │
│ HP   14 / 14  ██████████      │ HP   27 / 40  ███████░░░     │  with a marker on the  │
│ EN    2 / 8   (+5/turn, 3/hit)│ EN    3 / 6                  │  current step          │
│ ATK   7   Range 4  Move +1    │ DEF   4  → effective 2 (mud) │                        │
│ DEF   2  → effective 2        │ Effects: –                   │                        │
│ Tile  grass, height 1         │ Tile  mud, height 0          │                        │
│ Next hit: 7 x1.5 height x0.8 range - 2 = 6.4 -> 6                                     │
├───────────────────────────────────────────────────────────────────────────────────────┤
│ Step log (current row highlighted): Round | Event | Calculation | Target HP | Att. EN │
└───────────────────────────────────────────────────────────────────────────────────────┘
```

- Changing any picker recomputes the fight and goes back to step 0.
- **Distance** lists only legal values: `1` for melee, `1…range` for ranged.
- **Stat panels** show the `UnitView` snapshot of the current step: HP bar, EN with regen and attack cost, ATK, range, move penalty, base DEF and effective DEF on its terrain (shows Acid and mud), active effects with strength and rounds left, tile.
- **Next hit** uses `preview_attack` on the current snapshot with `AttackResult.formula()`, so height and range factors are visible before pressing Step.
- Keyboard: → / Space = Step, ← = Back, End = Run to end, Home = Reset.

---

## 7. Expected numbers today (test oracle)

From the prototype, with tab 1 defaults (both on grass, height 0, Archer at 2 tiles). Format `rounds (hits)`:

| attacker ↓ / target → | Boss (40 HP, DEF 4) | Big Rat (12, DEF 1) | Peasant (16, DEF 2) | Archer (14, DEF 2) |
|---|---|---|---|---|
| **Boss** (ATK 8, 1 hit/round) | 10 (10) | 2 (2) | 3 (3) | 3 (3) |
| **Big Rat** (ATK 5, 3 hits/round, Venom 1) | 10 (30) | 1 (3) | 2 (5) | 2 (5) |
| **Peasant** (ATK 6) | 12 (20) | 2 (3) | 3 (4) | 3 (4) |
| **Archer** (ATK 7, range 4) | 9 (14) | 2 (2) | 3 (4) | 2 (3) |

Big Rat → Boss trace: 3 hits of `5 - 4 = 1` per round, then Venom 1 at the start of the Boss's turn. 40 HP → 0 in round 10, with Venom as the last tick.

What the report will show at once (only observations, no changes in this plan): a Boss needs 9–12 rounds to be killed by one unit; a Big Rat does only 1 damage per hit to a Boss, so much of its damage there is Venom; the Archer is the fastest Boss killer from 2 tiles.

---

## 8. Tests (`tests/test_reports.py`)

Tests stay in `tests/` like the rest of the project; the app itself is one file.

| Test | Checks |
|---|---|
| `test_melee_fight_hand_calculated` | SOLDIER → SOLDIER (conftest): 3 damage per hit, HP 10 → 7 → 4 → 1 → 0, rounds and hits as calculated by hand. |
| `test_regen_limits_hits_per_round` | Boss (reg 3, cost 3): 1 hit per round; Big Rat: 3 hits in round 1. |
| `test_venom_ticks_at_target_turn_start` | VENOM rows come after the attacker's hits of the same round, amount `max(1, ATK // 4)`. |
| `test_acid_lowers_target_def` | A unit with Acid: target `UnitView.defense` goes down step by step, never below 0. |
| `test_terrain_and_height_change_damage` | Target on mud / attacker 1 level up: the step's calculation equals the engine's `formula()`. |
| `test_matrix_uses_tab1_conditions` | `attack_matrix` with defender on mud gives more damage per hit than on grass for every pair (every unit has DEF ≥ 1, and mud halves it, rounded down). |
| `test_distances` | `legal_distances`: melee → [1], Archer → [1, 2, 3, 4]; `best_distance`: Archer → 2. Illegal distance → `ValueError`. |
| `test_round_cap` | `RulesConfig(allow_block=True)` + weak attacker → `killed=False`, `rounds == MAX_ROUNDS`. |
| `test_matrix_has_every_pair` | `len(attack_matrix(...)) == len(roster) ** 2`, every target dies. |
| `test_matrix_matches_current_balance` | The section 7 table on the real roster (update it when you rebalance). |
| `test_gui_smoke` | Builds `ReportApp`, changes a tab-1 combobox, presses Refresh, Step/Back/Run in tab 2, destroys it. `pytest.skip` when there is no display. |

`tests/test_architecture.py` gets:
- `"reports": ("pygame", *WEB, "beastborn.ui", "beastborn.control")` in `FORBIDDEN` (the report uses only domain/engine/game);
- `"beastborn.reports"` added to every existing layer's list (nothing depends on the report);
- `test_tkinter_only_in_reports`: `tkinter` is imported only in `beastborn/reports/report.py`.

---

## 9. Milestones

| # | Work | Done when |
|---|---|---|
| R0 | **Check:** `venv\Scripts\python -c "import tkinter; tkinter._test()"` shows a small window. Commit the open changes. | Window appears. If not: Python 3.12 installer → Modify → tick "tcl/tk and IDLE" (no pip package needed). |
| R1 | Simulation part of `report.py` + logic tests. | Tests green; the section 7 table is reproduced. |
| R2 | Window, Refresh button, Tab 1 (control bar, matrix, tree, chart). | All 16 pairs shown; terrain/height/distance changes and Refresh work. |
| R3 | Tab 2. | Pickers, stepping, stat panels, Next-hit line, step log, chart marker. |
| R4 | Architecture tests, GUI smoke test, docs, manual checklist. | `python -m pytest` green; checklist passes. |

---

## 10. Docs

- `README.md`: new section **"Balance report"**: `python -m beastborn.reports.report`, one line per tab, "edit `beastborn/data/units.json` and press Refresh". Add `reports/report.py` to the project layout.
- `docs/architecture.md`: one paragraph "Balance report (`beastborn/reports/report.py`)": top layer, drives the real GSM, the only place that imports `tkinter`.

---

## 11. Manual checklist (R4)

1. `python -m beastborn.reports.report` opens "Beastborn v0.3 – Balance report" with Refresh on top and two tabs.
2. Tab 1 with defaults: the matrix matches section 7, coloured green→red.
3. Click "Big Rat → Boss": the tree opens that pair (30 hits + 10 Venom rows), the chart goes from 40 to 0.
4. Set Defender terrain to mud: damage per hit goes up in every pair (e.g. Boss → Boss `8 - 2 = 6`, Boss → Big Rat `8 - 0 = 8`).
5. Set Attacker height 1, Defender height 0: calculations show `x1.5 height`. Attacker height 0, Defender height 2: `x0 height` hits deal the minimum 1.
6. Ranged distance 1: the Archer row gets slower (×0.5). Back to "best".
7. Change the Big Rat's `atk` in `units.json` to 6, press **Refresh**: tab 1 and tab 2 change. Undo the change and Refresh again.
8. Break `units.json` (remove a comma), press Refresh: error dialog, old data stays. Fix it.
9. Tab 2: Archer height 1 vs Boss on mud height 0, distance 3. Next-hit line: `7 x1.5 height x0.8 range - 2 = 6.4 -> 6`, effective DEF 2.
10. Step ▶ several times: HP, EN and the log row move together; ◀ Back goes back; Run to end stops at the death; Reset returns to step 0.
11. Peasant as attacker: distance offers only 1. Big Rat as attacker: Venom shows in the target's effects with rounds counting down.
12. Resize the window: tables and chart scale and nothing overlaps.

---

## 12. Definition of Done

- `python -m beastborn.reports.report` works with the existing `venv` (no `pip install`).
- `python -m pytest` is green (old tests unchanged + new ones).
- `requirements.txt` and `pyproject.toml` are **unchanged**.
- New files: only `beastborn/reports/report.py` and `tests/test_reports.py`. Edited: only `tests/test_architecture.py`, `README.md`, `docs/architecture.md` and this plan.
- No change in `domain`, `engine`, `game`, `control`, `ui`, `constance.py`, `units.json`.

---

## 13. Risks and notes

- **tkinter missing on Windows:** only if Python was installed without "tcl/tk". R0 checks it first. There is no other GUI option without a new library, so the fix is the installer checkbox, not pip.
- **One file of ~500 lines** is fine to read if it keeps the section order from section 3. If it grows past ~800 lines (e.g. after adding duels), splitting the simulation part out is the natural next step.
- **PythonAnywhere:** the deploy pulls the whole repo, so `report.py` lands on the server too. That is harmless: the web app never imports it.
- **GUI smoke test** needs a display. It runs on your PC and is skipped on a headless machine.
- **One-sided fights** measure "how fast does A kill B", not "who wins A vs B". The duel option (R11) answers the second question.
- **Positioning and movement are ignored** (Archer kiting, the cost of walking to a target). The report measures the attack mechanic only, as the prompt asks.
