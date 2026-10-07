"""Web API tests (FastAPI TestClient - no browser needed)."""
import re
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from beastborn.domain import Board
from beastborn.game import custom_game
from beastborn.ui.web.app import STATIC_DIR, create_app
from beastborn.ui.web.sessions import SessionStore
from tests.conftest import KING, SOLDIER


@pytest.fixture
def store():
    return SessionStore(max_sessions=5)


@pytest.fixture
def client(store):
    return TestClient(create_app(store))


def new(client, **body):
    response = client.post("/api/games", json={"players": 2, "seed": 42, **body})
    assert response.status_code == 201, response.text
    return response.json()


def intent(client, game_id, **body):
    response = client.post(f"/api/games/{game_id}/intents", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def own_unit_with_moves(client, state):
    """Select units of the active player until one can move; return (state, unit)."""
    for unit in state["units"]:
        if unit["owner"] != state["active_player"]:
            continue
        state = intent(client, state["game_id"], type="click", x=unit["x"], y=unit["y"])
        if state["selection"]["reachable"]:
            return state, unit
    raise AssertionError("no movable unit")


# ---------------------------------------------------------------- games
def test_create_game_state_shape(client):
    state = new(client)
    assert re.fullmatch(r"[0-9a-f]{32}", state["game_id"])
    assert state["phase"] == "awaiting_command"
    assert state["active_player"] == 0 and state["round"] == 1 and state["seed"] == 42
    assert state["board"]["width"] == 12 and len(state["board"]["tiles"]) == 12
    assert state["board"]["tiles"][0][0] == {"terrain": "grass", "elevation": 0}
    assert len(state["units"]) == 10
    assert {"id", "owner", "type", "x", "y", "hp", "max_hp", "energy", "current_def", "effects"} <= set(state["units"][0])
    assert state["selection"] == {"unit_id": None, "reachable": [], "targets": []}
    assert state["message"] == {"text": "", "error": False}


def test_options_players_and_size(client):
    state = new(client, players=4, size=16)
    assert state["board"]["width"] == 16
    assert len(state["players"]) == 4 and len(state["units"]) == 20


def test_many_games_at_once(client):
    a, b = new(client, seed=1), new(client, seed=2)
    assert a["game_id"] != b["game_id"]
    assert a["board"] != b["board"]
    intent(client, a["game_id"], type="end_turn")
    assert client.get(f"/api/games/{a['game_id']}").json()["active_player"] == 1
    assert client.get(f"/api/games/{b['game_id']}").json()["active_player"] == 0


def test_same_seed_same_board(client):
    assert new(client, seed=5)["board"] == new(client, seed=5)["board"]


def test_random_seed_when_missing(client):
    state = client.post("/api/games", json={}).json()
    assert isinstance(state["seed"], int)


@pytest.mark.parametrize(
    "body", [{"players": 1}, {"players": 5}, {"size": 11}, {"seed": -1}, {"players": "many"}]
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


# ---------------------------------------------------------------- intents
def test_select_move_and_end_turn(client):
    state, unit = own_unit_with_moves(client, new(client))
    assert state["selection"]["unit_id"] == unit["id"]
    dest = state["selection"]["reachable"][0]
    assert dest["path"][0] == [unit["x"], unit["y"]] and dest["path"][-1] == [dest["x"], dest["y"]]

    state = intent(client, state["game_id"], type="click", x=dest["x"], y=dest["y"])
    moved = next(u for u in state["units"] if u["id"] == unit["id"])
    assert (moved["x"], moved["y"]) == (dest["x"], dest["y"])
    assert moved["energy"] == unit["energy"] - dest["cost"]
    assert any("moved" in line for line in state["log"])

    state = intent(client, state["game_id"], type="end_turn")
    assert state["active_player"] == 1
    assert state["selection"]["unit_id"] is None


def test_cancel_clears_selection(client):
    state, _ = own_unit_with_moves(client, new(client))
    state = intent(client, state["game_id"], type="cancel")
    assert state["selection"]["unit_id"] is None


def test_rejected_action_reports_error_and_keeps_state(client):
    state = new(client)
    enemy = next(u for u in state["units"] if u["owner"] == 1)
    state, _ = own_unit_with_moves(client, state)
    before = {u["id"]: (u["x"], u["y"], u["hp"], u["energy"]) for u in state["units"]}
    state = intent(client, state["game_id"], type="click", x=enemy["x"], y=enemy["y"])  # far away
    assert state["message"]["error"] and "range" in state["message"]["text"]
    assert {u["id"]: (u["x"], u["y"], u["hp"], u["energy"]) for u in state["units"]} == before


@pytest.mark.parametrize(
    "body",
    [{"type": "jump"}, {"type": "click"}, {"type": "click", "x": 12, "y": 0}, {"type": "click", "x": -1, "y": 0}],
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

    state = intent(client, gid, type="click", x=1, y=1)
    assert state["selection"]["targets"] == [{"unit_id": 3, "damage": 3, "formula": "4 +0 - 1 = 3", "blocked": False}]
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
    referenced |= {f"img/units/{name}.svg" for name in re.findall(r'"(boss|big_rat|peasant)"', js)}
    assert referenced, "nothing found - regexes broken?"
    for rel in sorted(referenced):
        assert (STATIC_DIR / rel).is_file(), rel
        assert client.get(f"/static/{rel}").status_code == 200, rel


def test_every_roster_unit_has_a_sprite():
    from beastborn.domain import load_roster

    for unit in load_roster():
        assert (STATIC_DIR / "img" / "units" / f"{unit.key}.svg").is_file(), unit.key


def test_credits_cover_all_icons():
    credits = (STATIC_DIR / "CREDITS.md").read_text(encoding="utf-8")
    for svg in Path(STATIC_DIR, "img").rglob("*.svg"):
        assert svg.relative_to(STATIC_DIR).as_posix() in credits, svg
