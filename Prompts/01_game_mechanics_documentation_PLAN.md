# PLAN: Implementation of `01_game_mechanics_documentation.md`

> Source prompt: `Prompts/01_game_mechanics_documentation.md`
> Target stack: Python 3.12, `pygame-ce==2.5.2`, `pytest` (already in `requirements.txt` / `venv`)
> Status: **implemented** (M0–M6) with the defaults from section 2. 112 tests.
> Deviations: unit data is `beastborn/data/units.json` (stdlib on Python 3.10+) instead of TOML. The UI presenter lives in `ui/interaction.py`, so it can be tested without pygame. Git commits were not made; that's left to you.

---

## 0. Current state of the repository (findings)

| Item | State | Consequence for the plan |
|---|---|---|
| `core/game.py` | `Beast` has `attack_min/attack_max/luck` (RNG-based), 6 biome terrains, no elevation, no energy. `BeastbornGame` calls `Board()` without params (crashes). `BoardPosition` validates `x` against rows and `y` against columns (inconsistent). 2-player setup puts both players on the same side (TL + BL). | Model contradicts the new spec (zero RNG). **Replace**, do not extend. |
| `core/ui.py` | Not valid Python (subclasses an `Enum` that has members, loose code at class level, undefined `pygame`, `TILE_SIZE`, `TERRAIN_COLORS`). | Delete; rewrite as a proper UI layer. |
| `core/unit_tests.py` | `from core import *` but `core/__init__.py` is empty → nothing is imported, test fails. | Move tests to `tests/`, rewrite. |
| `beastborn_demo.py` | Standalone pygame toy (real-time movement, random stats). | Keep as legacy or delete (decision D11). |
| Git | 2 commits; almost all files show as modified (likely CRLF/LF line-ending noise). | Add `.gitattributes`, commit a clean baseline before starting. |

---

## 1. Interpretation of the spec (what will be built)

1. Turn-based, grid, 2–4 human players in hot-seat on one PC. Each player owns 1 Boss + 4 random units (from the non-Boss roster).
2. Win: last player whose Boss is alive. A player whose Boss dies is eliminated.
3. Zero RNG in **combat**. RNG is used **only** for map generation and the roster draw, always via a seeded `random.Random(seed)` so every game is reproducible.
4. Every unit has its own energy pool. Energy is spent on moving and attacking; unspent energy carries over (capped at `MAX_EN`).
5. Strict three-layer architecture: **CE** (pure calculations) ← **GSM** (state + rules flow) ← **UI** (render + input). Dependencies point only to the left.

---

## 2. Decisions needed (proposed defaults — implementation will use these unless you change them)

Gaps and ambiguities found in the prompt. Every numeric default lives in one `RulesConfig` / unit data file, so changing it later is a one-line change.

| # | Question | Proposed default |
|---|---|---|
| D1 | Grid neighbourhood for movement and attack? | 4-directional (no diagonals). |
| D2 | Attack range? | Adjacent tile only (`range = 1`); stored per unit so ranged units can come later. |
| D3 | How many attacks per turn? | Unlimited while energy allows (`max_attacks_per_turn = None`). |
| D4 | Turn structure | Player turn → act with **any** own unit in **any** order, interleaving moves and attacks → `End Turn`. |
| D5 | Start-of-turn order | For each unit of the active player: 1) energy regen `EN = min(MAX_EN, EN + REG_EN)`, 2) status ticks (Venom), 3) resolve deaths. |
| D6 | Starting energy | `0` → on the first turn each unit has exactly `REG_EN`. |
| D7 | Downhill cost: prompt says 2 EN into normal, **5 EN into difficult** (more than the 3 EN on flat difficult). Intended? | Implement literally (configurable `EN_MD_DIFFICULT = 5`). Please confirm. |
| D8 | Multi-level step (elevation 0 → 2 in one step) | Allowed, cost `+2` per level (`0→2` on normal = 2 + 4 = 6). Map generator smooths terrain so adjacent tiles differ by ≤ 1 (flag). |
| D9 | Attacking from below | Optional mechanic as config flag `uphill_attack_penalty = False`. When on: `EM = -1 × levels below`. Attack from above: `EM = +2 × levels above`. |
| D10 | Rule RL_1 (block) | `allow_block = False` → damage is always `max(1, DMG)`. With `True`, `DMG <= 0` = blocked (0 damage, no on-hit effects). |
| D11 | Legacy code (`core/`, `beastborn_demo.py`) | Remove `core/` (kept in git history); keep `beastborn_demo.py` until the new UI runs, then delete. |
| D12 | Difficult terrain types | Two terrains to start: `GRASS` (normal) and `MUD` (difficult, DEF penalty 2). Old biomes (forest, snow…) dropped for now; terrain table makes adding them trivial. |
| D13 | Venom details | Per-unit params: `venom_damage = 1`, `venom_duration = 3` turns. Re-applying refreshes duration (no stacking). Ignores DEF. Can kill. |
| D14 | Acid | Implemented and tested in the CE, but no current unit uses it (prompt lists no Acid unit). Shred is applied **after** the hit's damage is calculated. |
| D15 | Blocking tiles | Units cannot pass through or end on occupied tiles (own or enemy). |
| D16 | Elimination | When a Boss dies, all of that player's remaining units are removed. |
| D17 | Map size | 12 × 12 by default (configurable). 20 × 20 makes Boss travel very slow. |
| D18 | Number formats | Integers only. No floats anywhere in rules. |

