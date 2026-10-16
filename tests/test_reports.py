"""Balance report (beastborn/reports/report.py): fight simulation, matrix and a GUI smoke test."""
import json
from dataclasses import replace
from fractions import Fraction

import pytest

pytest.importorskip("tkinter")  # the report is a tkinter app; tkinter ships with the standard Windows Python

from beastborn import constant as C  # noqa: E402
from beastborn.domain import EffectKind, EffectSpec, TerrainType  # noqa: E402
from beastborn.engine import StandardCalculationEngine  # noqa: E402
from beastborn.engine.results import fmt  # noqa: E402
from beastborn.reports import report  # noqa: E402
from beastborn.reports.report import (  # noqa: E402
    Scenario, Side, StepKind, attack_matrix, best_distance, legal_distances, pick_distance, simulate,
)
from tests.conftest import (  # noqa: E402
    ARCHER, FIXED_ARCHER, FIXED_BIG_RAT, FIXED_BOSS, FIXED_PEASANT, FIXED_UNITS, KING, SOLDIER, acid_shred,
    first_turn_energy, hit_damage, mud_defense, range_multiplier, venom_per_round, write_units_file,
)

pytestmark = pytest.mark.usefixtures("scenario_rules")  # tests/conftest.py

CE = StandardCalculationEngine()
K = Fraction(str(C.ELEVATION_COEF))  # damage bonus per elevation level


def hp_trace(fight):
    return [s.target_hp_after for s in fight.steps if s.kind in (StepKind.HIT, StepKind.VENOM)]


def legal(stats):
    """Distances a unit can attack from (ranged: every distance of the range table within its range)."""
    return [d for d in sorted(C.RANGE_DISSIPATION) if d <= stats.attack_range] if stats.is_ranged else [1]


def best(stats):
    """Distance with the strongest range multiplier; on a tie the nearer one."""
    return min(legal(stats), key=lambda d: (-range_multiplier(d), d))


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
    boss_fight = simulate(Scenario(Side(roster[C.UNIT_BOSS]), Side(roster[C.UNIT_BOSS])))
    rat_fight = simulate(Scenario(Side(roster[C.UNIT_BIG_RAT]), Side(roster[C.UNIT_BOSS])))

    def hits_in_round(fight, round_):
        return sum(1 for s in fight.steps if s.kind is StepKind.HIT and s.round == round_)

    boss, rat = roster[C.UNIT_BOSS], roster[C.UNIT_BIG_RAT]
    assert hits_in_round(boss_fight, 1) == first_turn_energy(boss) // boss.en_atk
    assert hits_in_round(rat_fight, 1) == first_turn_energy(rat) // rat.en_atk
    first_turn = rat_fight.steps[1]
    regen = first_turn_energy(rat) - min(rat.max_en, C.STARTING_ENERGY)
    assert first_turn.kind is StepKind.REGEN and first_turn.event == f"turn start +{regen} EN"
    assert first_turn.attacker.energy == first_turn_energy(rat)


