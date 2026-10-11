"""Balance report (beastborn/reports/report.py): fight simulation, matrix and a GUI smoke test."""
import json
import shutil
from dataclasses import replace

import pytest

pytest.importorskip("tkinter")  # the report is a tkinter app; tkinter ships with the standard Windows Python

from beastborn.domain import EffectKind, EffectSpec, TerrainType  # noqa: E402
from beastborn.engine import RulesConfig, StandardCalculationEngine  # noqa: E402
from beastborn.reports import report  # noqa: E402
from beastborn.reports.report import (  # noqa: E402
    Scenario, Side, StepKind, attack_matrix, best_distance, legal_distances, pick_distance, simulate,
)
from tests.conftest import ARCHER, KING, SOLDIER  # noqa: E402

CE = StandardCalculationEngine()


def hp_trace(fight):
    return [s.target_hp_after for s in fight.steps if s.kind in (StepKind.HIT, StepKind.VENOM)]


# ---------------------------------------------------------------- one fight
def test_melee_fight_hand_calculated():
    # Soldier: ATK 4, DEF 1, 6 EN per turn, 2 EN per hit -> 3 hits per round of 4 - 1 = 3 damage.
    fight = simulate(Scenario(Side(SOLDIER), Side(SOLDIER)))
    assert hp_trace(fight) == [7, 4, 1, 0]
    assert [s.calculation for s in fight.steps if s.kind is StepKind.HIT] == ["4 - 1 = 3"] * 4
    assert (fight.killed, fight.rounds, fight.hits) == (True, 2, 4)
    assert (fight.hit_damage, fight.venom_damage, fight.overkill) == (10, 0, 2)
    assert fight.steps[0].kind is StepKind.START and fight.steps[0].target_hp_after == 10
    assert fight.steps[-1].killed and fight.steps[-1].target is None
    assert fight.summary() == "2 rounds, 4 hits"


def test_regen_limits_hits_per_round(roster):
    boss_fight = simulate(Scenario(Side(roster["boss"]), Side(roster["boss"])))
    rat_fight = simulate(Scenario(Side(roster["big_rat"]), Side(roster["boss"])))

    def hits_in_round(fight, round_):
        return sum(1 for s in fight.steps if s.kind is StepKind.HIT and s.round == round_)

    assert hits_in_round(boss_fight, 1) == 1  # reg 3, 3 EN per hit
    assert hits_in_round(rat_fight, 1) == 3  # reg 6, 2 EN per hit
    first_turn = rat_fight.steps[1]
    assert first_turn.kind is StepKind.REGEN and first_turn.event == "turn start +6 EN"
    assert first_turn.attacker.energy == 6


