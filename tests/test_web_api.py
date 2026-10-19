"""Web API tests (Flask test client - no browser needed)."""
import re
from dataclasses import replace
from pathlib import Path

import pytest

from beastborn import constant as C
from beastborn.constant import STATIC_DIR, UNITS_PER_PLAYER
from beastborn.domain import Board
from beastborn.game import custom_game
from beastborn.control import BotPlayerController
from beastborn.ui.web.app import create_app
from beastborn.ui.web.sessions import SessionStore
from tests.conftest import KING, SOLDIER

pytestmark = pytest.mark.usefixtures("scenario_rules")  # tests/conftest.py


@pytest.fixture
def store():
    return SessionStore(max_sessions=5)


@pytest.fixture
def client(store):
    return create_app(store, bot_factory=lambda rng: BotPlayerController(depth=1, rng=rng)).test_client()  # quick bot


def new(client, **body):
    response = client.post("/api/games", json={"players": 2, "seed": 42, **body})
    assert response.status_code == 201, response.text
    return response.json


def intent(client, game_id, **body):
    response = client.post(f"/api/games/{game_id}/intents", json=body)
    assert response.status_code == 200, response.text
    return response.json


def active_unit(state):
    return next(u for u in state["units"] if u["id"] == state["active_unit"])


# ---------------------------------------------------------------- games
def test_create_game_state_shape(client):
    state = new(client)
    assert re.fullmatch(r"[0-9a-f]{32}", state["game_id"])
    assert state["phase"] == "awaiting_command"
    assert state["active_player"] == 0 and state["round"] == 1 and state["seed"] == 42
    assert state["board"]["width"] == C.DEFAULT_MAP_SIZE and len(state["board"]["tiles"]) == C.DEFAULT_MAP_SIZE
    assert state["board"]["tiles"][0][0] == {"terrain": "grass", "elevation": 0}
    assert len(state["units"]) == 2 * (1 + UNITS_PER_PLAYER)
    assert {"id", "owner", "type", "x", "y", "hp", "max_hp", "energy", "current_def", "effects"} <= set(state["units"][0])
    unit = active_unit(state)  # the game picks the unit; at the start it can only move
    start = min(unit["max_en"], C.STARTING_ENERGY)
    assert unit["owner"] == 0 and unit["energy"] == min(unit["max_en"], start + unit["reg_en"])
    assert state["reachable"] and state["targets"] == []
    assert state["message"] == {"text": "", "error": False}


def test_options_players_and_size(client):
    state = new(client, players=C.MAX_PLAYERS, size=max(C.MAP_SIZES))
    assert state["board"]["width"] == max(C.MAP_SIZES)
    assert len(state["players"]) == C.MAX_PLAYERS and len(state["units"]) == C.MAX_PLAYERS * (1 + UNITS_PER_PLAYER)


def test_many_games_at_once(client):
    a, b = new(client, seed=1), new(client, seed=2)
    assert a["game_id"] != b["game_id"]
    assert a["board"] != b["board"]
    intent(client, a["game_id"], type="end_turn")
    assert client.get(f"/api/games/{a['game_id']}").json["active_player"] == 1
    assert client.get(f"/api/games/{b['game_id']}").json["active_player"] == 0


def test_same_seed_same_board(client):
    assert new(client, seed=5)["board"] == new(client, seed=5)["board"]


def test_random_seed_when_missing(client):
    state = client.post("/api/games", json={}).json
    assert isinstance(state["seed"], int)


@pytest.mark.parametrize(
    "body",
    [{"players": C.MIN_PLAYERS - 1}, {"players": C.MAX_PLAYERS + 1}, {"size": max(C.MAP_SIZES) + 1}, {"seed": -1},
     {"seed": C.MAX_SEED + 1}, {"players": "many"}],
)
def test_invalid_new_game(client, body):
    assert client.post("/api/games", json=body).status_code == 422


def test_unknown_game(client):
    assert client.get("/api/games/nope").status_code == 404
    assert client.post("/api/games/nope/intents", json={"type": "end_turn"}).status_code == 404
    assert client.delete("/api/games/nope").status_code == 404


def test_delete_game(client):
    gid = new(client)["game_id"]
    assert client.delete(f"/api/games/{gid}").status_code == 204
    assert client.get(f"/api/games/{gid}").status_code == 404


def test_too_many_games(client):
    for _ in range(5):
        new(client)
    assert client.post("/api/games", json={}).status_code == 503


