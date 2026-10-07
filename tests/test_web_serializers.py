import json

from beastborn.domain import EffectKind, Position, StatusEffect
from beastborn.game import new_game
from beastborn.ui.interaction import Interaction
from beastborn.ui.interface import ClickTile
from beastborn.ui.web.serializers import LOG_LINES, render


def test_render_is_json_and_matches_gsm():
    gsm = new_game(3, seed=11)
    ui = Interaction(gsm)
    unit = next(u for u in gsm.view().units_of(0) if gsm.reachable_tiles(u.id))
    ui.handle(ClickTile(unit.position))

    state = json.loads(json.dumps(render("abc", ui)))
    assert state["game_id"] == "abc"
    reach = {(r["x"], r["y"]): r["cost"] for r in state["selection"]["reachable"]}
    assert reach == {(p.x, p.y): info.cost for p, info in gsm.reachable_tiles(unit.id).items()}
    assert [t["unit_id"] for t in state["selection"]["targets"]] == sorted(gsm.attack_targets(unit.id))
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
