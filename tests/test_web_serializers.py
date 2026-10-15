import json

from beastborn.constant import LOG_LINES
from beastborn.domain import EffectKind, Position, StatusEffect
from beastborn.game import new_game
from beastborn.ui.interaction import Interaction
from beastborn.ui.interface import ClickTile
from beastborn.ui.web.serializers import render


def test_render_is_json_and_matches_gsm():
    gsm = new_game(3, seed=11)
    ui = Interaction(gsm)
    unit_id = gsm.active_unit

    state = json.loads(json.dumps(render("abc", ui)))
    assert state["game_id"] == "abc"
    assert (state["active_player"], state["active_unit"]) == (0, unit_id)
    reach = {(r["x"], r["y"]): r["cost"] for r in state["reachable"]}
    assert reach and reach == {(p.x, p.y): info.cost for p, info in gsm.reachable_tiles(unit_id).items()}
    assert [t["unit_id"] for t in state["targets"]] == sorted(gsm.attack_plans(unit_id))
    for row_index, row in enumerate(state["board"]["tiles"]):
        for col_index, tile in enumerate(row):
            real = gsm.view().board.tile(Position(col_index, row_index))
            assert tile == {"terrain": real.terrain.value, "elevation": real.elevation}


def test_effects_and_log_limit():
    gsm = new_game(2, seed=1)
    gsm._state.units[2].effects = (StatusEffect(EffectKind.VENOM, 1, 2),)
    ui = Interaction(gsm)
    ui.log.extend(f"line {i}" for i in range(100))
    state = render("x", ui)
    unit = next(u for u in state["units"] if u["id"] == 2)
    assert unit["effects"] == [{"kind": "venom", "magnitude": 1, "turns": 2}]
    assert len(state["log"]) == LOG_LINES and state["log"][-1] == "line 99"


def test_attacks_are_listed_for_animation():
    from beastborn.domain import Board
    from beastborn.game import custom_game
    from tests.conftest import ARCHER, KING, SOLDIER

    board = Board.from_strings(["......", "......"])
    gsm = custom_game(board, [(0, ARCHER, (0, 0)), (1, SOLDIER, (2, 0)), (0, KING, (0, 1)), (1, KING, (5, 1))])
    ui = Interaction(gsm)
    assert render("x", ui)["actions"] == {"seq": 0, "steps": []}
    assert render("x", ui)["active_unit"] == 1  # the Archer acts first

    ui.handle(ClickTile(Position(2, 0)))  # shoot: 8 / RP 1 - DEF 1 = 7
    state = render("x", ui)
    assert state["actions"] == {"seq": 1, "steps": [{
        "kind": "attack", "seq": 1, "attacker_id": 1, "target_id": 2, "attacker_type": ARCHER.key, "owner": 0,
        "from": [0, 0], "to": [2, 0], "ranged": True, "damage": 7, "killed": False,
    }]}
    assert next(u for u in state["units"] if u["id"] == 1)["move_penalty"] == 1
    assert state["log"][-1].startswith("P1 Archer shot P2 Soldier for 7")

    ui.handle(ClickTile(Position(2, 0)))  # 3 HP left -> killed; position comes from before the hit
    state = render("x", ui)
    attacks = state["actions"]["steps"]
    assert len(attacks) == 1  # only the attacks since the last input are kept and sent
    assert (attacks[0]["seq"], attacks[0]["to"], attacks[0]["killed"]) == (2, [2, 0], True)
    assert state["active_unit"] == 4  # the Archer can do nothing more: P2's King acts

    ui.handle(ClickTile(Position(5, 1)))  # the active unit's own tile: no command, list is cleared
    assert render("x", ui)["actions"] == {"seq": 2, "steps": []}


def test_targets_after_a_move_carry_the_move_and_the_walk_is_animated():
    from beastborn.domain import Board
    from beastborn.game import custom_game
    from beastborn.ui.interface import EndTurn
    from tests.conftest import KING, SOLDIER

    board = Board.from_strings([".....", ".....", "....."])
    gsm = custom_game(board, [(0, KING, (0, 0)), (0, SOLDIER, (0, 1)), (1, KING, (4, 2)), (1, SOLDIER, (3, 1))])
    ui = Interaction(gsm)
    ui.handle(EndTurn())  # both Kings wait: the Soldier (2) is next
    ui.handle(EndTurn())
    assert render("x", ui)["targets"] == [{
        "unit_id": 4, "damage": 3, "formula": "4 - 1 = 3", "blocked": False,
        "move": {"x": 2, "y": 1, "cost": 4, "path": [[0, 1], [1, 1], [2, 1]]},
    }]
    ui.handle(ClickTile(Position(3, 1)))  # walks to (2,1), then hits
    walk, attack = render("x", ui)["actions"]["steps"]  # in order: the walk, then the hit
    assert walk == {"kind": "move", "seq": 3, "unit_id": 2, "owner": 0, "path": [[0, 1], [1, 1], [2, 1]]}
    assert (attack["kind"], attack["seq"], attack["from"], attack["to"]) == ("attack", 3, [2, 1], [3, 1])
