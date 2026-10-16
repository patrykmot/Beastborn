# Architecture

```
 Browser (jQuery + Bootstrap)          Flask    beastborn/ui/web                      unchanged core
┌───────────────────────────┐ intents ┌───────────────────────────────┐
│ static/index.html          │────────▶│ app.py      routes             │
│ static/js/beastborn.js     │         │ intents.py  JSON → UIIntent    │
│  render(state)             │◀────────│ serializers Interaction → JSON │
│  hover previews (local)    │  state  │ sessions.py game id → session  │
└───────────────────────────┘         └──────────────┬────────────────┘
                                                      │ one per game
                                             ┌────────▼─────────┐  Command   ┌───────────────────────┐
                                             │ Interaction       │──────────▶│ GameStateMachine (GSM) │
                                             │ (ui presenter)    │◀─ events ─│  state, turn flow,     │
                                             └──────────────────┘  GameView │  validation            │
                       PlayerController (bot) ── Command ─────────────────▶ └──────────┬────────────┘
                                                                                       │ values in / values out
                                                                            ┌──────────▼────────────┐
                                                                            │ CalculationEngine (CE) │
                                                                            │  stateless formulas    │
                                                                            └───────────────────────┘
```

Dependencies only point down: `domain` ← `engine` ← `game` ← `control` / `ui`.
`tests/test_architecture.py` fails if a layer imports something it must not. For example, Flask, Werkzeug and pydantic are only allowed in `ui/web`.

All constants (default rule numbers, map and session limits, paths, asset URLs) live in `beastborn/constant.py`.

## Calculation Engine (`beastborn/engine`)

- `CalculationEngine` (ABC) is the single interface for every rule calculation:
  - `step_cost`
  - `initial_energy`, `regenerate`, `attack_cost`, `in_attack_range`
  - `elevation_modifier`, `current_defense`, `range_multiplier`, `resolve_attack` (the rules in [game_mechanics.md](game_mechanics.md))
  - `tick_effects`
  - `score(state, player)`: how good a `GameState` is for a player (enemy HP lost, Boss HP ×10, killed enemies count with all their HP lost, + 5 per level of height of own units; numbers in `constant.py`). For bots; the browser shows it for the active player.
- It only computes. It gets snapshots (`CombatantSnapshot`, `Tile`, `UnitStats`) and returns results (`AttackResult`, `TickResult`). It never changes game state.
- `AttackResult` keeps every step of the calculation (momentum, effective RP, effective DEF, exact fractions). `formula()` turns it into text such as `8 x1.3 height x0.8 range - 2 = 6.32 -> 6`.
- `StandardCalculationEngine` implements the design document. It reads all its numbers straight from `beastborn/constant.py`.

**Change the rules:**

Tweak numbers: edit `beastborn/constant.py` (e.g. `ELEVATION_COEF`, `RANGE_DISSIPATION`, `ALLOW_BLOCK`).

Write a new rule set:

```python
from beastborn.engine import StandardCalculationEngine
from beastborn.game import new_game

class MyEngine(StandardCalculationEngine):
    def step_cost(self, unit, src, dst):
        return 1
gsm = new_game(2, engine=MyEngine())
```

Nothing in `game/` or `ui/` changes. `test_swapping_calculation_engine_changes_rules_without_touching_gsm` checks this.

## Game State Machine (`beastborn/game`)

- Phases: `SETUP → AWAITING_COMMAND ⇄ … → GAME_OVER`.
- `gsm.start()` begins round 1 and the first unit's turn. `new_game()` calls it for you.
- Turn flow (the GSM picks the unit, players cannot):
  - A round gives every unit on the board one turn. `gsm.active_unit` is the unit whose turn it is,
    `gsm.active_player` its owner. Only the active unit can be used in commands and queries.
  - Players take turns in index order, each round starting with the first player; a player's units go in id order.
    A unit that cannot move or attack now (checked with the energy it will have after its regeneration) is put off
    until no unit that can act is left in the round. Players without units (Boss killed) are skipped.
  - Start of a unit's turn: 1. energy regeneration, 2. status ticks, 3. death.
  - The unit moves and attacks (several commands) until it cannot move one tile nor hit anything: then the GSM
    ends its turn by itself and the next unit's turn starts in the same `CommandResult`. `EndTurnCommand` ends it
    earlier (the unit waits).
  - When every unit had its turn, the next round starts.
  - Events: `RoundStarted`, `TurnStarted(player, round, unit_id)`, `TurnEnded(player, unit_id, idle)`
    (`idle` = it neither moved nor attacked).
- Queries:
  - `view()` returns a read-only `GameView` (with `active_unit`).
  - `reachable_tiles(unit_id)` returns cost and path for every tile the active unit can reach.
  - `attack_targets(unit_id)` lists enemies the active unit can attack now, from where it stands.
  - `attack_plans(unit_id)` lists every enemy one `AttackCommand` can hit, including those it first has to move to,
    as `AttackPlan`s (tile it attacks from, the move, the exact result). `plan_attack(a, t, move_to=None)` returns one.
  - `preview_attack(a, t)` returns the exact result, because combat has no RNG.
  - `score(player)` returns the CE score of the current board for that player.
  - `find_all_possible_actions()` returns every way the active unit can play its **whole turn**, one command per way
    (for bots: one command = one unit turn, the next unit acts after it). A turn is: attacks (each may move first, as
    `AttackCommand` with an explicit `move_to`), then maybe one `MoveCommand`, then `EndTurnCommand` if the unit could
    still act (otherwise the GSM ends the turn by itself). One step is returned as that command, more as a `WrapperCommand`.
    Every distinct command sequence is listed (also ones ending on the same board). It is read only: every attack is
    tried with `submit` and undone with `rollback`, so the list is exactly what `submit` accepts. The list is cached
    until the state changes. Typical: ~1 ms, crowded fights up to ~65 ms and several hundred actions.
