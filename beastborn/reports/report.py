"""Beastborn balance report: how the attack mechanic plays out between every pair of units.

Run from the project root::

    python -m beastborn.reports.report

(running this file directly, e.g. with PyCharm's Run button, works too).

* Tab "Attack report": every unit attacks every unit (all pairs) with the terrain and height
  chosen on top. For each pair you see the target's HP after every step until it dies.
* Tab "Attack simulation": one unit against one unit, step by step, with both units' stats.
* "Refresh" (above the tabs) reloads ``beastborn/data/units.json`` and recalculates both tabs.

Fights run on the real Game State Machine (``custom_game`` + ``AttackCommand`` / ``EndTurnCommand``),
so energy regeneration, Venom, Acid and death work exactly as in a game. The attacker stands still
and hits while it has energy, then ends its turn. The target does nothing. Combat has no RNG,
so every fight has exactly one result.

Only the standard library (tkinter) is used. Plan: Prompts/04_report_PLAN.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and not __package__:  # run as a file: make the 'beastborn' package importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import json  # noqa: E402  (imports below the sys.path fix on purpose)
import math  # noqa: E402
import time  # noqa: E402
import tkinter as tk  # noqa: E402
from collections.abc import Callable, Sequence  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from enum import Enum  # noqa: E402
from tkinter import font as tkfont  # noqa: E402
from tkinter import messagebox, ttk  # noqa: E402

from beastborn.constance import DEFAULT_MAX_ELEVATION, DEFAULT_UNITS_FILE, GAME_TITLE  # noqa: E402
from beastborn.domain import Board, Roster, TerrainType, Tile, UnitStats, load_roster  # noqa: E402
from beastborn.engine import AttackResult, CalculationEngine, StandardCalculationEngine  # noqa: E402
from beastborn.game import AttackCommand, EndTurnCommand, GameView, UnitView, custom_game  # noqa: E402
from beastborn.game import events as ev  # noqa: E402

# ============================================================================ constants
REPORT_TITLE: str = f"{GAME_TITLE} - Balance report"
WINDOW_SIZE: str = "1280x880"
MIN_WINDOW_SIZE: tuple[int, int] = (1000, 700)
MAX_ROUNDS: int = 100  # a fight stops here even if the target is still alive (only possible with allow_block)
MAX_STEPS: int = 5000  # safety limit (e.g. a unit with en_atk 0 could attack forever)
TERRAINS: tuple[str, ...] = tuple(t.value for t in TerrainType)  # grass, mud
HEIGHTS: tuple[str, ...] = tuple(str(h) for h in range(DEFAULT_MAX_ELEVATION + 1))  # 0, 1, 2
BEST: str = "best"  # ranged distance with the highest damage multiplier
ATTACKER_ID: int = 1  # custom_game numbers the placements from 1
TARGET_ID: int = 2
ARROW: str = "→"  # →

# colours
CHART_BG: str = "#ffffff"
CHART_GRID: str = "#e3e3e3"
CHART_AXIS: str = "#7a7a7a"
CHART_LINE: str = "#3b6fb6"
HIT_COLOUR: str = "#3b6fb6"
VENOM_COLOUR: str = "#8e44ad"
MARKER_COLOUR: str = "#e67e22"
HP_BAR_COLOUR: str = "#4caf50"
EN_BAR_COLOUR: str = "#f1c40f"
BAR_BG: str = "#e6e6e6"
REGEN_TEXT: str = "#808080"
DEAD_TEXT: str = "#c0392b"
HEAT_SCALE: tuple[str, ...] = ("#b8e0b0", "#f6e7a1", "#f3aaa4")  # fast kill -> slow kill
CELL_SELECTED_BORDER: str = "#1f3f73"


# ============================================================================ simulation (no tkinter here)
@dataclass(frozen=True)
class Side:
    """One unit of a fight and the tile it stands on."""

    stats: UnitStats
    terrain: TerrainType = TerrainType.GRASS
    elevation: int = 0

    def tile(self) -> Tile:
        return Tile(self.terrain, self.elevation)

    def describe(self) -> str:
        return f"{self.terrain.value}, height {self.elevation}"


@dataclass(frozen=True)
class Scenario:
    attacker: Side
    target: Side
    distance: int = 1  # tiles between the two units (1 = next to each other)

    @property
    def title(self) -> str:
        return f"{self.attacker.stats.name} {ARROW} {self.target.stats.name}"


class StepKind(Enum):
    START = "start"
    REGEN = "turn start"  # the attacker's turn starts: energy regeneration
    HIT = "hit"
    VENOM = "venom"  # Venom ticks at the start of the target's turn


@dataclass(frozen=True)
class Step:
    """One row of the report: the state of both units after this step."""

    index: int
    round: int
    kind: StepKind
    event: str  # "hit", "venom - dead", "turn start +6 EN"...
    calculation: str  # AttackResult.formula() for hits
    target_hp_before: int
    target_hp_after: int  # 0 when dead
    damage: int  # HP the target lost in this step (can be more than it had left)
    killed: bool
    attacker: UnitView
    target: UnitView | None  # None once dead
    result: AttackResult | None = None  # only for HIT

    @property
    def overkill(self) -> int:
        return max(0, self.damage - self.target_hp_before) if self.killed else 0


@dataclass(frozen=True)
class Fight:
    scenario: Scenario
    steps: tuple[Step, ...]

    @property
    def killed(self) -> bool:
        return self.steps[-1].killed

    @property
    def rounds(self) -> int:
        return self.steps[-1].round

    @property
    def hits(self) -> int:
        return sum(1 for s in self.steps if s.kind is StepKind.HIT)

    @property
    def hit_damage(self) -> int:
        return sum(min(s.damage, s.target_hp_before) for s in self.steps if s.kind is StepKind.HIT)

    @property
    def venom_damage(self) -> int:
        return sum(min(s.damage, s.target_hp_before) for s in self.steps if s.kind is StepKind.VENOM)

    @property
    def overkill(self) -> int:
        return sum(s.overkill for s in self.steps)

    @property
    def hp_left(self) -> int:
        return self.steps[-1].target_hp_after

    def summary(self) -> str:
        """E.g. '10 rounds, 30 hits' or 'alive after 100 rounds (HP 12), 100 hits'."""
        hits: str = f"{self.hits} hit" + ("" if self.hits == 1 else "s")
        if not self.killed:
            return f"alive after {self.rounds} rounds (HP {self.hp_left}), {hits}"
        return f"{self.rounds} round{'' if self.rounds == 1 else 's'}, {hits}"

    def damage_text(self) -> str:
        return f"hits {self.hit_damage} HP · venom {self.venom_damage} HP · overkill {self.overkill}"

    def next_hit(self, index: int) -> Step | None:
        """The first hit after step ``index`` (combat has no RNG, so this is exactly the next hit)."""
        return next((s for s in self.steps[index + 1:] if s.kind is StepKind.HIT), None)


def legal_distances(stats: UnitStats, engine: CalculationEngine) -> list[int]:
    """Distances the unit can attack from: [1] for melee, e.g. [1, 2, 3, 4] for the Archer."""
    return [d for d in range(1, stats.attack_range + 1) if engine.in_attack_range(stats, d)]


def best_distance(stats: UnitStats, engine: CalculationEngine) -> int:
    """The distance with the highest damage multiplier (RP); on a tie the shortest one."""
    distances: list[int] = legal_distances(stats, engine)
    if not distances:
        raise ValueError(f"{stats.name} cannot attack at any distance")
    return max(distances, key=lambda d: (engine.range_multiplier(stats, d) or 0, -d))


def pick_distance(stats: UnitStats, engine: CalculationEngine, wanted: int | None) -> int:
    """``wanted`` if the unit can attack from there, otherwise its best (None) or farthest legal distance."""
    distances: list[int] = legal_distances(stats, engine)
    if wanted is None or not distances:
        return best_distance(stats, engine)
    if wanted in distances:
        return wanted
    return max(d for d in distances if d <= wanted) if min(distances) <= wanted else min(distances)


def simulate(scenario: Scenario, engine: CalculationEngine | None = None, max_rounds: int = MAX_ROUNDS) -> Fight:
    """Run one fight on the real Game State Machine and record every step."""
    engine = engine or StandardCalculationEngine()
    attacker, target, distance = scenario.attacker, scenario.target, scenario.distance
    if distance not in legal_distances(attacker.stats, engine):
        raise ValueError(f"{attacker.stats.name} cannot attack from {distance} tile(s)")

    # One row: attacker tile, flat grass in between, target tile.
    row: list[Tile] = [attacker.tile()] + [Tile()] * (distance - 1) + [target.tile()]
    gsm = custom_game(
        Board([row]), [(0, attacker.stats, (0, 0)), (1, target.stats, (distance, 0))], engine=engine, start=False
    )
    first: GameView = gsm.view()
    first_attacker: UnitView = _unit(first, ATTACKER_ID)
    steps: list[Step] = [
        Step(
            index=0, round=0, kind=StepKind.START, event="start",
            calculation=f"attacker {attacker.describe()} · target {target.describe()} · distance {distance}",
            target_hp_before=target.stats.hp, target_hp_after=target.stats.hp, damage=0, killed=False,
            attacker=first_attacker, target=first.unit(TARGET_ID),
        )
    ]
    events: Sequence[ev.GameEvent] = gsm.start()
    while gsm.view().round <= max_rounds and len(steps) < MAX_STEPS:
        _record(events, gsm.view(), steps)
        if steps[-1].killed:
            break
        if gsm.active_player == 0:
            result = gsm.submit(AttackCommand(ATTACKER_ID, TARGET_ID))
            if not result.ok:  # not enough energy left: end the turn
                result = gsm.submit(EndTurnCommand())
        else:  # the target does nothing
            result = gsm.submit(EndTurnCommand())
        events = result.events
    return Fight(scenario, tuple(steps))


def _unit(view: GameView, unit_id: int) -> UnitView:
    unit: UnitView | None = view.unit(unit_id)
    if unit is None:
        raise RuntimeError(f"Unit {unit_id} is missing")
    return unit


def _record(events: Sequence[ev.GameEvent], view: GameView, steps: list[Step]) -> None:
    """Turn the events of one command into report steps (snapshots = state after the command)."""
    attacker: UnitView = _unit(view, ATTACKER_ID)
    target: UnitView | None = view.unit(TARGET_ID)
    max_en: int = attacker.stats.max_en

    def add(round_: int, kind: StepKind, event: str, calculation: str, hp_after: int, damage: int,
            result: AttackResult | None = None) -> None:
        hp_before: int = steps[-1].target_hp_after
        killed: bool = hp_after <= 0
        steps.append(Step(
            index=len(steps), round=round_, kind=kind, event=event + (" - dead" if killed else ""),
            calculation=calculation, target_hp_before=hp_before, target_hp_after=max(0, hp_after), damage=damage,
            killed=killed, attacker=attacker, target=target, result=result,
        ))

    for event in events:
        if isinstance(event, ev.TurnStarted) and event.player == 0:
            regen = next((e for e in events if isinstance(e, ev.EnergyRegenerated) and e.unit_id == ATTACKER_ID), None)
            before, after = (regen.before, regen.after) if regen else (attacker.energy, attacker.energy)
            add(event.round, StepKind.REGEN, f"turn start +{after - before} EN",
                f"EN {before} {ARROW} {after} (max {max_en})", steps[-1].target_hp_after, 0)
        elif isinstance(event, ev.UnitAttacked) and event.target_id == TARGET_ID:
            r: AttackResult = event.result
            calculation: str = r.formula()
            if r.defense_change:
                old_def: int = steps[-1].target.defense if steps[-1].target else 0
                calculation += f"; acid DEF {old_def} {ARROW} {old_def + r.defense_change}"
            add(view.round, StepKind.HIT, "hit", calculation, event.target_hp_after, r.damage, r)
        elif isinstance(event, ev.EffectTicked) and event.unit_id == TARGET_ID:
            add(view.round, StepKind.VENOM, event.kind.value, f"{event.amount} HP (ignores DEF)",
                event.hp_after, event.amount)


def attack_matrix(
    roster: Roster,
    attacker_terrain: TerrainType = TerrainType.GRASS,
    attacker_elevation: int = 0,
    target_terrain: TerrainType = TerrainType.GRASS,
    target_elevation: int = 0,
    ranged_distance: int | None = None,
    engine: CalculationEngine | None = None,
) -> dict[tuple[str, str], Fight]:
    """Every unit attacks every unit (same type included), keyed by (attacker key, target key).

    Melee units attack from 1 tile. Ranged units attack from ``ranged_distance``
    (None = their best distance; a unit that cannot shoot that far uses its farthest distance).
    """
    engine = engine or StandardCalculationEngine()
    fights: dict[tuple[str, str], Fight] = {}
    for attacker in roster:
        distance: int = pick_distance(attacker, engine, ranged_distance) if attacker.is_ranged else 1
        for target in roster:
            scenario = Scenario(
                Side(attacker, attacker_terrain, attacker_elevation), Side(target, target_terrain, target_elevation),
                distance,
            )
            fights[(attacker.key, target.key)] = simulate(scenario, engine)
    return fights


def step_row(step: Step) -> tuple[str, str, str, str, str]:
    """Round | Event | Calculation | Target HP | Attacker EN, as shown in both tabs."""
    hp: str = (
        str(step.target_hp_after) if step.kind in (StepKind.START, StepKind.REGEN)
        else f"{step.target_hp_before} {ARROW} {step.target_hp_after}"
    )
    round_: str = "" if step.kind is StepKind.START else f"R{step.round}"
    return round_, step.event, step.calculation, hp, f"{step.attacker.energy}/{step.attacker.stats.max_en}"


def step_tag(step: Step) -> str:
    if step.killed:
        return "dead"
    return {StepKind.REGEN: "regen", StepKind.VENOM: "venom", StepKind.START: "regen"}.get(step.kind, "hit")


def display_names(roster: Roster) -> dict[str, str]:
    """Unit key -> name shown in the window (the key is added only if two units share a name)."""
    names: list[str] = [u.name for u in roster]
    return {u.key: u.name if names.count(u.name) == 1 else f"{u.name} [{u.key}]" for u in roster}


# ============================================================================ widgets
_FONTS: dict[tuple[bool, int], tkfont.Font] = {}


def ui_font(bold: bool = False, grow: int = 0) -> tkfont.Font:
    """The system default font, optionally bold and/or ``grow`` points bigger (cached; needs a Tk root)."""
    key: tuple[bool, int] = (bold, grow)
    if key not in _FONTS:
        font: tkfont.Font = tkfont.nametofont("TkDefaultFont").copy()
        size: int = font.cget("size")
        font.configure(weight="bold" if bold else "normal", size=(size - grow if size < 0 else size + grow))
        _FONTS[key] = font
    return _FONTS[key]


class HpChart(tk.Canvas):
    """Target HP after every step of one fight. Dots: hits (blue) and Venom (purple)."""

    PAD_LEFT, PAD_RIGHT, PAD_TOP, PAD_BOTTOM = 46, 18, 44, 34

    def __init__(self, master: tk.Misc, height: int = 260):
        super().__init__(master, background=CHART_BG, highlightthickness=1, highlightbackground=CHART_GRID,
                         height=height, width=420)
        self.fight: Fight | None = None
        self.marker: int | None = None
        self.bind("<Configure>", lambda _e: self.redraw())

    def show(self, fight: Fight | None, marker: int | None = None) -> None:
        self.fight, self.marker = fight, marker
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        width, height = max(self.winfo_width(), 100), max(self.winfo_height(), 100)
        if self.fight is None:
            self.create_text(width / 2, height / 2, text="Select a pair", fill=CHART_AXIS)
            return
        fight: Fight = self.fight
        steps: tuple[Step, ...] = fight.steps
        max_hp: int = max(1, fight.scenario.target.stats.hp)
        left, right = self.PAD_LEFT, width - self.PAD_RIGHT
        top, bottom = self.PAD_TOP, height - self.PAD_BOTTOM
        last: int = max(1, len(steps) - 1)

        def x(i: int) -> float:
            return left + (right - left) * i / last

        def y(hp: int) -> float:
            return bottom - (bottom - top) * hp / max_hp

        self.create_text(left, 12, anchor="w", font=ui_font(bold=True),
                         text=f"HP of {fight.scenario.target.stats.name}")
        for hp in sorted({0, max_hp // 2, max_hp}):  # horizontal grid
            self.create_line(left, y(hp), right, y(hp), fill=CHART_GRID)
            self.create_text(left - 6, y(hp), anchor="e", text=str(hp), fill=CHART_AXIS)
        regen_steps: list[Step] = [s for s in steps if s.kind is StepKind.REGEN]
        max_labels: int = max(1, int((right - left) // 34))  # about 34 px per "R10" label
        label_every: int = max(1, math.ceil(len(regen_steps) / max_labels))
        for n, step in enumerate(regen_steps):  # round borders
            self.create_line(x(step.index), top, x(step.index), bottom, fill=CHART_GRID, dash=(2, 3))
            if n % label_every == 0:
                self.create_text(x(step.index), bottom + 12, text=f"R{step.round}", fill=CHART_AXIS)
        self.create_line(left, bottom, right, bottom, fill=CHART_AXIS)
        self.create_line(left, top, left, bottom, fill=CHART_AXIS)
        self.create_text(right, bottom + 26, anchor="e", text="step", fill=CHART_AXIS)

        points: list[float] = [c for s in steps for c in (x(s.index), y(s.target_hp_after))]
        if len(points) >= 4:
            self.create_line(*points, fill=CHART_LINE, width=2)
        radius: float = 3 if len(steps) < 80 else 2
        for s in steps:
            colour: str | None = {StepKind.HIT: HIT_COLOUR, StepKind.VENOM: VENOM_COLOUR}.get(s.kind)
            if colour:
                cx, cy = x(s.index), y(s.target_hp_after)
                self.create_oval(cx - radius, cy - radius, cx + radius, cy + radius, fill=colour, outline=colour)
        self.create_oval(left, 25, left + 6, 31, fill=HIT_COLOUR, outline=HIT_COLOUR)
        self.create_text(left + 10, 28, anchor="w", text="hit", fill=CHART_AXIS)
        self.create_oval(left + 40, 25, left + 46, 31, fill=VENOM_COLOUR, outline=VENOM_COLOUR)
        self.create_text(left + 50, 28, anchor="w", text="venom", fill=CHART_AXIS)
        if not fight.killed:
            self.create_text(right, 28, anchor="e", fill=DEAD_TEXT, text=f"alive after {fight.rounds} rounds")

        if self.marker is not None and 0 <= self.marker < len(steps):
            step: Step = steps[self.marker]
            mx, my = x(step.index), y(step.target_hp_after)
            self.create_line(mx, top, mx, bottom, fill=MARKER_COLOUR, width=2)
            self.create_oval(mx - 5, my - 5, mx + 5, my + 5, outline=MARKER_COLOUR, width=2)


class Bar(tk.Canvas):
    """A small horizontal bar (HP, EN)."""

    def __init__(self, master: tk.Misc, colour: str, width: int = 90):
        super().__init__(master, width=width, height=12, background=BAR_BG, highlightthickness=0)
        self.colour: str = colour
        self.bar_width: int = width

    def set(self, value: int, maximum: int) -> None:
        self.delete("all")
        share: float = 0 if maximum <= 0 else max(0.0, min(1.0, value / maximum))
        if share > 0:
            self.create_rectangle(0, 0, self.bar_width * share, 12, fill=self.colour, width=0)


class StatsPanel(ttk.LabelFrame):
    """All stats of one unit at the current step."""

    ROWS: tuple[str, ...] = ("HP", "EN", "ATK", "DEF", "Range", "On hit", "Effects", "Tile")

    def __init__(self, master: tk.Misc, title: str):
        super().__init__(master, text=title, padding=8)
        self.title_text: str = title
        self.values: dict[str, ttk.Label] = {}
        self.hp_bar: Bar = Bar(self, HP_BAR_COLOUR)
        self.en_bar: Bar = Bar(self, EN_BAR_COLOUR)
        for row, name in enumerate(self.ROWS):
            ttk.Label(self, text=name, width=8, font=ui_font(bold=True)).grid(row=row, column=0, sticky="w")
            if name == "HP":
                self.hp_bar.grid(row=row, column=1, sticky="w", padx=(0, 8))
            elif name == "EN":
                self.en_bar.grid(row=row, column=1, sticky="w", padx=(0, 8))
            bar_row: bool = name in ("HP", "EN")
            # fixed widths (in characters) so the panel does not jump while stepping
            label: ttk.Label = ttk.Label(self, text="", anchor="w", width=20 if bar_row else 30)
            label.grid(row=row, column=2 if bar_row else 1, columnspan=1 if bar_row else 2, sticky="w", pady=1)
            self.values[name] = label
        self.columnconfigure(2, weight=1)

    def show(self, side: Side, unit: UnitView | None) -> None:
        stats: UnitStats = side.stats
        self.configure(text=f"{self.title_text}: {stats.name}")
        hp: int = unit.hp if unit else 0
        energy: int = unit.energy if unit else 0
        self.hp_bar.set(hp, stats.hp)
        self.en_bar.set(energy, stats.max_en)
        self.values["HP"].configure(text=f"{hp} / {stats.hp}" + ("  dead" if unit is None else ""),
                                    foreground=DEAD_TEXT if unit is None else "")
        self.values["EN"].configure(text=f"{energy} / {stats.max_en}  (+{stats.reg_en}/turn, {stats.en_atk}/hit)")
        self.values["ATK"].configure(text=str(stats.atk))
        if unit is None:
            defense = "-"
        else:
            defense = f"{unit.defense}"
            if unit.defense != stats.defense:
                defense += f" (base {stats.defense}, acid)"
            if unit.current_defense != unit.defense:
                defense += f" {ARROW} {unit.current_defense} on {side.terrain.value}"
        self.values["DEF"].configure(text=defense)
        kind: str = "ranged" if stats.is_ranged else "melee"
        move: str = f", +{stats.move_penalty} EN/tile moved" if stats.move_penalty else ""
        self.values["Range"].configure(text=f"{stats.attack_range} ({kind}){move}")
        self.values["On hit"].configure(text=", ".join(e.kind.value for e in stats.on_hit) or "-")
        effects: str = "-"
        if unit and unit.effects:
            effects = ", ".join(
                f"{e.kind.value} {e.magnitude} HP x {e.remaining_turns} more round(s)" for e in unit.effects
            )
        self.values["Effects"].configure(text=effects)
        self.values["Tile"].configure(text=side.describe())


STEP_COLUMNS: tuple[tuple[str, str, int, bool], ...] = (  # id, heading, width, stretch
    ("round", "Round", 60, False), ("event", "Event", 130, False), ("calculation", "Calculation", 260, True),
    ("hp", "Target HP", 85, False), ("en", "Attacker EN", 80, False),
)


def make_step_tree(master: tk.Misc, first_column: tuple[str, str, int, bool] | None, height: int,
                   tree_column: bool) -> tuple[ttk.Frame, ttk.Treeview]:
    """A Treeview with the step columns and a scrollbar, used by both tabs. Returns (frame to place, tree)."""
    frame = ttk.Frame(master)
    columns: tuple[tuple[str, str, int, bool], ...] = ((first_column,) if first_column else ()) + STEP_COLUMNS
    tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="tree headings" if tree_column else "headings",
                        height=height, selectmode="browse")
    if tree_column:
        tree.heading("#0", text="Pair / step", anchor="w")
        tree.column("#0", width=170, minwidth=120, stretch=False)
    for column, title, width, stretch in columns:
        tree.heading(column, text=title, anchor="w")
        tree.column(column, width=width, minwidth=40, stretch=stretch, anchor="w")
    scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    tree.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")
    bold: tkfont.Font = ui_font(bold=True)
    tree.tag_configure("pair", font=bold)
    tree.tag_configure("regen", foreground=REGEN_TEXT)
    tree.tag_configure("venom", foreground=VENOM_COLOUR)
    tree.tag_configure("dead", foreground=DEAD_TEXT, font=bold)
    return frame, tree


def combobox(master: tk.Misc, variable: tk.StringVar, values: Sequence[str], width: int) -> ttk.Combobox:
    box = ttk.Combobox(master, textvariable=variable, values=list(values), width=width, state="readonly")
    return box


# ============================================================================ tab 1: attack report
class ReportTab(ttk.Frame):
    """Every unit attacks every unit with the conditions chosen on top."""

    def __init__(self, master: tk.Misc, engine: CalculationEngine):
        super().__init__(master, padding=8)
        self.engine: CalculationEngine = engine
        self.roster: Roster | None = None
        self.names: dict[str, str] = {}
        self.fights: dict[tuple[str, str], Fight] = {}
        self.selected: tuple[str, str] | None = None
        self.cells: dict[tuple[str, str], tk.Label] = {}
        self.pair_items: dict[tuple[str, str], str] = {}
        self.item_pairs: dict[str, tuple[str, str]] = {}

        # --- control bar
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        self.attacker_terrain = tk.StringVar(value=TERRAINS[0])
        self.attacker_height = tk.StringVar(value=HEIGHTS[0])
        self.target_terrain = tk.StringVar(value=TERRAINS[0])
        self.target_height = tk.StringVar(value=HEIGHTS[0])
        self.ranged = tk.StringVar(value=BEST)
        for text, terrain, height in (("Attacker", self.attacker_terrain, self.attacker_height),
                                      ("Defender", self.target_terrain, self.target_height)):
            group = ttk.LabelFrame(bar, text=text, padding=(8, 2))
            group.pack(side="left", padx=(0, 10))
            ttk.Label(group, text="terrain").pack(side="left")
            combobox(group, terrain, TERRAINS, 7).pack(side="left", padx=(4, 10))
            ttk.Label(group, text="height").pack(side="left")
            combobox(group, height, HEIGHTS, 3).pack(side="left", padx=(4, 0))
        group = ttk.LabelFrame(bar, text="Ranged units", padding=(8, 2))
        group.pack(side="left")
        ttk.Label(group, text="distance").pack(side="left")
        self.ranged_box = combobox(group, self.ranged, (BEST,), 6)
        self.ranged_box.pack(side="left", padx=(4, 0))
        self.ranged_info = ttk.Label(bar, text="", foreground=REGEN_TEXT)
        self.ranged_info.pack(side="left", padx=10)
        for variable in (self.attacker_terrain, self.attacker_height, self.target_terrain, self.target_height,
                         self.ranged):
            variable.trace_add("write", lambda *_: self.recalculate())

        # --- matrix
        ttk.Label(self, text="Rounds to kill (hits).  Rows: attacker, columns: target.  "
                             "Green = target dies fast, red = slowly.  Click a cell to see the steps.",
                  foreground=REGEN_TEXT).pack(anchor="w", pady=(10, 4))
        self.matrix = ttk.Frame(self)
        self.matrix.pack(anchor="w")

        # --- steps of all pairs + chart
        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True, pady=(10, 0))
        left = ttk.Frame(panes)
        buttons = ttk.Frame(left)
        buttons.pack(fill="x", pady=(0, 4))
        ttk.Label(buttons, text="All pairs, every step", font=ui_font(bold=True)).pack(side="left")
        ttk.Button(buttons, text="Collapse all", command=lambda: self.expand_all(False)).pack(side="right")
        ttk.Button(buttons, text="Expand all", command=lambda: self.expand_all(True)).pack(side="right", padx=4)
        frame, self.tree = make_step_tree(left, None, height=14, tree_column=True)
        frame.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        panes.add(left, weight=3)
        right = ttk.Frame(panes, padding=(8, 0, 0, 0))
        self.chart = HpChart(right)
        self.chart.pack(fill="both", expand=True)
        self.summary = ttk.Label(right, text="", justify="left")
        self.summary.pack(anchor="w", pady=(6, 0))
        panes.add(right, weight=2)

    # ------------------------------------------------------------------ data
    def set_roster(self, roster: Roster) -> None:
        self.roster = roster
        self.names = display_names(roster)
        ranged: list[UnitStats] = [u for u in roster if u.is_ranged]
        longest: int = max((max(legal_distances(u, self.engine), default=1) for u in ranged), default=0)
        values: list[str] = [BEST] + [str(d) for d in range(1, longest + 1)]
        self.ranged_box.configure(values=values, state="readonly" if ranged else "disabled")
        if self.ranged.get() not in values:
            self.ranged.set(BEST)  # triggers recalculate()
        self.build_matrix()
        self.recalculate()

    def ranged_distance(self) -> int | None:
        value: str = self.ranged.get()
        return None if value == BEST else int(value)

    def recalculate(self) -> None:
        if self.roster is None:
            return
        self.fights = attack_matrix(
            self.roster,
            TerrainType(self.attacker_terrain.get()), int(self.attacker_height.get()),
            TerrainType(self.target_terrain.get()), int(self.target_height.get()),
            self.ranged_distance(), self.engine,
        )
        used: list[str] = []
        for unit in self.roster:
            if unit.is_ranged:
                fight: Fight = self.fights[(unit.key, unit.key)]
                used.append(f"{unit.name} from {fight.scenario.distance}")
        self.ranged_info.configure(text=("Melee from 1 tile. Ranged: " + ", ".join(used)) if used else "")
        self.fill_matrix()
        self.fill_tree()
        if self.selected not in self.fights:
            self.selected = next(iter(self.fights), None)
        self.select(self.selected, scroll=True)

    # ------------------------------------------------------------------ matrix
    def build_matrix(self) -> None:
        for child in self.matrix.winfo_children():
            child.destroy()
        self.cells.clear()
        assert self.roster is not None
        ttk.Label(self.matrix, text=f"attacker ↓   target {ARROW}", foreground=REGEN_TEXT).grid(
            row=0, column=0, sticky="w", padx=(0, 10))
        units: list[UnitStats] = list(self.roster)
        for col, target in enumerate(units, start=1):
            ttk.Label(self.matrix, text=f"{self.names[target.key]}\nHP {target.hp}, DEF {target.defense}",
                      anchor="center", justify="center").grid(row=0, column=col, padx=2, pady=(0, 2))
        for row, attacker in enumerate(units, start=1):
            ttk.Label(self.matrix, text=f"{self.names[attacker.key]}  (ATK {attacker.atk})",
                      font=ui_font(bold=True)).grid(row=row, column=0, sticky="w", padx=(0, 10))
            for col, target in enumerate(units, start=1):
                cell = tk.Label(self.matrix, text="", width=14, height=1, relief="flat", cursor="hand2",
                                borderwidth=2, highlightthickness=2, highlightbackground=CHART_BG)
                cell.grid(row=row, column=col, padx=2, pady=2, ipady=4)
                pair: tuple[str, str] = (attacker.key, target.key)
                cell.bind("<Button-1>", lambda _e, p=pair: self.select(p, scroll=True, open_pair=True))
                self.cells[pair] = cell

    def fill_matrix(self) -> None:
        rounds: list[int] = [f.rounds for f in self.fights.values()]
        low, high = min(rounds, default=0), max(rounds, default=0)
        for pair, cell in self.cells.items():
            fight: Fight | None = self.fights.get(pair)
            if fight is None:
                continue
            text: str = f"{fight.rounds} ({fight.hits})" if fight.killed else f"alive ({fight.hits})"
            cell.configure(text=text, background=heat_colour(fight.rounds, low, high) if fight.killed else BAR_BG)

    # ------------------------------------------------------------------ tree
    def fill_tree(self) -> None:
        open_pairs: set[tuple[str, str]] = {p for p, item in self.pair_items.items()
                                            if self.tree.exists(item) and self.tree.item(item, "open")}
        self.tree.delete(*self.tree.get_children())
        self.pair_items.clear()
        self.item_pairs.clear()
        for pair, fight in self.fights.items():
            first, last = fight.steps[0], fight.steps[-1]
            item: str = self.tree.insert(
                "", "end", text=f"{self.names[pair[0]]} {ARROW} {self.names[pair[1]]}", open=pair in open_pairs,
                values=("", fight.summary(), fight.damage_text(),
                        f"{first.target_hp_after} {ARROW} {last.target_hp_after}", ""),
                tags=("pair",),
            )
            self.pair_items[pair] = item
            self.item_pairs[item] = pair
            for step in fight.steps:
                child: str = self.tree.insert(item, "end", text=f"step {step.index}", values=step_row(step),
                                              tags=(step_tag(step),))
                self.item_pairs[child] = pair

    def expand_all(self, open_: bool) -> None:
        for item in self.tree.get_children():
            self.tree.item(item, open=open_)

    def on_tree_select(self, _event: object = None) -> None:
        selection: tuple[str, ...] = self.tree.selection()
        if not selection:
            return
        pair: tuple[str, str] | None = self.item_pairs.get(selection[0])
        if pair is None:
            return
        parent: str = self.tree.parent(selection[0])
        marker: int | None = self.tree.index(selection[0]) if parent else None
        self.show_pair(pair, marker)

    # ------------------------------------------------------------------ selection
    def select(self, pair: tuple[str, str] | None, scroll: bool = False, open_pair: bool = False) -> None:
        if pair is None or pair not in self.fights:
            self.show_pair(None, None)
            return
        item: str = self.pair_items[pair]
        if open_pair:
            self.tree.item(item, open=True)
        self.tree.selection_set(item)  # fires on_tree_select -> show_pair
        if scroll:
            self.tree.see(item)
            if open_pair:
                children: tuple[str, ...] = self.tree.get_children(item)
                if children:
                    self.tree.see(children[min(len(children) - 1, 8)])
                self.tree.see(item)
        self.show_pair(pair, None)

    def show_pair(self, pair: tuple[str, str] | None, marker: int | None) -> None:
        if self.selected in self.cells:
            self.cells[self.selected].configure(highlightbackground=CHART_BG)
        self.selected = pair
        fight: Fight | None = self.fights.get(pair) if pair else None
        if pair in self.cells:
            self.cells[pair].configure(highlightbackground=CELL_SELECTED_BORDER)
        self.chart.show(fight, marker)
        if fight is None:
            self.summary.configure(text="")
            return
        sc: Scenario = fight.scenario
        self.summary.configure(text=(
            f"{sc.title}: {fight.summary()}, distance {sc.distance}\n{fight.damage_text()}"
        ))


def heat_colour(value: int, low: int, high: int) -> str:
    """Green (low) -> yellow -> red (high), interpolated between the HEAT_SCALE colours."""
    if high <= low:
        return HEAT_SCALE[0]
    share: float = (value - low) / (high - low) * (len(HEAT_SCALE) - 1)
    index: int = min(int(share), len(HEAT_SCALE) - 2)
    t: float = share - index
    a, b = HEAT_SCALE[index], HEAT_SCALE[index + 1]
    rgb: list[int] = [round(int(a[i:i + 2], 16) * (1 - t) + int(b[i:i + 2], 16) * t) for i in (1, 3, 5)]
    return "#" + "".join(f"{c:02x}" for c in rgb)


# ============================================================================ tab 2: attack simulation
class SimulationTab(ttk.Frame):
    """Unit 1 attacks unit 2, step by step, with all stats of both units."""

    def __init__(self, master: tk.Misc, engine: CalculationEngine):
        super().__init__(master, padding=8)
        self.engine: CalculationEngine = engine
        self.roster: Roster | None = None
        self.keys_by_name: dict[str, str] = {}
        self.fight: Fight | None = None
        self.index: int = 0
        self._loading: bool = False

        # --- pickers
        top = ttk.Frame(self)
        top.pack(fill="x")
        self.attacker = tk.StringVar()
        self.target = tk.StringVar()
        self.attacker_terrain = tk.StringVar(value=TERRAINS[0])
        self.attacker_height = tk.StringVar(value=HEIGHTS[0])
        self.target_terrain = tk.StringVar(value=TERRAINS[0])
        self.target_height = tk.StringVar(value=HEIGHTS[0])
        self.distance = tk.StringVar(value="1")
        self.unit_boxes: list[ttk.Combobox] = []
        for title, unit, terrain, height in (
            ("Unit 1 (attacker)", self.attacker, self.attacker_terrain, self.attacker_height),
            ("Unit 2 (target)", self.target, self.target_terrain, self.target_height),
        ):
            group = ttk.LabelFrame(top, text=title, padding=(8, 2))
            group.pack(side="left", padx=(0, 10))
            ttk.Label(group, text="unit").pack(side="left")
            box = combobox(group, unit, (), 16)
            box.pack(side="left", padx=(4, 10))
            self.unit_boxes.append(box)
            ttk.Label(group, text="terrain").pack(side="left")
            combobox(group, terrain, TERRAINS, 7).pack(side="left", padx=(4, 10))
            ttk.Label(group, text="height").pack(side="left")
            combobox(group, height, HEIGHTS, 3).pack(side="left", padx=(4, 0))
        controls = ttk.Frame(self)  # distance + step buttons
        controls.pack(fill="x", pady=(10, 6))
        group = ttk.LabelFrame(controls, text="Distance", padding=(8, 2))
        group.pack(side="left", padx=(0, 12))
        self.distance_box = combobox(group, self.distance, ("1",), 4)
        self.distance_box.pack(side="left")
        self.distance_info = ttk.Label(group, text="", foreground=REGEN_TEXT)
        self.distance_info.pack(side="left", padx=(6, 0))
        self.attacker.trace_add("write", lambda *_: self.on_attacker_change())
        for variable in (self.target, self.attacker_terrain, self.attacker_height, self.target_terrain,
                         self.target_height, self.distance):
            variable.trace_add("write", lambda *_: self.recalculate())

        # --- step controls
        self.buttons: dict[str, ttk.Button] = {}
        for name, text, command in (
            ("reset", "⟲ Reset", lambda: self.go(0)),
            ("back", "◀ Back", lambda: self.go(self.index - 1)),
            ("step", "Step ▶", lambda: self.go(self.index + 1)),
            ("end", "Run to end ⏭", lambda: self.go(len(self.fight.steps) - 1 if self.fight else 0)),
        ):
            button = ttk.Button(controls, text=text, command=command)
            button.pack(side="left", padx=(0, 4))
            self.buttons[name] = button
        ttk.Label(controls, text="keys: ← → Home End", foreground=REGEN_TEXT).pack(side="left", padx=8)
        self.position = ttk.Label(controls, text="", font=ui_font(bold=True, grow=1))
        self.position.pack(side="right")

        # --- stats + chart
        middle = ttk.Frame(self)
        middle.pack(fill="x")
        self.attacker_panel = StatsPanel(middle, "Unit 1 (attacker)")
        self.attacker_panel.pack(side="left", fill="y", padx=(0, 8))
        self.target_panel = StatsPanel(middle, "Unit 2 (target)")
        self.target_panel.pack(side="left", fill="y", padx=(0, 8))
        self.chart = HpChart(middle, height=230)
        self.chart.pack(side="left", fill="both", expand=True)

        self.current = ttk.Label(self, text="", font=ui_font(grow=1))
        self.current.pack(anchor="w", pady=(8, 0))
        self.next_hit = ttk.Label(self, text="", font=ui_font(bold=True, grow=1))
        self.next_hit.pack(anchor="w", pady=(2, 6))
        self.summary = ttk.Label(self, text="", foreground=REGEN_TEXT)
        self.summary.pack(anchor="w", pady=(0, 6))

        # --- step log
        frame, self.log = make_step_tree(self, ("step", "Step", 50, False), height=10, tree_column=False)
        frame.pack(fill="both", expand=True)
        self.log.bind("<<TreeviewSelect>>", self.on_log_select)

    # ------------------------------------------------------------------ data
    def set_roster(self, roster: Roster) -> None:
        old_attacker: str | None = self.keys_by_name.get(self.attacker.get())
        old_target: str | None = self.keys_by_name.get(self.target.get())
        self.roster = roster
        names: dict[str, str] = display_names(roster)
        self.keys_by_name = {name: key for key, name in names.items()}
        for box in self.unit_boxes:
            box.configure(values=list(names.values()))
        units: list[UnitStats] = list(roster)
        default_attacker: str = next((u.key for u in units if not u.is_boss), units[0].key)
        default_target: str = next((u.key for u in units if u.is_boss), units[-1].key)
        self._loading = True  # set several variables, recalculate once
        try:
            self.attacker.set(names[old_attacker] if old_attacker in names else names[default_attacker])
            self.target.set(names[old_target] if old_target in names else names[default_target])
        finally:
            self._loading = False
        self.on_attacker_change(keep_step=True)

    def unit(self, variable: tk.StringVar) -> UnitStats | None:
        key: str | None = self.keys_by_name.get(variable.get())
        return self.roster[key] if self.roster is not None and key else None

    def on_attacker_change(self, keep_step: bool = False) -> None:
        if self._loading:
            return
        attacker: UnitStats | None = self.unit(self.attacker)
        if attacker is None:
            return
        distances: list[int] = legal_distances(attacker, self.engine)
        self.distance_box.configure(values=[str(d) for d in distances],
                                    state="readonly" if len(distances) > 1 else "disabled")
        best: int = best_distance(attacker, self.engine)
        self.distance_info.configure(text=f"best {best}" if attacker.is_ranged else "melee")
        self._loading = True
        try:
            if self.distance.get() not in [str(d) for d in distances]:
                self.distance.set(str(best))
        finally:
            self._loading = False
        self.recalculate(keep_step=keep_step)

    def scenario(self) -> Scenario | None:
        attacker, target = self.unit(self.attacker), self.unit(self.target)
        if attacker is None or target is None:
            return None
        return Scenario(
            Side(attacker, TerrainType(self.attacker_terrain.get()), int(self.attacker_height.get())),
            Side(target, TerrainType(self.target_terrain.get()), int(self.target_height.get())),
            int(self.distance.get()),
        )

    def recalculate(self, keep_step: bool = False) -> None:
        if self._loading:
            return
        scenario: Scenario | None = self.scenario()
        if scenario is None:
            return
        self.fight = simulate(scenario, self.engine)
        self.log.delete(*self.log.get_children())
        for step in self.fight.steps:
            self.log.insert("", "end", iid=str(step.index), values=(step.index, *step_row(step)),
                            tags=(step_tag(step),))
        self.summary.configure(text=f"Whole fight: {self.fight.summary()} · {self.fight.damage_text()}")
        self.go(min(self.index, len(self.fight.steps) - 1) if keep_step else 0)

    # ------------------------------------------------------------------ stepping
    def go(self, index: int) -> None:
        if self.fight is None:
            return
        steps: tuple[Step, ...] = self.fight.steps
        self.index = max(0, min(index, len(steps) - 1))
        step: Step = steps[self.index]
        scenario: Scenario = self.fight.scenario
        self.attacker_panel.show(scenario.attacker, step.attacker)
        self.target_panel.show(scenario.target, step.target)
        self.chart.show(self.fight, self.index)
        round_text: str = "before the fight" if step.kind is StepKind.START else f"round {step.round}"
        self.position.configure(text=f"{round_text} · step {self.index} / {len(steps) - 1}")
        description: str = f"This step: {step.event}"
        if step.calculation:
            description += f"  [{step.calculation}]"
        if step.kind in (StepKind.HIT, StepKind.VENOM):
            description += f"  HP {step.target_hp_before} {ARROW} {step.target_hp_after}"
        self.current.configure(text=description)
        upcoming: Step | None = self.fight.next_hit(self.index)
        if upcoming is not None and upcoming.result is not None:
            self.next_hit.configure(text=f"Next hit (round {upcoming.round}): {upcoming.result.formula()} damage",
                                    foreground="")
        elif step.killed:
            self.next_hit.configure(text=f"{scenario.target.stats.name} is dead.", foreground=DEAD_TEXT)
        else:
            self.next_hit.configure(text="No more hits.", foreground=DEAD_TEXT)
        at_start, at_end = self.index == 0, self.index == len(steps) - 1
        for name, disabled in (("reset", at_start), ("back", at_start), ("step", at_end), ("end", at_end)):
            self.buttons[name].state(["disabled"] if disabled else ["!disabled"])
        item: str = str(self.index)
        if self.log.exists(item) and self.log.selection() != (item,):
            self.log.selection_set(item)
            self.log.see(item)

    def on_log_select(self, _event: object = None) -> None:
        selection: tuple[str, ...] = self.log.selection()
        if selection and int(selection[0]) != self.index:
            self.go(int(selection[0]))


# ============================================================================ window
class ReportApp(tk.Tk):
    def __init__(self, units_file: str | Path | None = None, engine: CalculationEngine | None = None):
        super().__init__()
        self.title(REPORT_TITLE)
        self.geometry(WINDOW_SIZE)
        self.minsize(*MIN_WINDOW_SIZE)
        self.units_file: Path = Path(units_file or DEFAULT_UNITS_FILE)
        self.engine: CalculationEngine = engine or StandardCalculationEngine()

        top = ttk.Frame(self, padding=(8, 8, 8, 0))
        top.pack(fill="x")
        ttk.Button(top, text="⟳ Refresh", command=self.refresh).pack(side="left")
        self.status = ttk.Label(top, text="")
        self.status.pack(side="left", padx=10)
        ttk.Label(top, text="Attacker hits while it has energy, the target does nothing.",
                  foreground=REGEN_TEXT).pack(side="right")

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=8)
        self.report_tab = ReportTab(self.notebook, self.engine)
        self.simulation_tab = SimulationTab(self.notebook, self.engine)
        self.notebook.add(self.report_tab, text="  Attack report  ")
        self.notebook.add(self.simulation_tab, text="  Attack simulation  ")

        self.bind("<F5>", lambda _e: self.refresh())
        for key, move in (("<Right>", 1), ("<Left>", -1)):
            self.bind(key, lambda e, m=move: self.on_step_key(e, lambda tab: tab.go(tab.index + m)))
        self.bind("<Home>", lambda e: self.on_step_key(e, lambda tab: tab.go(0)))
        self.bind("<End>", lambda e: self.on_step_key(e, lambda tab: tab.go(len(tab.fight.steps) - 1)))

        if not self.refresh():
            raise SystemExit(f"Cannot load {self.units_file}")

    def refresh(self) -> bool:
        """Reload units.json and recalculate both tabs. On an error keep the old data."""
        try:
            roster: Roster = load_roster(self.units_file)
            if len(roster) == 0:
                raise ValueError("no units")
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            messagebox.showerror(REPORT_TITLE, f"Cannot load {self.units_file}:\n\n{error}", parent=self)
            return False
        self.report_tab.set_roster(roster)
        self.simulation_tab.set_roster(roster)
        self.status.configure(text=f"{self.units_file.name} loaded {time.strftime('%H:%M:%S')} · {len(roster)} units")
        return True

    def on_step_key(self, event: tk.Event, action: Callable[[SimulationTab], None]) -> None:
        if self.notebook.select() != str(self.simulation_tab) or self.simulation_tab.fight is None:
            return
        if isinstance(event.widget, (ttk.Combobox, tk.Entry, ttk.Entry)):
            return
        action(self.simulation_tab)


def main() -> None:
    ReportApp().mainloop()


if __name__ == "__main__":
    main()
