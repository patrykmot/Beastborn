# Architecture

```
            ┌──────────────────────────────┐
 device ──▶ │ InputAdapter (mouse/keyboard) │ ──UIIntent──┐
            └──────────────────────────────┘             ▼
            ┌──────────────────────────────┐   ┌──────────────────┐   Command   ┌───────────────────────┐
 screen ◀── │ Renderer (pygame)             │◀──│ Interaction       │───────────▶│ GameStateMachine (GSM) │
            └──────────────────────────────┘   │ (ui presenter)    │◀─ events ──│  state, turn flow,     │
                                               └──────────────────┘   GameView │  validation            │
                         PlayerController (bot) ── Command ──────────────────▶ └──────────┬────────────┘
                                                                                           │ values in / values out
                                                                                ┌──────────▼────────────┐
                                                                                │ CalculationEngine (CE) │
                                                                                │  stateless formulas    │
                                                                                └───────────────────────┘
```

Dependencies only point down: `domain` ← `engine` ← `game` ← `control` / `ui`.
`tests/test_architecture.py` fails if a layer imports something it must not. For example, pygame is only allowed in `ui/pygame_ui`.

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
  - `InputAdapter`
  - `Frontend`
- `interaction.py` (`Interaction`) holds the selection and hover state and turns intents into commands. It does not import pygame and is unit-tested.
- `pygame_ui/` is the desktop frontend: `Layout`, `MouseKeyboardInput`, `Renderer`, `PygameFrontend`.

**New input device:** implement `InputAdapter.poll()` and pass it in with `PygameFrontend(MyInput)`.
**New frontend** (console, web): render `Interaction.view` and feed it intents. Game code does not change.

## Bots

Implement `PlayerController.choose_command(gsm)` using `reachable_tiles` / `attack_targets` / `preview_attack`, then pass `controllers={player_index: MyBot()}` to the frontend.
`PassController` is a trivial example: it always ends its turn.
