# Beastborn

Turn-based tactics for 2–4 hot-seat players. Beasts fight on a random battlefield with mud and hills.
**Combat has zero randomness.** What happens depends only on energy, position and unit stats.

Made with fun by Patryk Motyczyński.

![Beastborn in the browser](docs/screenshot.png)

## Run

```bash
python -m venv venv
venv\Scripts\activate            # Windows  (Linux/macOS: source venv/bin/activate)
pip install -r requirements.txt

python main.py                   # then open http://127.0.0.1:8000 and press Start
python main.py --port 9000 --reload   # other port, auto-restart on code changes
```

Requires Python 3.10+ and Flask. Choose players (2–4), map size and an optional seed, then press **Start**.
Every game gets its own id in the URL (`/?game=<id>`). Refreshing the page keeps the game, and several games can run at once.
Games live in server memory. They are lost when the server stops and expire after 2 h without activity.
Settings: `BEASTBORN_SESSION_TTL` (seconds, default 7200) and `BEASTBORN_MAX_GAMES` (default 500).

## Deploy on PythonAnywhere

`gercio_eu_pythonanywhere_com_wsgi.py` is the production entry point. PythonAnywhere imports it and serves its `application`. You don't start it yourself.

1. Locally: `python beast_build.py` creates `beast.zip`, which holds the game, the entry points, `requirements.txt` and the installer.
2. Upload `beast.zip` to `/home/gercio` on the Files tab. The first time, also upload `beast_install.py`. After that, every install updates it from the zip.
3. In a PythonAnywhere Bash console: `python3.10 beast_install.py`. Use the same Python version the Web tab shows.
   It unpacks the zip, runs `pip install --user -r requirements.txt` and checks that the new version starts. Then it puts the new version into `/home/gercio/mysite`, keeps the previous one in `mysite_backup`, copies the WSGI file to `/var/www/` and reloads the site. If a step fails before the switch, the live site stays as it was.
4. Something wrong? Run `python3.10 beast_install.py --rollback` to go back to the previous version. Run it again to go forward.

Options: `--skip-pip` skips the pip step when the dependencies haven't changed. The project folder comes from `SERVER_PROJECT_HOME` in the WSGI file.
If the `API_TOKEN` environment variable is set, the installer also checks the web app's Python version and reloads the site through the PythonAnywhere API. Without it, the changed WSGI file reloads the site within about a minute, or you can press **Reload** on the Web tab.
Optional, but faster: Web tab → **Static files**: URL `/static/`, directory `/home/gercio/mysite/beastborn/ui/web/static`.

Games are kept in the memory of one server process. If your plan runs more than one web worker, games will randomly show "not found", so ask PythonAnywhere support to set the app to 1 worker. A reload or worker restart drops all running games.

To smoke-test the production entry point locally: `python gercio_eu_pythonanywhere_com_wsgi.py` (same options as `main.py`).

## Test

```bash
python -m pytest
```

## Rules in short

- **Win:** kill every enemy Boss. If your Boss dies, your whole army leaves the battlefield.
- **Turn:** at the start of your turn each of your units regenerates `REG_EN` energy (up to `MAX_EN`), then status effects tick (Venom).
  Units start a game with 0 energy. Then use your units in any order and end the turn.
- **Movement** (4 directions, units block tiles):

  | Step into… | Cost |
  |---|---|
  | grass, same level | 2 EN |
  | mud, same level | 3 EN |
  | each level uphill | +2 EN |
  | downhill into grass | 2 EN |
  | downhill into mud | 5 EN |
  | slow units (Archer) | +`move_penalty` EN per step |

- **Attack** (costs `EN_ATK`, repeat while energy lasts; full rules in [docs/game_mechanics.md](docs/game_mechanics.md)):
  `DMG = floor(ATK × (1 + 0.5 × EM) × RP − DEF)`, minimum 1 for every hit.
  - `EM` = your level minus the target's level. Each level above adds +50 % ATK, each level below takes −50 %.
  - `RP` only applies to ranged units (range > 1) and depends only on distance: 1 tile ×0.5, 2 tiles ×1, 3 tiles ×0.8, 4 tiles ×0.8, farther is impossible.
  - Units standing in mud have half DEF (rounded down).
- **Effects** (applied on every hit): Venom = lose `max(1, ATK ÷ 4)` HP at the start of each of your turns, for 5 rounds (re-applying refreshes it).
  Acid = −`max(1, ATK ÷ 10)` DEF permanently per hit.

| Unit | HP | ATK | EN_ATK | DEF | MAX_EN | REG_EN | Range | Special |
|---|---|---|---|---|---|---|---|---|
| Boss | 40 | 8 | 3 | 4 | 6 | 3 | 1 | slow, huge HP |
| Big Rat | 12 | 5 | 2 | 1 | 10 | 6 | 1 | Venom |
| Peasant | 16 | 6 | 3 | 2 | 8 | 5 | 1 | — |
| Archer | 14 | 7 | 3 | 2 | 8 | 5 | 4 | ranged, +1 EN per step; best at 2 tiles, weak next to the target |

Stats are placeholders for balancing. Edit them in `beastborn/data/units.json`.

## Controls (browser)

| Action | Input |
|---|---|
| Select own unit | Left click |
| Move | Left click a highlighted tile (number = EN cost) |
| Attack | Left click an enemy with a red frame (hover shows exact damage). Arrows and melee hits are animated |
| Deselect | Right click / Esc |
| Preview | Hover: path + move cost, or exact damage on a target |
| End turn | E / Space / button |

## Project layout

```
beastborn/
  domain/    pure data: Position, Tile, Board, UnitStats/UnitState, effects, roster loader
  engine/    Calculation Engine (CE): interface + StandardCalculationEngine + RulesConfig
  game/      Game State Machine (GSM): state, commands, events, pathfinding, map generator, setup
  control/   player controllers (human now, bots later)
  ui/        frontend-neutral intents + presenter (interface.py, interaction.py, text.py)
    web/     Flask backend (sessions, JSON API) + static/ page (jQuery + Bootstrap)
  data/      units.json
tests/       pytest suite (engine, GSM, map, UI, web API, architecture rules)
tools/       fetch_assets.py - re-downloads vendor libraries and icons
docs/        architecture.md
main.py      entry point (local web server)
gercio_eu_pythonanywhere_com_wsgi.py   PythonAnywhere WSGI entry point (production)
beast_build.py / beast_install.py      build beast.zip locally / install it on PythonAnywhere
```

## Web API

| Method | Path | Body |
|---|---|---|
| `POST` | `/api/games` | `{"players": 2, "size": 12, "seed": null}` → new game state |
| `GET` | `/api/games/{id}` | → state |
| `POST` | `/api/games/{id}/intents` | `{"type": "click", "x": 3, "y": 5}` / `{"type": "end_turn"}` / `{"type": "cancel"}` → state |
| `DELETE` | `/api/games/{id}` | → 204 |

## Credits

Icons by Delapouite, Lorc & Sbed from [game-icons.net](https://game-icons.net) (CC BY 3.0).
jQuery, Bootstrap and Bootstrap Icons (MIT). See `beastborn/ui/web/static/CREDITS.md`.

See [docs/architecture.md](docs/architecture.md) for how to swap the rules or the UI.
