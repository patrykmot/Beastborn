# PLAN: Web client (replace the desktop client)

> Request (2026-10-07): remove the pygame desktop client and add a simple **web client**. The backend is **FastAPI**; the frontend uses **jQuery + Bootstrap**; use simple downloaded graphics for terrain and characters. Reuse `ui/interface.py` where possible. **No game-logic changes, UI only.** Opening the page and pressing **Start** creates a new game with a unique id, and the backend keeps that game in a session, so many games can run at the same time. Keep it simple.
>
> Builds on: `01_game_mechanics_documentation_PLAN.md` (implemented, commit `7dc60ef`).
> Status: **implemented** (W0–W4) with the defaults from section 2. 142 tests pass, and the page was checked in headless Chromium (desktop + mobile width).
> Notes: the icons come from the `@iconify-json/game-icons@1.2.4` npm package, because the raw GitHub SVGs have a black background square that breaks CSS masks. The acid icon's author is Sbed. Nothing was committed to git.

---

## 0. Starting point

| Layer | Path | In this plan |
|---|---|---|
| Domain, CE, GSM, controllers | `beastborn/domain`, `engine`, `game`, `control` | **Untouched.** The Definition of Done checks this with `git diff`. |
| UI contracts | `beastborn/ui/interface.py` | **Reused.** The intents `ClickTile`, `EndTurn`, `Cancel` become the web API vocabulary. |
| UI presenter | `beastborn/ui/interaction.py` | **Reused as-is.** It holds the selection, turns intents into GSM commands and keeps the log/messages. One instance per web game. |
| Event text | `beastborn/ui/text.py` | **Reused** for the game log. |
| Desktop frontend | `beastborn/ui/pygame_ui/` | **Removed.** |
| Entry point | `main.py` | Rewritten: starts the web server. |

The desktop client was a thin layer over `Interaction`, so the web client can replace it without touching the game. The backend keeps one `Interaction` per game id. The browser sends intents and draws the state it gets back.

---

## 1. Target architecture

```
Browser (jQuery + Bootstrap)                FastAPI (beastborn/ui/web)                    unchanged core
┌──────────────────────────────┐  HTTP/JSON  ┌──────────────────────────────┐
│ index.html                   │────────────▶│ app.py      routes            │
│ js/beastborn.js              │             │ intents.py  JSON → UIIntent   │
│  - start screen              │◀────────────│ serializers render model     │
│  - board renderer (CSS grid) │  state JSON │ sessions.py game_id → session │──▶ Interaction ──▶ GameStateMachine ──▶ CalculationEngine
│  - hover previews (local)    │             │             (lock, TTL)       │      (ui/)          (game/)              (engine/)
└──────────────────────────────┘             └──────────────────────────────┘
```

- **Server owns all state.** That means the game, the selection, the log and error messages. The browser only draws the returned JSON and sends intents.
- **Hover is local.** The state already contains the selected unit's reachable tiles (with cost and path) and the attack previews for valid targets. The browser shows move costs, paths and exact damage without extra requests.
- **One request = one intent = one fresh state.** No WebSockets and no polling. This is enough for hot-seat play.

### 1.1 Files

```
beastborn/ui/
├─ interface.py            # unchanged (intents reused)
├─ interaction.py          # unchanged
├─ text.py                 # unchanged
└─ web/
   ├─ __init__.py
   ├─ app.py               # create_app(): FastAPI app, routes, static mount
   ├─ sessions.py          # GameSession + SessionStore (in-memory, per-game lock, TTL)
   ├─ intents.py           # request JSON -> ClickTile / EndTurn / Cancel
   ├─ schemas.py           # pydantic request models (NewGameRequest, IntentRequest)
   ├─ serializers.py       # Interaction -> render-model dict
   └─ static/
      ├─ index.html
      ├─ css/beastborn.css
      ├─ js/beastborn.js
      ├─ img/units/        boss.svg, big_rat.svg, peasant.svg
      ├─ img/terrain/      grass.svg, swamp.svg, hills.svg
      ├─ img/effects/      venom.svg, acid.svg
      ├─ vendor/           jquery, bootstrap, bootstrap-icons (pinned, downloaded)
      └─ CREDITS.md        asset licences + attribution
tools/
└─ fetch_assets.py         # re-downloads vendor libs + icons (pinned URLs)
main.py                    # python main.py [--host --port --reload] -> uvicorn
tests/
├─ test_web_api.py
├─ test_web_sessions.py
└─ test_web_serializers.py
```

---

## 2. Decisions (proposed defaults)