### Proposed placeholder unit stats (for balancing later)

| Unit | HP | ATK | EN_ATK | DEF | MAX_EN | REG_EN | Effect | Damage vs DEF 0 |
|---|---|---|---|---|---|---|---|---|
| Boss | 40 | 3 | 2 | 3 | 6 | 3 | — | 6 |
| Big Rat | 10 | 2 | 2 | 1 | 10 | 6 | Venom 1 × 3 turns | 4 |
| Peasant | 14 | 2 | 3 | 2 | 8 | 5 | — | 6 |

Boss "very limited movement" = `REG_EN 3` → one normal tile per turn, or hoard to 6 for three tiles.

---

## 3. Target architecture

```
Beastborn/
├─ beastborn/
│  ├─ __init__.py
│  ├─ domain/                 # pure data, no logic, no pygame
│  │  ├─ position.py          # Position(x, y), neighbours()
│  │  ├─ terrain.py           # TerrainType enum, Tile(terrain, elevation)
│  │  ├─ unit.py              # UnitStats (frozen), UnitState (hp, en, def, effects, pos, owner)
│  │  ├─ effects.py           # StatusEffect (kind, magnitude, remaining_turns)
│  │  ├─ board.py             # Board(width, height, tiles), occupancy helpers
│  │  └─ roster.py            # unit templates (Boss, Big Rat, Peasant) loaded from data
│  ├─ engine/                 # CALCULATION ENGINE (CE) – stateless
│  │  ├─ interface.py         # CalculationEngine (ABC)
│  │  ├─ results.py           # MoveCost, AttackResult, TickResult, EffectApplication
│  │  ├─ rules_config.py      # RulesConfig dataclass with all constants (EN_NT, EN_DT, ...)
│  │  └─ standard_engine.py   # StandardCalculationEngine – the initial implementation
│  ├─ game/                   # GAME STATE MACHINE (GSM)
│  │  ├─ state.py             # GameState (board, units, players, turn, phase)
│  │  ├─ phases.py            # Phase enum
│  │  ├─ commands.py          # MoveCommand, AttackCommand, EndTurnCommand
│  │  ├─ events.py            # UnitMoved, UnitAttacked, EffectTicked, UnitDied, PlayerEliminated, TurnStarted, GameOver
│  │  ├─ pathfinding.py       # Dijkstra over CE step costs → reachable tiles + paths
│  │  ├─ map_generator.py     # seeded random map + spawns
│  │  ├─ setup.py             # new_game(players, seed, config) -> GameStateMachine
│  │  ├─ view.py              # GameView: read-only snapshot for UI/Bot
│  │  └─ state_machine.py     # GameStateMachine
│  ├─ control/
│  │  └─ controller.py        # PlayerController ABC (Human now, Bot later)
│  └─ ui/
│     ├─ interface.py         # Frontend ABC, InputAdapter ABC, UI intents
│     └─ pygame_ui/
│        ├─ app.py            # main loop
│        ├─ renderer.py       # board, units, highlights, side panel, log
│        ├─ input_mouse_kb.py # pygame events → UI intents
│        └─ theme.py          # colors, sizes
├─ beastborn/data/units.json  # unit stats
├─ tests/
├─ main.py                    # entry point: python main.py
├─ pyproject.toml             # pytest config
└─ requirements.txt
```

**Dependency rule (enforced by a test):** `domain` ← `engine` ← `game` ← `control` / `ui`. `engine` and `game` never import `pygame`; `engine` never imports `game`.

### 3.1 Calculation Engine (CE) interface

The CE receives values, returns values, never mutates anything. Swapping the class changes every rule (costs, damage, effects).