def test_error_bodies_are_json(client):
    """The page reads errors as {"detail": str} or {"detail": [{"msg": ...}, ...]} (beastborn.js errorText)."""
    response = client.get("/api/games/nope")
    assert response.status_code == 404
    assert response.json == {"detail": "Game not found or expired"}

    response = client.post("/api/games", json={"players": 9})
    assert response.status_code == 422
    detail = response.json["detail"]
    assert isinstance(detail, list) and detail
    assert detail[0]["loc"] == ["body", "players"] and detail[0]["msg"]


def test_non_json_body_is_422(client):
    assert client.post("/api/games", data="x").status_code == 422
    gid = new(client)["game_id"]
    assert client.post(f"/api/games/{gid}/intents", data="x").status_code == 422


# ---------------------------------------------------------------- intents
def test_move_the_active_unit(client):
    state = new(client)
    unit = active_unit(state)
    dest = state["reachable"][0]
    assert dest["path"][0] == [unit["x"], unit["y"]] and dest["path"][-1] == [dest["x"], dest["y"]]

    state = intent(client, state["game_id"], type="click", x=dest["x"], y=dest["y"])
    moved = next(u for u in state["units"] if u["id"] == unit["id"])
    assert (moved["x"], moved["y"]) == (dest["x"], dest["y"])
    assert moved["energy"] == unit["energy"] - dest["cost"]
    assert any("moved" in line for line in state["log"])


def test_end_turn_passes_to_the_next_players_unit(client):
    state = new(client)
    first = state["active_unit"]
    state = intent(client, state["game_id"], type="end_turn")
    assert state["active_player"] == 1 and active_unit(state)["owner"] == 1
    assert state["log"][-1].endswith("waits") and state["active_unit"] != first


def test_other_own_unit_cannot_be_chosen(client):
    state = new(client)
    other = next(u for u in state["units"] if u["owner"] == 0 and u["id"] != state["active_unit"])
    after = intent(client, state["game_id"], type="click", x=other["x"], y=other["y"])
    assert after["message"]["error"] and "now it is" in after["message"]["text"]
    assert after["active_unit"] == state["active_unit"] and after["units"] == state["units"]


def test_rejected_action_reports_error_and_keeps_state(client):
    state = new(client)
    enemy = next(u for u in state["units"] if u["owner"] == 1)
    before = {u["id"]: (u["x"], u["y"], u["hp"], u["energy"]) for u in state["units"]}
    state = intent(client, state["game_id"], type="click", x=enemy["x"], y=enemy["y"])  # far away
    assert state["message"]["error"] and "range" in state["message"]["text"]
    assert {u["id"]: (u["x"], u["y"], u["hp"], u["energy"]) for u in state["units"]} == before


@pytest.mark.parametrize(
    "body",
    [{"type": "jump"}, {"type": "cancel"}, {"type": "click"}, {"type": "click", "x": C.DEFAULT_MAP_SIZE, "y": 0},
     {"type": "click", "x": -1, "y": 0}],
)
def test_invalid_intents(client, body):
    gid = new(client)["game_id"]
    assert client.post(f"/api/games/{gid}/intents", json=body).status_code == 422


# ---------------------------------------------------------------- combat & game over (hand-made game)
def inject(store, gsm) -> str:
    return store.create(gsm).id


def test_attack_and_game_over(client, store):
    board = Board.from_strings([".....", ".....", "....."])
    weak_king = replace(KING, hp=3)
    gsm = custom_game(board, [(0, SOLDIER, (1, 1)), (0, KING, (0, 0)), (1, weak_king, (2, 1)), (1, SOLDIER, (4, 2))])
    gid = inject(store, gsm)

    state = client.get(f"/api/games/{gid}").json
    assert state["active_unit"] == 1
    assert state["targets"] == [
        {"unit_id": 3, "damage": 3, "formula": "4 - 1 = 3", "blocked": False, "move": None}
    ]
    state = intent(client, gid, type="click", x=2, y=1)
    assert state["phase"] == "game_over" and state["winner"] == 0
    assert state["players"][1]["eliminated"] and state["players"][1]["units"] == 0
    assert state["log"][-1] == "Player 1 wins!"
    state = intent(client, gid, type="end_turn")  # ignored after game over
    assert state["phase"] == "game_over"