| # | Question | Proposed default |
|---|---|---|
| W1 | Where does the selection state live? | **Server**, in the existing `Interaction` (one per game). Click rules stay the same as the desktop client, with no duplicated logic in JS. |
| W2 | Hover previews | Computed in the browser from the state JSON. Damage preview only for enemies **in range** (`attack_targets`). The desktop client also previewed out-of-range enemies; that is dropped to keep the API small. |
| W3 | Session storage | **In-memory** `dict[game_id → GameSession]`. Run one uvicorn worker. Games are lost when the server restarts (documented). |
| W4 | Game id | `uuid4().hex` (32 chars). It goes in the URL as `/?game=<id>`, so refresh, bookmark and "send link" all work. |
| W5 | Expiry | A game is removed after **2 h** without requests. At most **500** games at once; above that, creating a game returns `503`. Both limits are env vars. |
| W6 | Access control | None. Anyone with the link plays all seats (hot-seat), same as the desktop client. |
| W7 | Graphics | **game-icons.net** SVGs (CC BY 3.0, attribution required) for units, terrain decoration and effects. They are tinted per player with CSS. See section 4. |
| W8 | Libraries | **Downloaded and committed** under `static/vendor/`, so the game runs offline: jQuery 3.7.1, Bootstrap 5.3.3 (CSS + bundle JS), Bootstrap Icons 1.11.3. The alternative is CDN `<link>` tags. |
| W9 | `Frontend` / `InputAdapter` ABCs in `interface.py` | **Kept unchanged.** They suit loop-based clients (console). The web client is request-driven, so it reuses the intent classes but not those two ABCs. |
| W10 | Start options | Players 2–4 (default 2), optional seed, map size Small 10 / Normal 12 / Large 16. |
| W11 | pygame | Removed from `requirements.txt` and `pyproject.toml` together with `ui/pygame_ui/`. |
| W12 | Bots | Not in scope. The hook stays: after each intent the server calls `Interaction.tick()` until a human turn or game over. |

---

## 3. Backend (FastAPI)

### 3.1 Sessions (`sessions.py`)

```python
@dataclass
class GameSession:
    id: str
    interaction: Interaction          # wraps the GameStateMachine
    lock: threading.Lock              # sync endpoints run in a threadpool
    created_at: float
    last_access: float

class SessionStore:
    def __init__(self, ttl_seconds=7200, max_sessions=500, clock=time.monotonic): ...
    def create(self, gsm: GameStateMachine) -> GameSession   # purges expired first; 503 when full
    def get(self, game_id: str) -> GameSession | None        # touches last_access; None if expired
    def delete(self, game_id: str) -> None
```

- The store is created in `create_app()` and kept in `app.state.sessions`. Tests create their own app, so nothing is global.
- `clock` is injectable, so expiry can be tested without sleeping.

### 3.2 API

| Method | Path | Body | Returns |
|---|---|---|---|
| `GET` | `/` | – | `index.html` |
| `POST` | `/api/games` | `{"players": 2, "seed": null, "size": 12}` | `201` + state |
| `GET` | `/api/games/{id}` | – | state, or `404` (unknown or expired) |
| `POST` | `/api/games/{id}/intents` | `{"type": "click", "x": 3, "y": 5}` · `{"type": "end_turn"}` · `{"type": "cancel"}` | state (rejected moves come back as `message.error = true`, still `200`) |
| `DELETE` | `/api/games/{id}` | – | `204` |

- Validation by pydantic: `players` 2–4, `size` ∈ {10, 12, 16}, coordinates inside the board. Invalid input returns `422`.
- `intents.py` maps the JSON to the **existing** intent classes:
  - `click` → `ClickTile(Position(x, y))`
  - `end_turn` → `EndTurn()`
  - `cancel` → `Cancel()`
- The endpoint body is just:

  ```python
  with session.lock:
      session.interaction.handle(intent)
      return render(session)
  ```

- Game creation uses `new_game(players, seed, GameConfig(width=size, height=size))`, an existing function.

### 3.3 State JSON (`serializers.py`)

```json
{
  "game_id": "3f2c…",
  "seed": 42, "round": 3, "phase": "awaiting_command", "active_player": 1, "winner": null,
  "board": { "width": 12, "height": 12,
             "tiles": [[{"terrain": "grass", "elevation": 0}, …], …] },
  "players": [{"index": 0, "name": "Player 1", "eliminated": false, "units": 5}, …],
  "units": [{"id": 7, "owner": 1, "type": "big_rat", "name": "Big Rat", "code": "R", "boss": false,
             "x": 6, "y": 5, "hp": 6, "max_hp": 10, "energy": 4, "max_en": 10, "reg_en": 6,
             "atk": 2, "en_atk": 2, "def": 1, "current_def": 0, "range": 1,
             "effects": [{"kind": "venom", "magnitude": 1, "turns": 2}], "on_hit": ["venom"]}],
  "selection": { "unit_id": 2,
                 "reachable": [{"x": 3, "y": 1, "cost": 4, "path": [[2,0],[2,1],[3,1]]}],
                 "targets":   [{"unit_id": 7, "damage": 4, "formula": "4 +0 - 0 = 4", "blocked": false}] },
  "message": {"text": "", "error": false},
  "log": ["--- Round 3: Player 2 ---", "P1 Big Rat hit P2 Big Rat for 4 [4 +0 - 0 = 4], HP 6"]
}
```

