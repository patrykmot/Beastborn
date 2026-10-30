# PLAN: Game record format (BGN, "Beastborn Game Notation")

> Request (2026-10-30): a way of saving a game similar to chess PGN. It holds the **seed**, the **players** and
> their **moves**, so that loading it simulates the whole game again. It must be possible to get it from the
> `GameStateMachine`. **For now only the format itself**: this plan. **No code changes.**
> Status: **format designed, not implemented.** A throw-away prototype (outside the repo) exported and re-loaded
> 8 random full games (seeds 1, 2, 3, 7, 11, 99, 1234, 482913; with wrapper commands, auto-moved attacks and
> no-op end turns): every replay gave the same final state. The example in section 3 is a real game from it.

---

## 0. Why seed + commands are enough

- `new_game(players, seed, width, height)` always builds the same map, armies and unit ids (`map_generator.py`
  uses only `random.Random(seed)`; `place_armies` gives ids in a fixed order).
- Combat has **no RNG** and the GSM picks whose turn it is, so the state after a command depends only on the state
  before it.
- `GameStateMachine.command_log` already holds every accepted command (the GSM's own comment: *"with the seed this
  is enough to replay a game"*). A rejected command is never logged; a rolled-back one is removed from the log.
- An `AttackCommand` that auto-moved is logged **with the tile it moved to**, so a replay never depends on how the
  tile was picked (`AUTO_MOVE_TO_ATTACK`, `_origin_rank`).
- Bots do not need to be re-run: their moves are in the log like a human's.

What else must match for a replay to give the same game: the **rules** (`constant.py` rule numbers, map generator
numbers, army counts) and **`units.json`**. Section 2.2 adds a short hash of them, so a mismatch is reported
instead of silently giving another game.

---

## 1. Questions

| # | Question | Proposed default |
|---|---|---|
| Q1 | How are tiles written? | **Chess-like:** column letter + row number, `a1` = `Position(0, 0)` = top-left (P1's corner, like White's). `x` -> `a..p`, `y` -> `1..16` (the biggest map is 16x16). Short, and unambiguous next to unit names. *Alternative:* `(x,y)` as in `Position.__str__` (longer, but the same as the code and the logs). |
| Q2 | File extension | **`.bgn`**, UTF-8 text. Several games in one file allowed (separated by a blank line, as in PGN): handy for bot test sets and training data. |
| Q3 | Comments with numbers (HP after a hit etc.) in the exported text? | **Off by default**, an option of the exporter (`annotate=True`). The parser always skips comments. |
| Q4 | Games made with `custom_game` (no seed) | **Not in v1** (the exporter refuses them). v2: optional `Board` + `Units` tags (section 2.6), like PGN's `FEN`. |

---

## 2. The format

A record = **tag pairs** (header), an empty line, the **movetext**, and a **result** token. Like PGN.

### 2.1 Tags

`[Name "Value"]`, one per line. Order as below. Unknown tags are kept and ignored (as in PGN).

| Tag | Required | Example | Source | Notes |
|---|---|---|---|---|
| `Game` | yes | `"Beastborn"` | `GAME_NAME` | identifies the file type |
| `Version` | yes | `"0.10"` | `GAME_VERSION` | informational; `Rules` decides compatibility |
| `Rules` | yes | `"3f9a1c0b7d22"` | section 2.2 | replay refuses (or warns, `strict=False`) on mismatch |
| `Date` | no | `"2026.10.30"` | exporter | PGN date style |
| `Site` | no | `"gercio.eu"` | caller | |
| `Seed` | yes | `"482913"` | `gsm.view().seed` | |
| `Size` | yes | `"12x12"` | `view.board.width`, `height` | `new_game(width=, height=)` |
| `Players` | yes | `"2"` | `len(view.players)` | 2-4 |
| `P1` .. `P4` | yes | `"Alice"` | `view.players[i].name` | one per player; `P1` = player index 0 |
| `P1Type` .. `P4Type` | no | `"human"` / `"bot"` | caller (the GSM does not know) | default `human` |
| `P1Bot` .. `P4Bot` | no | `"minimax depth 16"` | caller | free text, which bot played |
| `Result` | yes | `"P1"`, `"draw"`, `"*"` | `view.phase`, `view.winner` | `*` = game not finished; `draw` = `winner None` |
| `Rounds` | no | `"11"` | `view.round` | informational |
| `Final` | no (exporter always writes it) | `"6fa1cdf54b97"` | section 2.3 | checked after a replay |

### 2.2 `Rules` hash

First 12 hex digits of SHA-256 of a canonical JSON string (`sort_keys=True`, no spaces) of:

- `units`: the `"units"` list of `units.json` (what `load_roster` reads);
- `rules`: `EN_NT, EN_DT, EN_MU, EN_MD_NORMAL, EN_MD_DIFFICULT, DIFFICULT_TERRAINS, ELEVATION_COEF,
  RANGE_DISSIPATION, TERRAIN_DEFENSE_DIVISOR, MIN_DAMAGE, ALLOW_BLOCK, VENOM_*, ACID_ATK_DIVISOR,
  MIN_EFFECT_MAGNITUDE, STARTING_ENERGY, MAX_ATTACKS_PER_TURN`;
- `setup`: `BIG_RATS/PEASANTS/ARCHERS_PER_PLAYER, SPAWN_ZONE_SIZE, MAX_ELEVATION, SMOOTH_ELEVATION,
  HILLS_PER_100_TILES, MUD_PATCHES_PER_100_TILES, HILL_BASE_ELEVATION, MIN_HILLS, MIN_MUD_PATCHES,
  MUD_WALK_MIN_STEPS, MUD_WALK_MAX_STEPS, TILES_PER_DENSITY_UNIT`.

Not included: `AUTO_MOVE_TO_ATTACK` (the record always has the tile), score / bot / AI / UI constants (they never
change what a command does). One function, `rules_hash()`, next to the exporter; a new rule constant must be added
to it (a test lists the rule section of `constant.py` to catch a forgotten one).

### 2.3 `Final` checksum

First 12 hex digits of SHA-256 of the final state, written as text:
`round;winner;phase` then, for each unit on the board in id order,
`id:x,y:hp:energy:defense:effects` (effects = `kind/magnitude/remaining_turns/source_id`, in their stored order),
joined with `|`. After a replay the loader computes it again: equal = the replay really ended where the game did.

### 2.4 Movetext

```
1. R2 f2; R6 end; B1 b2; P7 j10; P3 c3; B5 k11; A4 d1; A8 k10
2. B1 d2; B5 j11 j12; R2 h5; ...
```

- **One line per round**: `<round>.` then the **unit turns** of that round, separated by `;`, in the order they
  were played.
- **A unit turn** = the unit, then its actions:  `<Unit> <action> <action> ...`
  - **Unit** = its letter (`code` in `units.json`: `B`, `R`, `P`, `A`) + its **id**: `R2`, `B5`. The id is what
    counts; the letter is for reading and is checked on load. The unit is written even though the GSM picks it:
    turn order is not obvious (a unit that cannot act is put off, e.g. `R2` plays before `B1` in round 1), and it
    lets the loader check that the replay is still in step.
- **Actions** (the unit is the turn's unit):

| Action | Text | Command |
|---|---|---|
| Move | `f2` | `MoveCommand(unit, Position(5, 1))` |
| Attack from where it stands | `xR6` | `AttackCommand(unit, 6)` |
| Move, then attack (one command) | `k5xR6` | `AttackCommand(unit, 6, move_to=Position(10, 4))` |
| End turn | `end` | `EndTurnCommand()` |

  `f2 xR6` (a move, then an attack: two commands) and `f2xR6` (one attack command that moves first) are kept apart,
  as in the log.
- **No `end`** after a turn means the GSM ended it (the unit could not do anything more). Turns the GSM skipped
  (a unit that could not act at all, or died from venom at its turn start) are not written: the replay makes them
  happen by itself.
- **Comments**: `{...}` anywhere between tokens, ignored by the parser. E.g. `xR6 {R6 25->20}`.
- **Whitespace and line breaks do not matter** (only `;` and round numbers structure the text), so long rounds may
  wrap. Round numbers are checked on load but are not needed to replay.
- **Result token** at the end, equal to the `Result` tag: `#P1` .. `#P4` (winner), `#draw`, `*` (unfinished).
  (`#` because `P1` alone would read as "Peasant id 1".)

### 2.5 Grammar

```
record     = { tag } blank movetext result
tag        = "[" Name " \"" Value "\"]" newline
movetext   = { round }
round      = Int "." turn { ";" turn }
turn       = unit action { action }
action     = square | "x" unit | square "x" unit | "end"
unit       = Upper { Upper } Int                 e.g. R2, B12
square     = "a".."p" Int                        a1 .. p16
result     = "#P1" | "#P2" | "#P3" | "#P4" | "#draw" | "*"
comment    = "{" { any char except "}" } "}"     allowed between any two tokens
```

### 2.6 v2 (Q4): hand-made boards

`[SetUp "1"]` + `[Board "..."]` (rows of `Board.__str__` tokens joined by `/`, e.g. `.0 .0 m1/.0 .1 .0`) +
`[Units "1:boss:0:a1 2:big_rat:0:b1 ..."]` (`id:unit key:owner:tile`). The loader then calls `custom_game`
instead of `new_game`. Needs the GSM's start state (section 4.1), so not in v1.

---

## 3. Example (real game, seed 7, from the prototype)

```
[Game "Beastborn"]
[Version "0.10"]
[Rules "3f9a1c0b7d22"]
[Date "2026.10.30"]
[Seed "7"]
[Size "12x12"]
[Players "2"]
[P1 "Alice"]
[P1Type "human"]
[P2 "Player 2 (Bot)"]
[P2Type "bot"]
[Result "P2"]
[Rounds "11"]
[Final "6fa1cdf54b97"]

1. R2 e3; R6 i9; B1 b2; B5 k11; P3 b4; P7 i11; A4 c2; A8 k10
2. B1 c3; B5 j10; R2 h5; R6 g6; P3 b7; P7 f11; A4 end; A8 j9
3. B1 c5; B5 i9; R2 h6xR6 xR6; R6 xR2 xR2 xR2; P3 c9; P7 f8; A4 d3; A8 j8
4. B1 c7; B5 g9; R2 j7xA8; R6 j6xR2; P3 end; P7 c8; A4 e3; A8 i8 xR2
5. B1 d8; B5 e9; R2 j8xA8 j9; R6 f7; P3 xP7 d9xB5; P7 d7 c7; A4 e4 xR6; A8 xR2 xR2
6. B1 d7xP7 end; B5 xP3 end; P3 xB5 xB5; R6 e7xB1 xB1; A4 f4xR6; P7 xB1 end; A8 h8
7. B1 e8; B5 end; P3 xB5 end; R6 xB1 end; A4 xR6 f5; P7 d8xB1; A8 g8xP3
8. B1 xP7 end; B5 xP3 e10; P3 e9 xB5 xB5; P7 d9xP3 d8; A4 xA8 end; A8 g9xB1
9. B1 xP7 f8; B5 xP3 end; P3 e8xP7; P7 xP3 xP3; A4 g5xA8
10. B1 f10; B5 e9xP3 xP3; P3 f9xB5; P7 e10; A4 g7
#P2
```

(The `Rules` value is a placeholder until `rules_hash()` exists.) Things this game shows:
- Round 1: `R2` before `B1` (the Boss is boxed in at the start, so it is put off).
- `R2` is missing from round 6 on (killed in round 5); `R6` and `A8` drop out later the same way.
- The game ends in **round 11 with no command**: `B1` dies from venom at its turn start. The movetext stops after
  round 10; the replay reaches the end by itself, and `Result` / `Final` confirm it.

---

## 4. How it will connect to the code (later, not now)

### 4.1 Getting the record from the GSM (export)

New module `beastborn/game/record.py` (game layer: imports only `game`, `domain`, `constant`; nothing from
`ui`/`control`, so `tests/test_architecture.py` stays happy):

```python
@dataclass(frozen=True)
class UnitTurn:
    round: int
    unit_id: int
    commands: tuple[Command, ...]       # Move / Attack / EndTurn only (wrappers unpacked)

@dataclass(frozen=True)
class GameRecord:
    tags: dict[str, str]
    turns: tuple[UnitTurn, ...]
    result: str                          # "P1".."P4", "draw", "*"

def record_game(gsm: GameStateMachine, player_types: Mapping[int, str] | None = None,
                extra_tags: Mapping[str, str] | None = None) -> GameRecord: ...
def format_record(record: GameRecord, annotate: bool = False) -> str: ...
def parse_record(text: str) -> GameRecord: ...          # also parse_records() for a multi-game file
def replay(record: GameRecord, until: int | None = None, strict: bool = True) -> GameStateMachine: ...
def rules_hash() -> str: ...
```

`record_game` uses **only public GSM API**: `gsm.view()` (seed, board size, players, round, phase, winner),
`gsm.command_log` and `gsm.engine`. The log has no round / turn boundaries, so `record_game` **replays the log on a
fresh `new_game(seed, ...)`** and reads them from the events (`TurnEnded`, `gsm.active_unit`, `view().round`). This
costs a few ms (no bots) and doubles as a check that the log really replays. Player types come from the caller:
`Interaction` knows its controllers, so a later `Interaction.record()` can pass `{1: "bot"}`.

Optional small GSM addition for v2 / custom boards: a read-only `start_state` (a copy of the state before the
first logged command), so `record_game` does not need a seed.

### 4.2 Pitfall found by the prototype: the no-op `EndTurn` of a wrapper

`_run_wrapper` accepts a trailing `EndTurnCommand` **after the GSM already ended the turn**, does nothing, and
**logs it** inside the `WrapperCommand`. Unpacked and submitted on its own, that `end` would end the **next**
unit's turn and the replay would go wrong. Rule for the exporter: inside one logged wrapper, once a command's events
end the turn (`gsm.turn_over(unit, events)`), drop the rest (it can only be that `EndTurn`). A plain logged
`EndTurnCommand` is never a no-op, so it is always kept. (The prototype's first version had exactly this bug.)

Other cases, all handled by the format as designed:
- `AttackCommand` with `move_to` = its own tile is logged unchanged: written as a plain `xR6` (the exporter writes
  a tile only if a `UnitMoved` event came out of the command).
- A wrapper rejected part-way is logged with only the commands that ran: nothing special.
- A game saved in the middle of a unit turn: the last turn is just shorter; `Result "*"`.

### 4.3 Loading (replay)

1. `parse_record` -> tags + turns. Check `Game`, required tags, `Result` tag = result token.
2. Compare `Rules` with `rules_hash()`: different -> `strict` raises a clear error, otherwise warn and try.
3. `gsm = new_game(Players, seed=Seed, width, height, player_names=[P1..])`.
4. For each turn: check `gsm.active_unit == turn.unit_id` (and the letter), then `submit` each command; a rejected
   command -> error with the round and the turn text (e.g. `round 4, "R6 j6xR2": Not enough energy...`).
5. Check `Final` (if present) and the result.
6. `until=n` stops after `n` unit turns (a viewer can jump to a position).

The replayed GSM is a normal game: an unfinished record (`*`) can be **played on**. A replay viewer gets stepping
back for free from `gsm.rollback()` (one logged command per action, so back = `rollback`, forward = `submit`).

### 4.4 Tests (later)

- Round trip: random legal games (several seeds, 2 and 4 players, wrappers, auto-move attacks) -> export -> parse
  -> replay -> the same `view()` and `Final`.
- The wrapper no-op `EndTurn` case (4.2) on a hand-picked position.
- Parser: comments, line wrapping, multi-game file, bad tokens (clear error with position).
- Wrong `Rules` -> error; wrong `Final` -> error; wrong turn unit -> error naming the round.
- `rules_hash()` covers every constant of the rules section of `constant.py`.

### 4.5 Milestones (later)

| # | Step | Done when |
|---|---|---|
| M1 | `record.py`: dataclasses, `record_game`, `format_record`, `rules_hash` | the section 3 game exports to the same text |
| M2 | `parse_record`, `replay` (+ `until`, `strict`) | round-trip tests green |
| M3 | Web: `GET /api/games/<id>/record` (download `.bgn`) + "Save game" button; `Interaction.record()` | a browser game downloads and re-loads |
| M4 | Load a `.bgn` in the browser (replay viewer: next / back via `rollback`) | optional |
| M5 | `docs/` page: the format (sections 2-3 of this plan) | text matches the code |

**This task changed no code.** Only this file was added.
