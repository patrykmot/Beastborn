# PLAN: Bot player (alpha-beta) + WatchState

> Request (2026-10-18, updated 18:43): `10_generate_bot.md`. Add a bot that can be picked as a player: at most one
> bot, and only in 2-player games. With 2 players, one player is a bot by default. While the bot plays, the human is
> in **WatchState**: the UI is blocked (except **New game**) and shows every bot action. The new
> `BotPlayerController(PlayerController)` chooses its command with **alpha-beta** search (ALG) over
> `GameStateMachine.find_all_possible_actions`, undoes its trial moves with `rollback` and maximises
> **my score − opponent score** (`GameStateMachine.score`). Default depth is 5. The GSM state must be exactly the
> same after the search. After every turn the bot says something short and a little funny (start / attack /
> defense), and it is easy to add more lines later. **Do not check performance.**
>
> Builds on: 06 (score), 08 (the GSM picks the unit), 09 (`find_all_possible_actions`, `WrapperCommand`, `rollback`).
> Status: **implemented** (B0-B5) with the decisions below. 274 tests pass (25 new). The page was checked in headless
> Chromium (desktop + mobile width): the Bot row on the start screen, WatchState, speech bubbles, the bot as
> Player 1, New game and reload during a bot turn, no console errors. `domain`, `engine` and `game` are unchanged.
> Notes: A5 became one 3-way choice, "Bot: No bot | Player 1 | Player 2" (default Player 2), instead of two seat
> toggles: it allows at most one bot by design. `create_app(store, bot_factory=...)` lets tests use a quick bot.
> Nothing was committed to git.
>
> Changes since the first version of this plan: evaluation = score difference (your answer); fixed depth 5 with no
> time budget or iterative deepening (you said "do not check performance"); the engine-validation question and the
> optional bot-vs-bot report (B6) are dropped.

---

## 0. Starting point

| What | Where | Today |
|---|---|---|
| Controller contract | `control/controller.py` | `PlayerController.choose_command(gsm) -> Command \| None`, `is_human`. `HumanController`, `PassController`. |
| Bot loop | `ui/interaction.py` | `Interaction(gsm, controllers)`, `human_turn`, `tick()` = one `choose_command` + `submit`. Intents are ignored when it is not a human's turn. |
| Web | `ui/web/app.py` | `run_bots(ui)` runs after **every intent** in the same request (`MAX_BOT_STEPS`). `create_game` never runs bots, so a bot as P1 would never move. |
| Sessions | `ui/web/sessions.py` | `SessionStore.create(gsm)` builds `Interaction(gsm)` **without controllers**. |
| New-game API | `ui/web/schemas.py` | `players`, `seed`, `size`. No way to ask for a bot. |
| Search primitives | `game/state_machine.py` | `find_all_possible_actions()` (one command = one **whole unit turn**, cached per state version), `submit`, `rollback()` (exact), `score(player)`. |
| Browser | `static/js/beastborn.js` | `human_turn` only disables the End turn button. Clicks and E/Space still send intents. |

Facts that shape the bot:

1. **One ply = one unit turn.** Every action from `find_all_possible_actions` plays a unit's whole turn, and then the
   GSM gives the next unit its turn. So depth 5 = bot unit, enemy unit, bot unit, enemy unit, bot unit.
2. **Turns do not strictly alternate.** Players take turns unit by unit, but once one side has fewer units left, the
   other side plays several units in a row (and units that cannot act are put off). So each node is MAX or MIN by
   `gsm.active_player == me`, **not** by depth parity.
3. `find_all_possible_actions` lists waiting first, then moves, then attacks. Alpha-beta cuts more when the strong
   moves come first, so the bot reorders them (attacks first). This changes only how much is searched, never the
   chosen value.

---

## 1. Decisions