- Every field comes from `Interaction.view`, `.reachable`, `.targets`, `.selected`, `.message` and `.log`. Nothing is recomputed.
- The log is limited to the last 50 lines.
- The board is resent each time (about 150 small objects). That's simpler than caching, and the size is negligible.

---

## 4. Graphics

The download sources were checked from this environment.

| Use | Icon (game-icons.net) | Author |
|---|---|---|
| Boss | `ogre` | Delapouite |
| Big Rat | `rat` | Delapouite |
| Peasant | `farmer` | Delapouite |
| Grass decoration | `grass` | Delapouite |
| Mud decoration | `swamp` | Delapouite |
| Elevation marker | `hills` | Delapouite |
| Venom badge | `poison-bottle` | Lorc |
| Acid badge (future) | `acid` | game-icons.net |

- **Source:** `https://raw.githubusercontent.com/game-icons/icons/master/<author>/<name>.svg`. Fallback: the npm package `@iconify-json/game-icons` via jsDelivr (4,000+ icons).
- **Licence:** CC BY 3.0, so credit is required. The page footer will say: "Icons by Delapouite & Lorc, game-icons.net, CC BY 3.0". `static/CREDITS.md` lists every file.
- **Units:** the SVG is used as a CSS `mask-image` on a coloured disc, so one file serves every player colour. A gold ring marks the Boss.
  - The file name is the unit `key` from `units.json` (`boss`, `big_rat`, `peasant`).
  - A unit type without an image falls back to its letter `code`. New units from `units.json` still render before they get art.
- **Terrain:** a flat colour per terrain and elevation (same palette as the pygame theme), plus the terrain icon at about 20 % opacity and one or two `hills` pips for elevation 1/2. This stays readable at small tile sizes.
- **Why not Kenney (CC0):** the *Tiny Dungeon* / *Tiny Town* packs would be a nicer pixel-art upgrade. kenney.nl and opengameart.org are blocked from this environment, though. They can be dropped into `img/` later without code changes, as long as the file names match.

---

## 5. Frontend (jQuery + Bootstrap)

### 5.1 Page layout (`index.html`)

- **Navbar:** "Beastborn", a **New game** button, and a game id badge with a copy-link button.
- **Start screen** (Bootstrap card, shown when there is no `?game=`):
  - players: radio buttons 2/3/4
  - size: Small / Normal / Large
  - optional seed
  - big **Start** button
- **Game screen:** `row` with two columns.
  - Left (`col-lg-8`): the board, a CSS grid `repeat(width, var(--tile))`. `--tile` is computed so the board fits the viewport (32–64 px).
  - Right (`col-lg-4`): a stack of cards.
    1. **Turn:** round, active player colour swatch, player list (units left / eliminated).
    2. **Selected unit:** HP / EN bars (Bootstrap `progress`), ATK × EN_ATK, DEF / current DEF, range, effects.
    3. **Hover:** tile terrain + elevation, unit stats, move cost or exact damage formula.
    4. **Log:** scrollable `list-group`, newest at the bottom.
    5. **End turn (E)** button.
- **Errors** (`message.error`) appear as a Bootstrap **toast**.
- **Game over:** a Bootstrap **modal** with the winner and **New game**.
- **Footer:** icon credits.

### 5.2 Board rendering

- Full re-render of the grid on every state response. It's only about 144 `div`s, so no diffing is needed.
- Tile classes:
  - terrain and elevation: `terrain-grass`, `terrain-mud`, `elev-0/1/2`
  - selection: `reachable` (+ cost label), `target` (red frame), `selected` (yellow frame), `on-path` (set on hover)
- Units go inside their tile: tinted icon, two thin bars (HP green, EN blue) and a venom badge.

### 5.3 Input → API

| User action | Request |
|---|---|
| Left click tile/unit | `POST intents {type:"click", x, y}` |
| Right click / Esc | `POST intents {type:"cancel"}` |
| E / Space / End turn button | `POST intents {type:"end_turn"}` |
| Mouse over tile | none (local hover from state) |
| Start | `POST /api/games`, then `history.replaceState(?game=id)` |
| Page load with `?game=id` | `GET /api/games/{id}`; on `404`, a toast "Game expired" and the start screen |