```python
class CalculationEngine(ABC):
    # Movement
    @abstractmethod
    def step_cost(self, unit: UnitStats, src: Tile, dst: Tile) -> int: ...

    # Energy
    @abstractmethod
    def regenerate(self, unit: UnitStats, current_en: int) -> int: ...
    @abstractmethod
    def attack_cost(self, attacker: UnitStats) -> int: ...

    # Combat
    @abstractmethod
    def elevation_modifier(self, attacker_tile: Tile, defender_tile: Tile) -> int: ...
    @abstractmethod
    def current_defense(self, defender_def: int, defender_tile: Tile) -> int: ...
    @abstractmethod
    def resolve_attack(self, attacker: CombatantSnapshot, defender: CombatantSnapshot) -> AttackResult: ...
        # AttackResult: damage, blocked, breakdown (base, EM, current_def), effects_to_apply

    # Status effects
    @abstractmethod
    def on_hit_effects(self, attacker: UnitStats) -> list[EffectApplication]: ...
    @abstractmethod
    def tick_effects(self, effects: tuple[StatusEffect, ...], hp: int, defense: int) -> TickResult: ...
        # TickResult: hp_delta, def_delta, remaining_effects
```

`StandardCalculationEngine(config: RulesConfig)` implements the formulas:

- Step cost: base `EN_NT=2` / `EN_DT=3`; uphill `+EN_MU × levels`; downhill → `EN_MD_NORMAL=2` / `EN_MD_DIFFICULT=5`.
- `EM = (att_elev − def_elev) × 2` if above; `0` or `−1 × levels` if below (D9).
- `Current DEF = max(0, DEF − terrain_penalty)`.
- `DMG = ATK × EN_ATK + EM − Current DEF`, then RL_1 (D10).
- Venom tick, Acid shred.

`breakdown` in `AttackResult` lets the UI show an exact damage preview before the attack. That works because combat has no RNG.

### 3.2 Game State Machine (GSM)

Phases:

```
SETUP → TURN_START → AWAITING_COMMAND ⇄ (resolve Move / Attack) → TURN_END → TURN_START (next living player) …
                                                     └─ only one Boss left → GAME_OVER
```

Public API (used by the UI now and by the bot later, through the same path):

```python
class GameStateMachine:
    def __init__(self, state: GameState, engine: CalculationEngine): ...
    phase: Phase
    def view(self) -> GameView                                         # read-only snapshot
    def reachable_tiles(self, unit_id) -> dict[Position, PathInfo]      # cost + path
    def attack_targets(self, unit_id) -> dict[UnitId, AttackResult]     # preview
    def submit(self, cmd: Command) -> CommandResult                     # ok | error, events[]
```

- All validation is here: owner, active player, energy, occupancy, range, phase.
- All mutation is here: subtract energy, move, apply damage/effects, remove dead units, eliminate players, advance turn.
- It returns a list of **events**. The UI uses them for the log and animations.
- `pathfinding.py`: Dijkstra limited by the unit's current energy, using `engine.step_cost`. It reads state and changes nothing, but it isn't a game rule, so it stays out of the CE. The CE only says what one step costs.

### 3.3 UI layer

```python
class Frontend(ABC):
    def run(self, gsm: GameStateMachine) -> None: ...

class InputAdapter(ABC):           # raw device events → intents
    def poll(self) -> list[UIIntent]   # ClickTile(pos), EndTurn, Cancel, Quit
```

The UI only reads `GameView`, asks the GSM for previews, and sends `Command`s. Swapping pygame for console/web means a new `Frontend`. Swapping mouse for keyboard/gamepad means a new `InputAdapter`. The game code doesn't change.

**Pygame v1 (simple on purpose):**

- Grid: colour by terrain, darker/lighter shade + small number by elevation (0/1/2).
- Units: coloured circle per player + letter (B/R/P), HP and EN bars, effect icon (V).
- Side panel: active player, selected unit stats (HP, ATK, EN_ATK, DEF / current DEF, EN/MAX_EN, REG_EN, effects).
- Interaction: click own unit → reachable tiles highlighted with EN cost; enemies in range get a red outline with exact damage preview on hover. Click tile = move, click enemy = attack. `E`/button = End Turn, `Esc` = deselect.
- Event log (last ~8 lines). Game-over screen with the winner.

---

## 4. Map generation (seeded)

1. Grid `W×H` (default 12×12), all `GRASS`, elevation 0.
2. **Hills:** N hill seeds (scaled to map area). Each raises a radius-1 area to 1 and the centre to 2. Clamp to `max_elevation = 2`. Optional smoothing pass so neighbours differ by ≤ 1 (D8).
3. **Mud:** M random blobs of `MUD` (random walk, 3–6 tiles).
4. **Spawn zones:** 3×3 area at each used corner forced to flat `GRASS` (fairness, no free height advantage).
5. **Corner order:** P1 top-left, P2 bottom-right, P3 top-right, P4 bottom-left. With 2 players they start opposite each other.
6. **Units:** Boss on the corner tile; 4 units drawn from the roster without the Boss (seeded), placed on the closest free tiles around the Boss.
7. Same `seed` → identical map and roster (tested).

---

## 5. Implementation milestones

Each milestone ends green on `pytest` and as its own commit.

### M0 — Clean-up and scaffolding