def test_venom_ticks_at_target_turn_start(roster):
    fight = simulate(Scenario(Side(roster["big_rat"]), Side(roster["boss"])))
    kinds = [s.kind for s in fight.steps if s.round == 1]
    assert kinds == [StepKind.REGEN, StepKind.HIT, StepKind.HIT, StepKind.HIT, StepKind.VENOM]
    venom = next(s for s in fight.steps if s.kind is StepKind.VENOM)
    assert venom.damage == max(1, roster["big_rat"].atk // 4)
    assert venom.target.effects[0].kind is EffectKind.VENOM
    assert (fight.rounds, fight.hits, fight.venom_damage) == (10, 30, 10)


def test_acid_lowers_target_def():
    acid = replace(SOLDIER, key="acid", atk=10, on_hit=(EffectSpec(EffectKind.ACID),))  # 1 DEF per hit
    target = replace(KING, defense=2, hp=40)
    fight = simulate(Scenario(Side(acid), Side(target)))
    defenses = [s.target.defense for s in fight.steps if s.kind is StepKind.HIT and s.target]
    assert defenses[:3] == [1, 0, 0]
    assert "acid DEF 2 → 1" in next(s for s in fight.steps if s.kind is StepKind.HIT).calculation


def test_terrain_and_height_change_damage(roster):
    scenario = Scenario(Side(roster["archer"], elevation=1), Side(roster["boss"], TerrainType.MUD), distance=3)
    fight = simulate(scenario)
    hit = next(s for s in fight.steps if s.kind is StepKind.HIT)
    assert hit.calculation == "7 x1.5 height x0.8 range - 2 = 6.4 -> 6"
    assert hit.result.formula() == hit.calculation
    assert hit.target.current_defense == 2


def test_matrix_uses_tab1_conditions(roster):
    grass = attack_matrix(roster)
    mud = attack_matrix(roster, target_terrain=TerrainType.MUD)
    for pair, fight in grass.items():
        first_hit = next(s for s in fight.steps if s.kind is StepKind.HIT)
        first_hit_mud = next(s for s in mud[pair].steps if s.kind is StepKind.HIT)
        assert first_hit_mud.damage > first_hit.damage, pair
    uphill = attack_matrix(roster, target_elevation=2)  # 2 levels up: momentum 0, every hit deals the minimum 1
    assert all(s.damage == 1 for f in uphill.values() for s in f.steps if s.kind is StepKind.HIT)


def test_distances(roster):
    assert legal_distances(roster["peasant"], CE) == [1]
    assert legal_distances(roster["archer"], CE) == [1, 2, 3, 4]
    assert best_distance(roster["peasant"], CE) == 1
    assert best_distance(roster["archer"], CE) == 2
    tie = StandardCalculationEngine(RulesConfig(range_dissipation={1: 0.5, 2: 1.0, 3: 1.0, 4: 0.8}))
    assert best_distance(ARCHER, tie) == 2
    short_bow = replace(ARCHER, attack_range=2)
    assert pick_distance(short_bow, CE, 4) == 2 and pick_distance(short_bow, CE, None) == 2
    with pytest.raises(ValueError):
        simulate(Scenario(Side(SOLDIER), Side(SOLDIER), distance=2))


def test_round_cap():
    engine = StandardCalculationEngine(RulesConfig(allow_block=True))
    weak = replace(SOLDIER, atk=1)
    fight = simulate(Scenario(Side(weak), Side(KING)), engine, max_rounds=5)
    assert not fight.killed
    assert fight.rounds == 5
    assert fight.hp_left == KING.hp
    assert fight.summary().startswith("alive after 5 rounds")


def test_matrix_has_every_pair(roster):
    fights = attack_matrix(roster)
    assert len(fights) == len(roster) ** 2
    assert all(f.killed for f in fights.values())
    assert fights[("archer", "boss")].scenario.distance == 2
    assert fights[("boss", "archer")].scenario.distance == 1
    assert attack_matrix(roster, ranged_distance=1)[("archer", "boss")].scenario.distance == 1


# Update this table when you rebalance beastborn/data/units.json: it is what the report shows today
# (both units on grass, height 0, Archer at 2 tiles). (rounds, hits) per attacker -> target.
CURRENT_BALANCE = {
    "boss": {"boss": (10, 10), "big_rat": (2, 2), "peasant": (3, 3), "archer": (3, 3)},
    "big_rat": {"boss": (10, 30), "big_rat": (1, 3), "peasant": (2, 5), "archer": (2, 5)},
    "peasant": {"boss": (12, 20), "big_rat": (2, 3), "peasant": (3, 4), "archer": (3, 4)},
    "archer": {"boss": (9, 14), "big_rat": (2, 2), "peasant": (3, 4), "archer": (2, 3)},
}


def test_matrix_matches_current_balance(roster):
    fights = attack_matrix(roster)
    actual = {a: {t: (fights[a, t].rounds, fights[a, t].hits) for t in row} for a, row in CURRENT_BALANCE.items()}
    assert actual == CURRENT_BALANCE


def test_step_rows_for_the_tables():
    fight = simulate(Scenario(Side(SOLDIER), Side(SOLDIER)))
    assert report.step_row(fight.steps[1]) == ("R1", "turn start +6 EN", "EN 0 → 6 (max 10)", "10", "6/10")
    assert report.step_row(fight.steps[2]) == ("R1", "hit", "4 - 1 = 3", "10 → 7", "4/10")
    assert report.step_row(fight.steps[-1])[1] == "hit - dead"
    assert report.step_tag(fight.steps[-1]) == "dead"


def test_heat_colour():
    assert report.heat_colour(1, 1, 10) == report.HEAT_SCALE[0]
    assert report.heat_colour(10, 1, 10) == report.HEAT_SCALE[-1]
    assert report.heat_colour(5, 5, 5) == report.HEAT_SCALE[0]


# ---------------------------------------------------------------- GUI
@pytest.fixture
def app(tmp_path, monkeypatch):
    import tkinter as tk

    units_file = tmp_path / "units.json"
    shutil.copy(report.DEFAULT_UNITS_FILE, units_file)
    errors = []
    monkeypatch.setattr(report.messagebox, "showerror", lambda *args, **kwargs: errors.append(args))
    try:
        window = report.ReportApp(units_file)
    except tk.TclError as error:  # no display (e.g. a headless server)
        pytest.skip(f"no display: {error}")
    window.withdraw()
    window.errors = errors
    window.units_file_path = units_file
    yield window
    window.destroy()


def test_gui_report_tab(app):
    tab = app.report_tab
    app.update()
    assert len(tab.fights) == 16
    assert tab.cells[("big_rat", "boss")].cget("text") == "10 (30)"
    assert len(tab.tree.get_children()) == 16
    tab.target_terrain.set("mud")  # recalculates every pair
    assert tab.cells[("boss", "boss")].cget("text") == "7 (7)"  # 8 - 2 = 6 per hit, 40 HP
    tab.select(("big_rat", "boss"), scroll=True, open_pair=True)
    assert tab.chart.fight is tab.fights[("big_rat", "boss")]
    tab.ranged.set("1")
    assert tab.fights[("archer", "boss")].scenario.distance == 1


def test_gui_simulation_tab_stepping(app):
    tab = app.simulation_tab
    app.notebook.select(tab)
    tab.attacker.set("Archer")
    tab.attacker_height.set("1")
    tab.target.set("Boss")
    tab.target_terrain.set("mud")
    tab.distance.set("3")
    assert tab.fight.scenario.distance == 3
    assert tab.index == 0
    tab.buttons["step"].invoke()
    tab.buttons["step"].invoke()
    assert tab.index == 2
    assert tab.target_panel.values["HP"].cget("text").startswith("34 / 40")
    tab.buttons["back"].invoke()
    assert tab.index == 1
    tab.buttons["end"].invoke()
    assert tab.fight.steps[tab.index].killed
    assert tab.buttons["step"].instate(["disabled"])
    tab.buttons["reset"].invoke()
    assert tab.index == 0
    tab.attacker.set("Peasant")  # melee: only distance 1
    assert tab.distance.get() == "1"
    assert list(tab.distance_box.cget("values")) == ["1"]


def test_gui_refresh_reloads_units_and_keeps_old_data_on_error(app):
    data = json.loads(app.units_file_path.read_text(encoding="utf-8"))
    next(u for u in data["units"] if u["key"] == "big_rat")["atk"] = 6
    app.units_file_path.write_text(json.dumps(data), encoding="utf-8")
    app.simulation_tab.attacker.set("Big Rat")
    assert app.refresh()
    assert app.report_tab.roster["big_rat"].atk == 6
    assert app.simulation_tab.attacker.get() == "Big Rat"  # the choice survives a refresh
    assert app.simulation_tab.fight.scenario.attacker.stats.atk == 6

    app.units_file_path.write_text("{ broken", encoding="utf-8")
    assert not app.refresh()
    assert len(app.errors) == 1
    assert app.report_tab.roster["big_rat"].atk == 6  # old data kept