Input is disabled while a request is in flight, so double clicks don't send two commands. All of the above lives in about one 300-line `beastborn.js`: an `api` object, a `render(state)` function and event handlers.

---

## 6. Milestones

| # | Work | Done when |
|---|---|---|
| W0 | **Remove the desktop client:** delete `ui/pygame_ui/`, the two pygame tests in `tests/test_ui.py` (frontend smoke test, mouse/keyboard translation) and `pygame-ce` from requirements. Rewrite `test_main_parses_arguments` for the new `main.py` options. Add `fastapi`, `uvicorn[standard]` and `httpx` (TestClient). | `pytest` green, no `pygame` anywhere. |
| W1 | **Backend:** `sessions.py`, `intents.py`, `schemas.py`, `serializers.py`, `app.py`. New `main.py` (`python main.py --port 8000 [--reload]`). | API tests green. Swagger works at `/docs`. |
| W2 | **Assets:** `tools/fetch_assets.py` (pinned URLs). Download the vendor libs and 8 icons into `static/`. Write `CREDITS.md`. | Page loads with no external requests. |
| W3 | **Frontend:** `index.html`, `beastborn.css`, `beastborn.js`. | Manual checklist below passes in Chrome and Firefox. |
| W4 | **Docs:** README (run, controls, API), `docs/architecture.md` (web diagram), screenshot. | — |

### Tests

- `test_web_sessions.py`:
  - create/get/delete
  - two games are independent
  - TTL expiry with a fake clock
  - `max_sessions` limit
- `test_web_api.py` (FastAPI `TestClient`):
  - `POST /api/games` returns an id and the state shape. Two POSTs give different ids and boards (different seeds).
  - Same seed gives the same board.
  - click own unit selects it (`selection.reachable` not empty); click a reachable tile moves; click an enemy in range attacks (HP drops by `targets[].damage`); `end_turn` changes the active player.
  - A rejected action returns `message.error = true`, and the state is unchanged.
  - Unknown id → `404`, bad body → `422`. `GET /` serves the HTML, and every file it references exists in `static/`.
  - Game over: a session is injected with `custom_game` (a weak Boss), and the state reports `phase = game_over` and the winner.
- `test_web_serializers.py`: the render model is JSON-serialisable and matches `gsm.reachable_tiles` / `attack_targets`.
- `test_architecture.py` (extended):
  - `fastapi` / `starlette` only inside `ui/web`
  - `pygame` nowhere
  - `domain` / `engine` / `game` / `control` rules unchanged
- **Existing 100+ tests stay green unchanged.** Only the pygame-specific tests are removed.

### Manual checklist (W3)

- [ ] Open `http://127.0.0.1:8000`, press **Start**. The URL now has `?game=…` and the board shows 2 armies in opposite corners.
- [ ] Open a second tab and press **Start**: a different game id and map. Both games play independently.
- [ ] Refreshing a tab keeps its game.
- [ ] Select a unit to see the reachable tiles with costs. Hover shows the path. Clicking moves the unit, and EN drops by the cost.
- [ ] Hovering an enemy in range shows the exact damage. Clicking attacks, and the log shows the formula.
- [ ] An invalid click (enemy out of range) shows a toast, and nothing changes.
- [ ] E / Space / button ends the turn. Venom ticks show in the log.
- [ ] Killing a Boss in a 2-player game opens the winner modal. **New game** starts a fresh game.
- [ ] 4-player game: all corners are used and eliminated players are skipped.

---

## 7. Definition of Done

- [ ] `python main.py` → open `http://127.0.0.1:8000` → **Start** → a full game is playable in the browser. No pygame window and no pygame dependency.
- [ ] Several games run at once, each under its own id. They expire after 2 h idle.
- [ ] `git diff 7dc60ef -- beastborn/domain beastborn/engine beastborn/game beastborn/control` is **empty** (no game-logic changes).
- [ ] `ui/interface.py`, `ui/interaction.py` and `ui/text.py` are unchanged and used by the web backend.
- [ ] Graphics and libraries are served locally. The licence attribution is shown on the page and in `CREDITS.md`.
- [ ] `pytest` is green.

---

## 8. Risks and notes

- **In-memory sessions:** a restart loses games, and more than one uvicorn worker would split them. Both are fine for this scope. A later step could save `seed + command_log` (already recorded by the GSM) to disk and replay it on startup.
- **No auth:** whoever has the link controls all players. That's intended for hot-seat play, but it isn't safe on a public network.
- **CC BY 3.0 attribution** must stay visible if the game is published. Kenney CC0 art would remove that requirement (W7).
- **Out-of-range attack preview** is dropped (W2). If you miss it, a `GET /api/games/{id}/preview?attacker=&target=` endpoint can bring it back without touching game logic.