- `.gitattributes` (`* text=auto`), commit current baseline.
- Remove `core/` (D11), create the package skeleton above, `pyproject.toml` with `[tool.pytest.ini_options] testpaths = ["tests"]`.
- `main.py` placeholder. Update `README.md` (how to run and test).

### M1 — Domain model + config

- `Position`, `TerrainType` (with `is_difficult`, `def_penalty`), `Tile`, `UnitStats` (frozen dataclass), `UnitState`, `StatusEffect`, `Board`.
- `data/units.json` + loader → roster. `RulesConfig` with every constant from the spec.
- Tests: data loading, position bounds, neighbours on edges and corners.

### M2 — Calculation Engine

- `interface.py`, `results.py`, `StandardCalculationEngine`.
- Table-driven tests (`pytest.mark.parametrize`) covering every row in the spec:
  - normal 2, difficult 3, uphill normal 4, uphill mud 5, downhill normal 2, downhill mud 5, two levels up.
  - EM: +2 per level above, 0 on level, below with flag on and off.
  - Current DEF: 3 on mud → 1; 1 on mud → 0 (no negative).
  - **Spec example: ATK 2, EN_ATK 2, +1 level, DEF 4 on mud → DMG 4.**
  - RL_1: DMG ≤ 0 → 1 (block off); → 0 and no effects (block on).
  - Venom: tick, duration countdown, refresh on re-apply, can kill. Acid: −1 DEF per hit, min 0.
- Test: a dummy `AlternativeEngine` (e.g. all moves cost 1) plugged into the GSM changes behaviour without any GSM edit. This proves the CE is isolated.

### M3 — Map generator + game setup

- `map_generator.py`, `setup.new_game(num_players, seed, config, engine)`.
- Tests: 2/3/4 players, invalid counts rejected, elevations ⊂ {0,1,2}, spawn zones flat, 1 Boss + 4 units per player, no overlapping units, determinism by seed.

### M4 — Game State Machine

- Phases, commands, events, pathfinding, `submit()`, victory.
- Tests (scripted scenarios on small hand-built boards):
  - Move succeeds and costs the right energy, along the cheapest path. Insufficient energy → rejected with no state change.
  - Cannot move other player's unit / out of turn / through occupied tiles.
  - Attack: out of range, not enough energy, friendly target → rejected; valid → HP and energy updated, events emitted.
  - Energy carries over and caps at `MAX_EN` (hoarding).
  - Start-of-turn order: regen → venom → deaths (D5).
  - Boss killed (by attack or by venom) → player eliminated, units removed, turn skips them. Last Boss → `GAME_OVER`.
- Architecture test: scan imports; `engine` and `game` never import `pygame` or `beastborn.ui`.

### M5 — UI abstraction + pygame frontend

- `ui/interface.py`, `pygame_ui/*`, `main.py` (`--players 2..4 --seed N --size 12`).
- Headless smoke test: `SDL_VIDEODRIVER=dummy`, create frontend, render one frame, inject a few intents.
- Manual play-test checklist (section 6).

### M6 — Polish and documentation

- README: rules summary, controls, how to swap the CE or the UI.
- Short `docs/architecture.md` with the layer diagram.
- Delete `beastborn_demo.py` (D11).

### Later (out of scope for this prompt, but already supported by the design)

- Bot: `BotController` builds `Command`s from `GameView`, using `reachable_tiles` and `attack_targets` previews.
- More units / effects: new rows in `units.json` + new effect kinds in the CE.
- Other frontends (console, web) via `Frontend`.
- Save/replay: command log + seed fully reproduces a game, because combat has no RNG.

---

## 6. Definition of Done

- [ ] `python main.py --players 4 --seed 42` opens a pygame window with a random 12×12 map, 4 corners, 4 Bosses, 16 units.
- [ ] Each player can select units, see reachable tiles with costs, move, see exact damage previews, attack and end the turn.
- [ ] Energy regen, hoarding, venom and elevation/terrain modifiers work as in the spec. The spec's damage example gives 4.
- [ ] Killing a Boss eliminates its player; the last player standing wins.
- [ ] `pytest` passes. CE has full branch coverage of the formulas.
- [ ] Swapping `StandardCalculationEngine` for another implementation needs **no** change in `game/` or `ui/` (proven by a test).
- [ ] `engine/` and `game/` don't import `pygame` (proven by a test).

---

## 7. Risks and notes

- **Balance:** placeholder stats will need tuning after the first play-tests. Keeping them in `units.json` makes this cheap.
- **Downhill-into-mud cost (D7)** is higher than flat mud. It looks intentional ("penalty") but is unusual, so it's worth confirming.
- **Hot-seat only:** no hidden information, so one shared screen is fine for v1.
- **Windows venv:** run tests with `venv\Scripts\python -m pytest`.