def test_venom_ticks_at_target_turn_start(roster):
    rat, boss = roster[C.UNIT_BIG_RAT], roster[C.UNIT_BOSS]
    fight = simulate(Scenario(Side(rat), Side(boss)))
    kinds = [s.kind for s in fight.steps if s.round == 1]
    assert kinds == [StepKind.REGEN] + [StepKind.HIT] * (first_turn_energy(rat) // rat.en_atk) + [StepKind.VENOM]
    venom = next(s for s in fight.steps if s.kind is StepKind.VENOM)
    assert venom.damage == venom_per_round(rat.atk)
    assert venom.target.effects[0].kind is EffectKind.VENOM
    assert fight.venom_damage > 0 and fight.venom_damage % venom_per_round(rat.atk) == 0
    assert fight.hit_damage + fight.venom_damage == boss.hp + fight.overkill  # every HP is accounted for


def test_acid_lowers_target_def():
    acid = replace(SOLDIER, key="acid", atk=10, on_hit=(EffectSpec(EffectKind.ACID),))
    target = replace(KING, defense=2, hp=40)
    shred = acid_shred(acid.atk)
    fight = simulate(Scenario(Side(acid), Side(target)))
    defenses = [s.target.defense for s in fight.steps if s.kind is StepKind.HIT and s.target]
    assert defenses[:3] == [max(0, target.defense - shred * hits) for hits in (1, 2, 3)]
    first_hit = next(s for s in fight.steps if s.kind is StepKind.HIT)
    assert f"acid DEF {target.defense} → {max(0, target.defense - shred)}" in first_hit.calculation


def test_terrain_and_height_change_damage(roster):
    scenario = Scenario(
        Side(roster[C.UNIT_ARCHER], elevation=1), Side(roster[C.UNIT_BOSS], TerrainType.MUD), distance=3
    )
    fight = simulate(scenario)
    hit = next(s for s in fight.steps if s.kind is StepKind.HIT)
    archer, rp, defense = roster[C.UNIT_ARCHER], range_multiplier(3), mud_defense(roster[C.UNIT_BOSS].defense)
    raw = archer.atk * (1 + K) * rp - defense
    assert hit.calculation.startswith(f"{archer.atk} x{fmt(1 + K)} height x{fmt(rp)} range - {defense} = {fmt(raw)}")
    assert hit.damage == hit_damage(raw)
    assert hit.result.formula() == hit.calculation
    assert hit.target.current_defense == defense


def test_matrix_uses_tab1_conditions(roster):
    grass = attack_matrix(roster)
    mud = attack_matrix(roster, target_terrain=TerrainType.MUD)
    for pair, fight in grass.items():
        first_hit = next(s for s in fight.steps if s.kind is StepKind.HIT)
        first_hit_mud = next(s for s in mud[pair].steps if s.kind is StepKind.HIT)
        assert first_hit_mud.damage > first_hit.damage, pair
    uphill = attack_matrix(roster, target_elevation=2)  # 2 levels up: momentum = ATK * (1 - 2 * ELEVATION_COEF)
    for pair, fight in uphill.items():
        hit = next(s for s in fight.steps if s.kind is StepKind.HIT)
        flat_hit = next(s for s in grass[pair].steps if s.kind is StepKind.HIT)
        assert hit.result.momentum == max(0, hit.result.atk * (1 - 2 * K)), pair
        assert hit.damage <= flat_hit.damage, pair


def test_distances(roster, monkeypatch):
    assert legal_distances(roster[C.UNIT_PEASANT], CE) == [1]
    assert legal_distances(roster[C.UNIT_ARCHER], CE) == legal(roster[C.UNIT_ARCHER])
    assert best_distance(roster[C.UNIT_PEASANT], CE) == 1
    assert best_distance(roster[C.UNIT_ARCHER], CE) == best(roster[C.UNIT_ARCHER])
    short_bow = replace(ARCHER, attack_range=2)
    assert pick_distance(short_bow, CE, 4) == 2 and pick_distance(short_bow, CE, None) == 2
    with pytest.raises(ValueError):
        simulate(Scenario(Side(SOLDIER), Side(SOLDIER), distance=2))
    monkeypatch.setattr(C, "RANGE_DISSIPATION", {1: 0.5, 2: 1.0, 3: 1.0, 4: 0.8})  # tie: the nearer distance wins
    assert best_distance(ARCHER, CE) == 2


def test_round_cap(monkeypatch):
    monkeypatch.setattr(C, "ALLOW_BLOCK", True)
    weak = replace(SOLDIER, atk=1)
    fight = simulate(Scenario(Side(weak), Side(KING)), CE, max_rounds=5)
    assert not fight.killed
    assert fight.rounds == 5
    assert fight.hp_left == KING.hp
    assert fight.summary().startswith("alive after 5 rounds")


def test_matrix_has_every_pair(roster):
    fights = attack_matrix(roster)
    assert len(fights) == len(roster) ** 2
    assert all(f.killed for f in fights.values())
    assert fights[(C.UNIT_ARCHER, C.UNIT_BOSS)].scenario.distance == best(roster[C.UNIT_ARCHER])
    assert fights[(C.UNIT_BOSS, C.UNIT_ARCHER)].scenario.distance == 1
    assert attack_matrix(roster, ranged_distance=1)[(C.UNIT_ARCHER, C.UNIT_BOSS)].scenario.distance == 1


# The matrix for the FIXED_* test units (tests/conftest.py), not for units.json, worked out with the rule numbers
# in MATRIX_RULES: rebalancing units.json or constant.py does not change it. Both units on grass, height 0,
# Archer at its best distance (2 tiles). (rounds, hits) per attacker -> target.
MATRIX_RULES = {
    "STARTING_ENERGY": 0,
    "MIN_DAMAGE": 1,
    "VENOM_ATK_DIVISOR": 2,
    "RANGE_DISSIPATION": {1: 0.5, 2: 1.0, 3: 0.8, 4: 0.8},
}
EXPECTED_MATRIX = {
    C.UNIT_BOSS: {C.UNIT_BOSS: (40, 40), C.UNIT_BIG_RAT: (5, 5), C.UNIT_PEASANT: (6, 6), C.UNIT_ARCHER: (5, 5)},
    C.UNIT_BIG_RAT: {C.UNIT_BOSS: (8, 24), C.UNIT_BIG_RAT: (2, 5), C.UNIT_PEASANT: (2, 5), C.UNIT_ARCHER: (2, 4)},
    C.UNIT_PEASANT: {C.UNIT_BOSS: (12, 20), C.UNIT_BIG_RAT: (3, 4), C.UNIT_PEASANT: (3, 4), C.UNIT_ARCHER: (3, 4)},
    C.UNIT_ARCHER: {C.UNIT_BOSS: (9, 14), C.UNIT_BIG_RAT: (3, 4), C.UNIT_PEASANT: (3, 4), C.UNIT_ARCHER: (2, 3)},
}


def test_matrix_rounds_and_hits(roster, monkeypatch):
    for name, value in MATRIX_RULES.items():
        monkeypatch.setattr(C, name, value)
    fights = attack_matrix(roster)
    actual = {a: {t: (fights[a, t].rounds, fights[a, t].hits) for t in row} for a, row in EXPECTED_MATRIX.items()}
    assert actual == EXPECTED_MATRIX


def test_step_rows_for_the_tables():
    fight = simulate(Scenario(Side(SOLDIER), Side(SOLDIER)))
    start, energy, top = min(SOLDIER.max_en, C.STARTING_ENERGY), first_turn_energy(SOLDIER), SOLDIER.max_en
    damage = SOLDIER.atk - SOLDIER.defense
    assert report.step_row(fight.steps[1]) == (
        "R1", f"turn start +{energy - start} EN", f"EN {start} → {energy} (max {top})", "10", f"{energy}/{top}",
    )
    assert report.step_row(fight.steps[2]) == (
        "R1", "hit", f"4 - 1 = {damage}", f"10 → {10 - damage}", f"{energy - SOLDIER.en_atk}/{top}",
    )
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

    units_file = write_units_file(tmp_path / "units.json")
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
    assert len(tab.fights) == len(FIXED_UNITS) ** 2
    def cell(pair):
        fight = tab.fights[pair]
        return tab.cells[pair].cget("text"), f"{fight.rounds} ({fight.hits})"

    text, expected = cell((C.UNIT_BIG_RAT, C.UNIT_BOSS))
    assert text == expected
    assert len(tab.tree.get_children()) == len(FIXED_UNITS) ** 2
    boss_on_grass = tab.fights[(C.UNIT_BOSS, C.UNIT_BOSS)].hits
    tab.target_terrain.set("mud")  # recalculates every pair
    text, expected = cell((C.UNIT_BOSS, C.UNIT_BOSS))
    assert text == expected
    assert tab.fights[(C.UNIT_BOSS, C.UNIT_BOSS)].hits < boss_on_grass  # half DEF in mud: fewer hits to kill
    tab.select((C.UNIT_BIG_RAT, C.UNIT_BOSS), scroll=True, open_pair=True)
    assert tab.chart.fight is tab.fights[(C.UNIT_BIG_RAT, C.UNIT_BOSS)]
    tab.ranged.set("1")
    assert tab.fights[(C.UNIT_ARCHER, C.UNIT_BOSS)].scenario.distance == 1


def test_gui_simulation_tab_stepping(app):
    tab = app.simulation_tab
    app.notebook.select(tab)
    tab.attacker.set(FIXED_ARCHER.name)
    tab.attacker_height.set("1")
    tab.target.set(FIXED_BOSS.name)
    tab.target_terrain.set("mud")
    tab.distance.set("3")
    assert tab.fight.scenario.distance == 3
    assert tab.index == 0
    tab.buttons["step"].invoke()
    tab.buttons["step"].invoke()
    assert tab.index == 2
    first_hit = tab.fight.steps[2]  # start, turn start, hit
    assert first_hit.kind is StepKind.HIT
    assert tab.target_panel.values["HP"].cget("text").startswith(f"{first_hit.target_hp_after} / {FIXED_BOSS.hp}")
    tab.buttons["back"].invoke()
    assert tab.index == 1
    tab.buttons["end"].invoke()
    assert tab.fight.steps[tab.index].killed
    assert tab.buttons["step"].instate(["disabled"])
    tab.buttons["reset"].invoke()
    assert tab.index == 0
    tab.attacker.set(FIXED_PEASANT.name)  # melee: only distance 1
    assert tab.distance.get() == "1"
    assert list(tab.distance_box.cget("values")) == ["1"]


def test_gui_refresh_reloads_units_and_keeps_old_data_on_error(app):
    data = json.loads(app.units_file_path.read_text(encoding="utf-8"))
    next(u for u in data["units"] if u["key"] == C.UNIT_BIG_RAT)["atk"] = 6
    app.units_file_path.write_text(json.dumps(data), encoding="utf-8")
    app.simulation_tab.attacker.set(FIXED_BIG_RAT.name)
    assert app.refresh()
    assert app.report_tab.roster[C.UNIT_BIG_RAT].atk == 6
    assert app.simulation_tab.attacker.get() == FIXED_BIG_RAT.name  # the choice survives a refresh
    assert app.simulation_tab.fight.scenario.attacker.stats.atk == 6

    app.units_file_path.write_text("{ broken", encoding="utf-8")
    assert not app.refresh()
    assert len(app.errors) == 1
    assert app.report_tab.roster[C.UNIT_BIG_RAT].atk == 6  # old data kept