- Commands: every command is a `Command` (frozen dataclass) with a `kind` (`CommandType`: `MOVE`, `ATTACK`,
  `END_TURN`, `WRAPPER`); `submit` dispatches on the kind and returns a `CommandResult(ok, events, error)`.
  - A rejected command never changes state (except a `WrapperCommand`, see below).
  - `WrapperCommand(commands)`: several commands of one unit's turn, run in order. Must not be empty, nested, or have
    `EndTurnCommand` before its end. If a wrapped command is rejected (or the unit's turn is already over), the rest
    is skipped: the commands that ran stay done, the result is `ok=False` with their events, and the log keeps a
    `WrapperCommand` of just those.
  - Accepted commands are stored in `command_log`. Seed + log is enough to replay a game.
  - `rollback()` undoes the last logged command exactly (state and log) and returns it (None = nothing to undo); it can
    be repeated back to the start of the game. `submit` keeps the state from before each command untouched in a
    history next to `command_log` and works on a copy (`GameState.copy()`, the board is shared because it never
    changes), so a rollback brings back the very same objects. A rejected or failing command leaves the state as it was.
    Cost: about 8 KB of memory per command (4 players).
  - `AttackCommand(attacker, target, move_to=None)`: gsm will attack target, if needed move is a swell made before attack.
- Map generation (`map_generator.py`) is the only random part. It uses `random.Random(seed)`.
  Every army is the same: a Boss + `BIG_RATS_PER_PLAYER` / `PEASANTS_PER_PLAYER` / `ARCHERS_PER_PLAYER` (`constant.py`).

## UI (`beastborn/ui`)

- `interface.py` defines the neutral contracts:
  - intents: `ClickTile`, `HoverTile`, `EndTurn`, `Quit`
  - `InputAdapter` and `Frontend`, for loop-based clients such as a console client. The web client is request-driven, so it reuses the intents but not these two.
- `interaction.py` (`Interaction`) holds the hover state and turns intents into commands for the GSM's active unit
  (click an enemy = attack, click a reachable tile = move, `EndTurn` = end the unit's turn). It knows nothing about the web and is unit-tested.
- `text.py` turns events into log lines.

### Web client (`beastborn/ui/web`)

- `sessions.py`: `SessionStore` keeps one `GameSession` (an `Interaction` plus a lock) per game id (`uuid4().hex`).
  - Sessions live in memory, so run a single server process (threads are fine).
  - A game expires after `BEASTBORN_SESSION_TTL` seconds idle (default 2 h). At most `BEASTBORN_MAX_GAMES` (default 500) run at once.
- `intents.py` maps request JSON to the existing intents: `click` → `ClickTile`, `end_turn` → `EndTurn`.
- `serializers.py` turns an `Interaction` into the state JSON: board, units, players, `active_unit`, what it can do (`reachable` tiles with cost and path, `targets` with exact damage), message and log. Nothing is recomputed.
- `app.py`: `create_app(store=None)` returns the Flask app.
  - `POST /api/games`, `GET /api/games/{id}`, `POST /api/games/{id}/intents`, `DELETE /api/games/{id}`, plus the static page at `/`.
  - Each intent runs under the game's lock: `interaction.handle(intent)` → `render(...)`.
- `static/`: `index.html`, `css/beastborn.css`, `js/beastborn.js` (jQuery), the icons in `img/` and the vendored libraries in `vendor/`.
  - The browser redraws the whole board from every state response.
  - Hover previews (path, cost, damage) come from the last state, so they need no requests.
    Each entry of `targets` has `move: null` (in range) or `{x, y, cost, path}` (moves there first).
  - Animations: the state has `actions: {seq, steps: [...]}`. `steps` holds only the moves and attacks since the last intent, in the order they happened (a move + attack is a move step, then an attack step; more if bots played). A move step (`kind: "move"`) has the path; an attack step (`kind: "attack"`) has from/to tile, ranged, damage and killed. `Interaction` keeps just these small records (`MoveAnimation`, `AttackAnimation`), not game snapshots, and clears them on the next intent. The browser plays steps newer than the last `seq` it saw (the unit walks its path, an arrow for ranged units, a lunge for melee) on the old board, then draws the new state. A page reload only remembers `seq`, so nothing is replayed.
  - A unit's picture is `img/units/<unit key>.svg`. A unit type without a picture shows its letter `code`.

## Bots

Implement `PlayerController.choose_command(gsm)` for `gsm.active_unit` using `reachable_tiles` / `attack_plans` / `preview_attack`, then give the session's `Interaction` the controllers (`Interaction(gsm, controllers={player_index: MyBot()})`). After every intent the web backend calls `run_bots()`, which lets bots act until a human is to move.
`PassController` is a trivial example: it ends every unit's turn without acting.