# ---------------------------------------------------------------- page & static files
def test_index_and_all_referenced_static_files_exist(client):
    response = client.get("/")
    assert response.status_code == 200 and "Beastborn" in response.text
    html = response.text
    css = (STATIC_DIR / "css" / "beastborn.css").read_text(encoding="utf-8")
    js = (STATIC_DIR / "js" / "beastborn.js").read_text(encoding="utf-8")
    referenced = set(re.findall(r'(?:src|href)="/static/([^"]+)"', html))
    referenced |= set(re.findall(r"url\(/static/([^)]+)\)", html + css))
    referenced |= {f"img/units/{name}.svg" for name in re.findall(r'"(boss|big_rat|peasant|archer)"', js)}
    assert referenced, "nothing found - regexes broken?"
    for rel in sorted(referenced):
        assert (STATIC_DIR / rel).is_file(), rel
        assert client.get(f"/static/{rel}").status_code == 200, rel


def test_index_shows_name_and_version(client):
    from beastborn.constant import GAME_NAME, GAME_TITLE, GAME_VERSION

    html = client.get("/").text
    assert f"<title>{GAME_TITLE}</title>" in html
    assert GAME_NAME in html and f"v{GAME_VERSION}" in html
    assert "{{" not in html, "a template placeholder was left unfilled"


def test_every_roster_unit_has_a_sprite():
    from beastborn.domain import load_roster

    for unit in load_roster():
        assert (STATIC_DIR / "img" / "units" / f"{unit.key}.svg").is_file(), unit.key


def test_credits_cover_all_icons():
    credits = (STATIC_DIR / "CREDITS.md").read_text(encoding="utf-8")
    for svg in Path(STATIC_DIR, "img").rglob("*.svg"):
        assert svg.relative_to(STATIC_DIR).as_posix() in credits, svg


# ---------------------------------------------------------------- bot & WatchState
def bot_step(client, game_id):
    response = client.post(f"/api/games/{game_id}/bot-step")
    assert response.status_code == 200, response.text
    return response.json


def test_new_game_with_a_bot(client):
    state = new(client, bots=[1])
    assert [p["bot"] for p in state["players"]] == [False, True]
    assert state["players"][1]["name"] == C.BOT_NAME_TEMPLATE.format(number=2)
    assert state["human_turn"] and not state["watching"]
    assert not any(p["bot"] for p in new(client)["players"])  # the API adds no bot unless asked


@pytest.mark.parametrize(
    "body",
    [{"bots": [0, 1]}, {"bots": [1, 1]}, {"bots": [2]}, {"bots": [-1]}, {"players": 3, "bots": [1]}, {"bots": "x"}],
)
def test_invalid_bots(client, body):
    response = client.post("/api/games", json={"players": 2, **body})
    assert response.status_code == 422 and response.json["detail"][0]["msg"]


def test_watch_the_bot_play(client):
    state = new(client, bots=[1])
    state = intent(client, state["game_id"], type="end_turn")
    assert state["watching"] and not state["human_turn"] and state["active_player"] == 1
    assert state["reachable"] == [] and state["targets"] == []  # no previews for the bot's unit
    frozen = intent(client, state["game_id"], type="end_turn")  # input is ignored while watching
    assert (frozen["active_unit"], frozen["units"]) == (state["active_unit"], state["units"])

    speech = []
    for _ in range(2 * (1 + UNITS_PER_PLAYER)):
        if not state["watching"]:
            break
        state = bot_step(client, state["game_id"])
        speech += [s for s in state["actions"]["steps"] if s["kind"] == "say"]
    assert not state["watching"] and state["human_turn"] and state["active_player"] == 0
    assert speech and speech[0]["player"] == 1 and speech[0]["text"]
    assert any(line.startswith(f'{state["players"][1]["name"]}: "') for line in state["log"])


def test_bot_in_the_first_seat_starts(client):
    state = new(client, bots=[0])
    assert state["watching"] and state["active_player"] == 0
    state = bot_step(client, state["game_id"])
    assert state["actions"]["steps"][-1]["kind"] == "say"


def test_same_seed_same_bot_moves(client):
    first, second = new(client, bots=[0]), new(client, bots=[0])  # seed 42 for both: the bot's rng too
    assert bot_step(client, first["game_id"])["units"] == bot_step(client, second["game_id"])["units"]


def test_bot_step_on_a_human_turn_changes_nothing(client):
    state = new(client, bots=[1])
    after = bot_step(client, state["game_id"])
    assert (after["active_unit"], after["units"], after["actions"]["seq"]) == (
        state["active_unit"], state["units"], state["actions"]["seq"]
    )
    assert client.post("/api/games/nope/bot-step").status_code == 404
