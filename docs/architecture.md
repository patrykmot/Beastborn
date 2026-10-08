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

## Calculation Engine (`beastborn/engine`)

- `CalculationEngine` (ABC) is the single interface for every rule calculation:
  - `step_cost`
  - `initial_energy`, `regenerate`, `attack_cost`, `in_attack_range`
  - `elevation_modifier`, `current_defense`, `resolve_attack`
  - `tick_effects`
- It only computes. It gets snapshots (`CombatantSnapshot`, `Tile`, `UnitStats`) and returns results (`AttackResult`, `TickResult`). It never changes game state.
- `StandardCalculationEngine` implements the design document. All its numbers are in `RulesConfig`.

**Change the rules:**

```python
from beastborn.engine import RulesConfig, StandardCalculationEngine
from beastborn.game import new_game

# tweak numbers
gsm = new_game(2, engine=StandardCalculationEngine(RulesConfig(uphill_attack_penalty=True, allow_block=True)))

# or write a new rule set
class MyEngine(StandardCalculationEngine):
    def step_cost(self, unit, src, dst):
        return 1
gsm = new_game(2, engine=MyEngine())
```

Nothing in `game/` or `ui/` changes. `test_swapping_calculation_engine_changes_rules_without_touching_gsm` checks this.

## Game State Machine (`beastborn/game`)

- Phases: `SETUP → AWAITING_COMMAND ⇄ … → GAME_OVER`.
- `gsm.start()` begins the first turn. `new_game()` calls it for you.
- Start of turn, for each unit of the active player:
  1. energy regeneration
  2. status ticks
  3. deaths
- Eliminated players are skipped.
- Queries:
  - `view()` returns a read-only `GameView`.
  - `reachable_tiles(unit_id)` returns cost and path for every tile the unit can reach.
  - `attack_targets(unit_id)` lists enemies the unit can attack now.
  - `preview_attack(a, t)` returns the exact result, because combat has no RNG.
- Commands: `submit(MoveCommand | AttackCommand | EndTurnCommand)` returns a `CommandResult(ok, events, error)`.
  - A rejected command never changes state.
  - Accepted commands are stored in `command_log`. Seed + log is enough to replay a game.
- Map generation (`map_generator.py`) and the army draw are the only random parts. Both use `random.Random(seed)`.

## UI (`beastborn/ui`)

- `interface.py` defines the neutral contracts:
  - intents: `ClickTile`, `HoverTile`, `EndTurn`, `Cancel`, `Quit`
  - `InputAdapter` and `Frontend`, for loop-based clients such as a console client. The web client is request-driven, so it reuses the intents but not these two.
- `interaction.py` (`Interaction`) holds the selection and hover state and turns intents into commands. It knows nothing about the web and is unit-tested.
- `text.py` turns events into log lines.

### Web client (`beastborn/ui/web`)

- `sessions.py`: `SessionStore` keeps one `GameSession` (an `Interaction` plus a lock) per game id (`uuid4().hex`).
  - Sessions live in memory, so run a single server process (threads are fine).
  - A game expires after `BEASTBORN_SESSION_TTL` seconds idle (default 2 h). At most `BEASTBORN_MAX_GAMES` (default 500) run at once.
- `intents.py` maps request JSON to the existing intents: `click` → `ClickTile`, `end_turn` → `EndTurn`, `cancel` → `Cancel`.
- `serializers.py` turns an `Interaction` into the state JSON: board, units, players, selection (reachable tiles with cost and path, targets with exact damage), message and log. Nothing is recomputed.
- `app.py`: `create_app(store=None)` returns the Flask app.
  - `POST /api/games`, `GET /api/games/{id}`, `POST /api/games/{id}/intents`, `DELETE /api/games/{id}`, plus the static page at `/`.
  - Each intent runs under the game's lock: `interaction.handle(intent)` → `render(...)`.
- `static/`: `index.html`, `css/beastborn.css`, `js/beastborn.js` (jQuery), the icons in `img/` and the vendored libraries in `vendor/`.
  - The browser redraws the whole board from every state response.
  - Hover previews (path, cost, damage) come from the last state, so they need no requests.
  - A unit's picture is `img/units/<unit key>.svg`. A unit type without a picture shows its letter `code`.

**New frontend** (console, desktop): render `Interaction.view` and feed it intents. Game code does not change.

## Bots

Implement `PlayerController.choose_command(gsm)` using `reachable_tiles` / `attack_targets` / `preview_attack`, then give the session's `Interaction` the controllers (`Interaction(gsm, controllers={player_index: MyBot()})`). After every intent the web backend calls `run_bots()`, which lets bots act until a human is to move.
`PassController` is a trivial example: it always ends its turn.