| # | Decision |
|---|---|
| A1 | **Evaluation** (your answer): `evaluate(gsm, me) = gsm.score(me) - gsm.score(opponent)`. Positive = good for the bot. It is one method, `BotPlayerController.evaluate`, so changing the scoring later touches one place (or only `StandardCalculationEngine.score`, as today). End of game: a win = `+BOT_WIN_SCORE` (1 000 000), a loss = `-BOT_WIN_SCORE`, each adjusted by the remaining depth so the bot prefers a faster win and a slower loss (otherwise it may "play with its food"). |
| A2 | **Depth:** fixed `BOT_SEARCH_DEPTH = 5` (a constructor parameter too, so tests can use smaller ones). No time budget and no iterative deepening (you said "do not check performance"). The structure leaves room to add a time limit later without changing callers (section 7). |
| A3 | **No extra validation layer:** the bot trusts the GSM. Every action from `find_all_possible_actions` must be accepted by `submit`; if one is not, that is a bug and the bot raises `RuntimeError` (as `GameStateMachine._turns_after` already does). |
| A4 | **The GSM, CE and domain are not changed.** The bot uses only the public GSM API (`find_all_possible_actions`, `submit`, `rollback`, `score`, `view`, `command_log`, `phase`, `active_player`). |
| A5 | **Bot seat:** with 2 players the start screen shows `Player 1 [Human \| Bot]` and `Player 2 [Human \| Bot]`, **default: Player 2 = Bot** (the human moves first). Choosing Bot for one seat sets the other one to Human. With 3-4 players the row is hidden (no bots). |
| A6 | *(default)* **One bot unit turn per request.** A new endpoint, `POST /api/games/{id}/bot-step`, plays exactly one bot unit turn and returns the state with that turn's animations and bot message. In WatchState the browser calls it in a loop until it is the human's turn. The human sees each bot turn as it happens, no request runs a whole bot round, and a reload simply resumes the loop. `run_bots()` and `MAX_BOT_STEPS` are removed. |
| A7 | *(default)* **API default stays "no bot":** `NewGameRequest.bots: list[int] = []`. The UI sends `[1]` by default. That keeps the existing API tests and scripted hot-seat games working. So "2 players = one bot by default" is a **UI** default. |
| A8 | The bot's name is `"Player 2 (Bot)"` (`BOT_NAME_TEMPLATE`), set through `make_players(names=...)`. The JSON gets `players[i].bot = true`. |
| A9 | **Bot messages:** `Mood` = `START` (the bot's first turn in the game), `ATTACK` (the turn has an attack), `DEFENSE` (no attack: move or wait). The lines live in `beastborn/data/bot_lines.json`, like `units.json`, so you can add text without code. A new mood = one enum value + one JSON key. The line is picked at random (an injectable `random.Random`), never the same line twice in a row. Only `BotPlayerController` has a voice. |
| A10 | During WatchState the server does not compute `reachable` / `targets` for the bot's unit (they are sent empty), so the board shows no human highlights on the bot's turn. |

---

## 2. Design

### 2.1 Files

```
beastborn/
├─ constant.py                 + BOT_* constants (2.5), - MAX_BOT_STEPS
├─ data/bot_lines.json         NEW  mood -> list of lines
├─ control/
│  ├─ __init__.py              + export BotPlayerController, BotVoice, Mood
│  ├─ controller.py            (docstring only: points to bot.py)
│  ├─ bot.py                   NEW  BotPlayerController (alpha-beta, evaluate)
│  └─ bot_voice.py             NEW  Mood, BotVoice, load_bot_lines()
└─ ui/
   ├─ interaction.py           + BotSpeech step, watching, no previews on bot turns, tick() -> bool
   └─ web/
      ├─ schemas.py            + NewGameRequest.bots (validated)
      ├─ sessions.py           create(gsm, controllers=None)
      ├─ app.py                create_game with bots, POST /bot-step, run_bots removed
      ├─ serializers.py        + players[].bot, "watching", step kind "say"
      └─ static/
         ├─ index.html         + Opponent row on the start screen, "Bot is thinking" badge
         ├─ js/beastborn.js    + WatchState loop, speech bubble, input blocking
         └─ css/beastborn.css  + .watching, .speech-bubble
tests/
├─ test_bot.py                 NEW
├─ test_ui.py / test_web_api.py / test_web_serializers.py   + a few cases
docs/architecture.md, README.md   Bots section, API table
```

### 2.2 `BotPlayerController` (`control/bot.py`)

```python
class BotPlayerController(PlayerController):
    """Alpha-beta over whole unit turns (GameStateMachine.find_all_possible_actions). Never changes the game."""

    def __init__(self, depth: int = C.BOT_SEARCH_DEPTH, voice: BotVoice | None = None):
        self.depth = depth
        self.last_message: str | None = None        # what the bot says about the command it just chose
        self.last_value: int | None = None          # search value of that command (log / tests / future moods)

    def choose_command(self, gsm) -> Command | None:
        if gsm.phase is not Phase.AWAITING_COMMAND:
            return None
        me = gsm.active_player
        log_size, before = len(gsm.command_log), gsm.view()
        command, self.last_value = self._best_action(gsm, me)   # every submit is undone in finally: rollback()
        if len(gsm.command_log) != log_size or gsm.view() != before:
            raise RuntimeError("Bot search changed the game state")   # the critical requirement, checked every call
        self.last_message = self._voice.line(self._mood(command))
        return command

    def evaluate(self, gsm, me: int) -> int:        # A1: the one place that defines "good for the bot"
        return gsm.score(me) - gsm.score(self._opponent(gsm, me))
```

Search (ALG):

- `_best_action`: `actions = find_all_possible_actions()`. If there is only one action, return it (no search).
  Otherwise run alpha-beta at the root over `_order(actions)` with `depth - 1` below it, and keep the first action
  with the best value.
- `_alphabeta(gsm, me, depth, alpha, beta) -> int`:
  - game over → `+BOT_WIN_SCORE + depth` if the bot won, `-BOT_WIN_SCORE - depth` if it lost (a draw = `evaluate`)
  - `depth == 0` → `evaluate(gsm, me)`
  - MAX if `gsm.active_player == me`, else MIN (fact 2)
  - for each action in `_order(...)`: `result = gsm.submit(action)`; if not `result.ok` → `RuntimeError` (A3);
    then `try: value = _alphabeta(...depth - 1...) finally: gsm.rollback()`; update alpha/beta; stop when `alpha >= beta`
- `_order`: actions with more attacks first, then moves, then waiting (fact 3). It is a stable sort, so equal values
  go to the first action and the result is deterministic.
- `_opponent(gsm, me)`: the other player index (a bot exists only in 2-player games, A5).
- Nothing is kept in the bot between turns (no stale state).

`try/finally` around every `submit` means that even an exception deep in the tree (a bug, Ctrl+C in tests) unwinds
all trial moves. The final check in `choose_command` proves it on every call.

### 2.3 `BotVoice` (`control/bot_voice.py`) and `data/bot_lines.json`

```python
class Mood(Enum):
    START = "start"
    ATTACK = "attack"
    DEFENSE = "defense"
    # later: KILL, BOSS_HIT, WINNING / LOSING (from last_value), GAME_OVER ...

class BotVoice:
    def __init__(self, lines: Mapping[Mood, Sequence[str]] | None = None, rng: random.Random | None = None): ...
    def line(self, mood: Mood) -> str: ...   # a random line, not the previous one again

def load_bot_lines(path: Path = C.BOT_LINES_FILE) -> dict[Mood, tuple[str, ...]]: ...  # checks every Mood has lines
```

The bot picks the mood: `START` for its first command in the game, `ATTACK` if the command (or its wrapper) has an
`AttackCommand`, otherwise `DEFENSE`. Starter lines (you can edit these):

```json
{
  "start":   ["Beep boop. I have calculated 5 turns ahead. You have calculated lunch.",
              "Initiating Operation Big Rat. Resistance is mildly inconvenient.",
              "Good luck, human. Statistically, you will need it."],
  "attack":  ["Nothing personal. It's just alpha-beta.",
              "Bonk. Calculated bonk.",
              "Your Boss's HP bar looks a bit long. Let me fix that."],
  "defense": ["Tactical retreat. Totally planned. Definitely.",
              "I'm not hiding, I'm optimising my position.",
              "Waiting is also a move. A very clever move."]
}
```

### 2.4 `Interaction`

- `tick() -> bool`: if a bot is to move, run one `choose_command` + `submit` (= one whole unit turn) and return
  True if something happened. After an accepted bot command with a `last_message`, append
  `BotSpeech(seq, player, unit_id, text)` to `last_actions` (after that turn's move and attack steps) and a log line
  `Player 2 (Bot): "Bonk. Calculated bonk."`.
- `watching` property: `phase != GAME_OVER and not human_turn`.
- `_refresh()`: on a bot turn, `reachable` / `attack_plans` stay empty (A10).
- Intents during a bot turn are already ignored (`_waiting_for_human`). No change there.

### 2.5 Constants (`constant.py`)

```python
# ====================================================================== bot
BOT_SEARCH_DEPTH: Final[int] = 5               # unit turns searched ahead (ALG)
BOT_WIN_SCORE: Final[int] = 1_000_000          # value of a won game in the search (a lost one = minus this)
MAX_BOTS: Final[int] = 1                       # bots per game
BOT_PLAYERS: Final[int] = 2                    # a bot can play only in games with exactly this many players
BOT_NAME_TEMPLATE: Final[str] = "Player {number} (Bot)"
BOT_LINES_FILE: Final[Path] = PACKAGE_DIR / "data" / "bot_lines.json"
```
`MAX_BOT_STEPS` is removed (A6).

### 2.6 Web backend

- `schemas.NewGameRequest.bots: list[int] = []`, with a model validator. Each entry is a seat index
  `< players`, entries are unique, `len(bots) <= MAX_BOTS`, and bots are allowed only when
  `players == BOT_PLAYERS`. Anything else → 422 with a clear `msg` (the browser shows `detail[0].msg`).
- `sessions.SessionStore.create(gsm, controllers=None)` → `Interaction(gsm, controllers)`.
- `app.create_game`: bot seats get `BOT_NAME_TEMPLATE` names and a `BotPlayerController()`. The response comes back
  right away. If the bot is P1, the state is already `watching`.
- `app.send_intent`: no `run_bots`. An intent sent during a bot turn changes nothing and returns the state.
- **New** `POST /api/games/<id>/bot-step` (no body): under the game lock, if `watching`, run `ui.tick()` once.
  Return `render(...)`. If it is the human's turn or the game is over, it changes nothing (safe for two tabs or a
  double call).
- `serializers.render`: `players[].bot`, `"watching"`, and a step `{"kind": "say", "seq", "player", "unit_id", "text"}`.

### 2.7 Browser: start screen and WatchState

- **Start screen:** an "Opponent" row that is shown only for 2 players, with two seat toggles (A5). The default is
  Player 2 = Bot. The request sends `bots: [seat]` or `[]`.
- **WatchState** (`state.watching`):
  - `body.watching`: board clicks, hover previews and E/Space are off, the cursor is `not-allowed`, and End turn is
    disabled. **New game** and **Copy link** still work.
  - The turn card shows a "🤖 Bot is thinking…" badge with a spinner (it stays up while a `bot-step` request runs).
  - Loop: `setState` → if watching → `api.botStep()` → `playActions(new steps)` (move and attack animations as
    today, plus `say` = a speech bubble over the bot's unit for about 2.5 s; the line is also in the log) →
    `setState` → … until it is a human's turn or the game is over.
  - **New game** during WatchState: abort the pending request (`jqXHR.abort()`) and bump a "game generation"
    counter, so a late response is dropped.
  - Reload or open the link during a bot turn: `GET` returns `watching: true`, and the loop resumes.
  - A `bot-step` error: show a toast; the loop stops and the badge becomes a "Continue" button that restarts it.

---

## 3. Tests (simple, happy path + the critical ones)

`tests/test_bot.py` (small `custom_game` boards where a scenario is needed):

| Test | Checks |
|---|---|
| `test_bot_returns_a_possible_action` | With the default depth (5): the command is in `find_all_possible_actions()` and `submit` accepts it (`new_game(2, seed=7)`, round 1). |
| `test_search_leaves_state_untouched` | **Critical:** `command_log`, `view()`, `score(0)`, `score(1)` and `find_all_possible_actions()` are identical before and after `choose_command`. |
| `test_state_untouched_after_error_in_search` | `evaluate` patched to raise after N calls: the exception comes out and the state is still identical (every trial move was rolled back). |
| `test_alphabeta_equals_minimax` | On a tiny board at depth 3, the root value equals a plain minimax written in the test, including a position where one side moves twice in a row (fact 2). |
| `test_bot_takes_the_winning_hit` | A weakened enemy Boss in range: the bot attacks it (win). |
| `test_bot_prefers_attack_to_waiting` | An easy hit is available: the bot does not wait. |
| `test_voice_moods` | First message = `START`, attack turn = `ATTACK`, move/wait = `DEFENSE`; injected lines are used; no line twice in a row; `bot_lines.json` has lines for every `Mood`. |

Additions to existing files:
- `test_ui.py`: `Interaction` with a bot (small depth): `tick()` plays one unit turn and adds a `say` step and a log
  line; intents are ignored while `watching`; `reachable`/`targets` are empty on a bot turn.
- `test_web_api.py`: `bots: [1]` → `players[1].bot`; 422 for `bots: [0, 1]`, `players: 3` + bot, seat 2; flow:
  human `end_turn` → `watching` → `bot-step` until the human's turn, with a `say` step; `bots: [0]` → the first
  state is `watching`; `bot-step` on a human turn changes nothing. The API tests use a bot with a small depth
  (`create_app` gets an optional bot factory) so the suite stays quick.
- `test_web_serializers.py`: the new fields.
- `test_architecture.py`: unchanged. The bot lives in `control` and may import game/engine/domain/constant, not ui.

---

## 4. Milestones

| # | Step | Done when |
|---|---|---|
| B0 | Constants, `bot_lines.json`, `BotVoice` | `test_voice_moods` green |
| B1 | `BotPlayerController`: ALG, `evaluate`, state check | `test_bot.py` green |
| B2 | `Interaction`: `BotSpeech`, `watching`, no previews on bot turns | `test_ui.py` green |
| B3 | Backend: schema, sessions, `create_game`, `bot-step`, `run_bots` removed | `test_web_api.py`, `test_web_serializers.py` green |
| B4 | Browser: start screen, WatchState loop, speech bubble, blocking | manual checklist (section 5) passes in headless Chromium, desktop + mobile width |
| B5 | Docs: `docs/architecture.md` (Bots section rewritten), `README.md` (API table + bot) | text matches the code |

---

## 5. Manual checklist

1. Start screen, 2 players: the Opponent row shows Player 2 = Bot. 3-4 players: the row is hidden.
2. Start: the human (P1) moves. After the unit's turn ends, the "Bot is thinking" badge appears and the board is
   blocked (clicks, hover previews, E/Space and End turn do nothing).
3. Each bot unit turn is animated (walk, arrow or lunge), then a speech bubble appears and the log shows the
   message. Then either the next bot turn runs or control comes back to the human.
4. Seat 1 = Bot: the bot opens the game right after Start.
5. **New game** during a bot turn works at once, and nothing from the old game appears later.
6. Reload during a bot turn: the game resumes in WatchState and finishes the bot's turns.
7. Play to the end: the game-over modal works when the bot wins and when it loses.

---

## 6. Definition of Done

- `python -m pytest` is green.
- `beastborn/domain`, `engine` and `game` are **not changed** (`git diff --stat`): the bot uses only the public
  GSM API (A4).
- The bot never changes the game while searching (tests + runtime check in `choose_command`).
- At most one bot, only in 2-player games (API 422 + UI).
- The manual checklist passes.

---

## 7. Risks and notes

- **Thinking time (not a task here, as asked).** When I measured the current code before this update, a fixed depth
  of 5 took about 1 s per bot unit turn while the armies are apart, but 10 s to over 40 s in fights. WatchState
  keeps the UI honest ("Bot is thinking"), and one-turn-per-request (A6) keeps each request to a single unit turn.
  On PythonAnywhere a long `bot-step` request also holds a server thread and that game's lock. If it becomes a
  problem later, there are three ways out: lower `BOT_SEARCH_DEPTH`, add a time budget with iterative deepening
  inside `_best_action` (callers do not change), or skip duplicate end states (see below).
- **Duplicate end states:** `find_all_possible_actions` lists every command order (e.g. two attacks in the other
  order), and the search visits each. That is correct, just repeated work.
- **Opponent model:** the bot assumes the human plays the best reply under the same `evaluate`. Changing
  `evaluate` / `score` later changes how careful or bold the bot is.
