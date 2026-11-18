"""Live training monitor: charts and statistics of ``tools/train_ai_bot.py`` runs, epoch by epoch, and a viewer
of the games it played.

Run from the project's environment (Python 3.10+ with tkinter; the Training tab alone needs no extra packages)::

    python tools/train_monitor.py                         # finds train_ai_bot.csv (or .log) and .bgn by itself
    python tools/train_monitor.py tools/train_ai_bot.csv
    python tools/train_monitor.py tools/train_ai_bot.log  # a run started before the csv existed
    python tools/train_monitor.py train_ai_bot.bgn        # opens the Games tab with these games

Training tab:

It reads the epoch rows the trainer appends to ``--csv`` (or the epoch lines of its log file) and checks the file for
new rows every 2 seconds, reading only what was added since the last check. Every run in the file is listed; with
"Follow newest run" the view jumps to a new run as soon as its first epoch is written.

Charts: results (% wins, losses, stopped), unit turns (all games, won ones, lost ones), score and training loss.
Thin lines are the epoch values, thick ones the moving average over the "Average" epochs. Move the mouse over a chart
to read the values of one epoch.

Benchmark: every BENCHMARK_EVERY_EPOCHS epochs of the newest run (epoch 10, 20, ...) the monitor copies the weights
file (AI_WEIGHTS_FILE, the trainer's default ``--out``) and plays the benchmark of ``tools/eval_ai_bot.py`` with it in a
separate process (lower priority, one at a time; the BENCHMARK_* settings of ``beastborn/constant.py``, against the
AI in TRAIN_MONITOR_BENCHMARK_AI, whose name the Benchmarks panel shows). Its results
are the "Benchmark" chart (like "Results per epoch", against an opponent that does not change, so it shows real
progress) and the list next to it; "Benchmark now" plays one for the newest run's last epoch at once. Only epochs
written while the monitor runs are benchmarked (older ones were played with other weights); closing the monitor stops
a benchmark that is being played. Files next to the statistics file: ``train_ai_bot.benchmark.csv`` (one row per
benchmark, every run), ``.bgn`` (the games of the newest run's benchmarks), ``.log`` (eval_ai_bot's result lines)
and ``.out`` (the console of the last benchmark).

Games tab: the games of a ``.bgn`` file (Beastborn Game Notation, ``beastborn/game/bgn.py``; the trainer writes
``train_ai_bot.bgn``), checked for new games every 2 seconds like the statistics. "Games" switches between the training
games and the benchmark games (their "Epoch" is the training epoch whose weights played them; double-click a benchmark
in the Training tab to see its games). Pick one and watch it: one step is
one command of the game (a whole unit turn for the bots), forwards, backwards, or played automatically. The board
shows the last step's move (dashed line) and attacks (red arrow); above it each player's score and the result.
Keys: Right / Left = step, Space = play / pause, Home / End = start / end.
"Epoch" above the list shows only the games of one epoch. The epoch of each game comes from the statistics file of
the Training tab: the trainer empties the .bgn file when a run starts and appends its games in order, so the run's
rows (found by the time of its first game) and their "games" counts tell which epoch played which game. Games after
the run's last row belong to an epoch that is still being played (or was stopped).

Network tab: the health of a network (``AfterstateNet``, ``beastborn/control/ai_find_action_strategy.py``). By default
the weights file the trainer writes (its ``--out``, read from the run's log, else AI_WEIGHTS_FILE); "Open weights…"
checks any other ``.safetensors`` file, "Training weights" goes back. With "Auto" it is checked again whenever the
trainer saves it (at most once a minute, only while the tab is shown). Nothing about the network is fixed here: every
tensor of the file is listed, the network is built from AfterstateNet with the sizes the file's shapes give, and its
ReLU-like activations are found while it runs on "States" afterstates of the newest training games (as the network
sees them in training; random games when there are none).
Weights: mean, spread, how many are 0 (and output units with only zero weights), how much they changed since the
save before, and how many get no gradient. Activations: dead units (never above 0 on any of the states, so a ReLU
unit no longer learns; LeakyReLU only learns there at its small slope) and rarely active ones. On the right one
square per unit coloured by how often it is active, and the histogram of the row picked (at first: the network's
output, whose spread near 0 means it says the same of every state).
"""
from __future__ import annotations

import csv
import os
import re
import shutil
import subprocess
import sys
import time
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, ttk

if not __package__:  # run as a file: make the 'beastborn' package importable (the Games tab imports it when used)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from beastborn import constant as C  # noqa: E402  (a leaf module, standard library only)

POLL_MS: int = 2000
CANDIDATES: tuple[str, ...] = ("train_ai_bot.csv", "train_ai_bot.log")
GAMES_CANDIDATES: tuple[str, ...] = ("train_ai_bot.bgn",)
DEFAULT_DELAY_MS: int = 500  # Games tab: time between two steps when it plays by itself
NUMBERS: tuple[str, ...] = ("epoch", "epochs", "games", "wins", "losses", "draws", "stopped", "unit_turns",
                            "win_turns", "loss_turns", "score", "loss", "memory", "steps", "seconds")
BENCHMARK_NUMBERS: tuple[str, ...] = ("epoch", "games", "wins", "losses", "draws", "stopped", "unit_turns",
                                      "win_turns", "loss_turns", "score", "seconds")  # tools/eval_ai_bot.py --csv
EVAL_SCRIPT: Path = Path(__file__).resolve().with_name("eval_ai_bot.py")
BENCHMARK_NAME: str = ".benchmark"  # train_ai_bot.csv -> train_ai_bot.benchmark.csv / .bgn / .log / .out
TRAINING_GAMES, BENCHMARK_GAMES = "training games", "benchmark games"  # the Games tab's lists

# ---------------------------------------------------------------------- colours
BG, PANEL, TEXT, MUTED, GRID, AXIS = "#1c1d22", "#25272e", "#e8e8ea", "#9a9ca5", "#33353d", "#4a4d57"
WIN, LOSS, STOPPED = "#4cc38a", "#ef6461", "#a0a4ad"
TURNS, SCORE, TRAIN_LOSS, WARN = "#6aa9ff", "#c58af9", "#f2a53a", "#f2a53a"
PLAYER_COLOURS = ("#d6483f", "#3e7cde", "#e2b830", "#a056cc")  # as in the browser
GRASS, MUD, HIGH_GROUND = "#3f5f38", "#6b5434", "#c8d9a0"
ACTIVE, HP_BAR, HP_EMPTY, BOSS_RING, VENOM = "#ffec5a", "#5ad264", "#6e1e1e", "#fadc78", "#8cf03c"
FONT = ("Segoe UI", 9)
FONT_SMALL = ("Segoe UI", 8)
FONT_BIG = ("Segoe UI Semibold", 16)
FONT_TITLE = ("Segoe UI Semibold", 10)


# ====================================================================== reading the file
Row = dict  # one epoch: the NUMBERS as float | None, plus "run" and "time" (str)


class Source:
    """Epoch rows of every run in a csv or log file, read incrementally (only new complete lines each poll)."""

    def __init__(self, path: Path, numbers: tuple[str, ...] = NUMBERS) -> None:
        """``numbers``: the csv columns read as numbers; the others are kept as text."""
        self.path: Path = path
        self.numbers: tuple[str, ...] = numbers
        self.is_log: bool = path.suffix.lower() == ".log"
        self.reset()

    def reset(self) -> None:
        self.runs: dict[str, list[Row]] = {}
        self.offset: int = 0
        self.rest: str = ""
        self.header: list[str] | None = None
        self.run: str = "?"  # log: the run being read
        self.game_turns: dict[str, list[int]] = {"win": [], "loss": []}  # log: this epoch's decisive games

    def poll(self) -> bool:
        """Read what was added since the last poll. True when there are new rows."""
        try:
            size: int = self.path.stat().st_size
            if size < self.offset:  # the file was replaced or emptied: start over
                self.reset()
            if size == self.offset:
                return False
            with open(self.path, "rb") as file:
                file.seek(self.offset)
                data: bytes = file.read(size - self.offset)
        except OSError:
            return False
        self.offset += len(data)
        lines: list[str] = (self.rest + data.decode("utf-8", errors="replace")).split("\n")
        self.rest = lines.pop()  # an unfinished last line waits for the next poll
        before: int = sum(len(rows) for rows in self.runs.values())
        for line in lines:
            (self._log_line if self.is_log else self._csv_line)(line.rstrip("\r"))
        return sum(len(rows) for rows in self.runs.values()) != before

    # -------------------------------------------------------------- csv
    def _csv_line(self, line: str) -> None:
        if not line.strip():
            return
        values: list[str] = next(csv.reader([line]))
        if values and values[0] == "run":
            self.header = values
            return
        if self.header is None or len(values) != len(self.header):
            return
        raw: dict[str, str] = dict(zip(self.header, values))
        row: Row = {**raw, "run": raw.get("run", "?"), "time": raw.get("time", "")}
        for name in self.numbers:
            row[name] = _number(raw.get(name, ""))
        self.runs.setdefault(row["run"], []).append(row)

    # -------------------------------------------------------------- log
    _TRAINING = re.compile(r"^(\S+ \S+?)(?:,\d+)? \S+ \S+: Training: (\d+) epochs")
    _GAME = re.compile(r"epoch \d+/\d+ game \d+/\d+ .*?: (win|loss|draw|stopped) after (\d+) unit turns")
    _EPOCH = re.compile(
        r"^(\S+ \S+?)(?:,\d+)? .*?epoch (\d+)/(\d+): (\d+) games, (\d+) wins, (\d+) losses, (\d+) draws, "
        r"(\d+) stopped, ([\d.]+) unit turns, score ([+-]?[\d.]+), loss ([\d.]+|-), memory (\d+), ([\d.]+) s")

    def _log_line(self, line: str) -> None:
        if " game " in line:
            match = self._GAME.search(line)
            if match and match.group(1) in self.game_turns:
                self.game_turns[match.group(1)].append(int(match.group(2)))
            return
        match = self._EPOCH.search(line)
        if match:
            g = match.groups()
            row: Row = {"run": self.run, "time": g[0]}
            for name, value in zip(("epoch", "epochs", "games", "wins", "losses", "draws", "stopped",
                                    "unit_turns", "score", "loss", "memory", "seconds"), g[1:]):
                row[name] = _number(value)
            row["steps"] = None
            for result in ("win", "loss"):
                turns: list[int] = self.game_turns[result]
                row[f"{result}_turns"] = sum(turns) / len(turns) if turns else None
            self.game_turns = {"win": [], "loss": []}
            self.runs.setdefault(self.run, []).append(row)
            return
        match = self._TRAINING.search(line)
        if match:
            self.run = match.group(1)
            self.game_turns = {"win": [], "loss": []}

    def run_names(self) -> list[str]:
        return [name for name, rows in self.runs.items() if rows]

    def all_rows(self) -> list[Row]:
        return [row for rows in self.runs.values() for row in rows]


def _number(text: str) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _parse_time(text: str) -> float | None:
    try:
        return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S").timestamp()
    except (TypeError, ValueError):
        return None


# ====================================================================== maths
def moving_average(values: list[float | None], window: int) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(values)):
        part = [v for v in values[max(0, i - window + 1):i + 1] if v is not None]
        out.append(sum(part) / len(part) if part else None)
    return out


def mean_of(values: list[float | None]) -> float | None:
    part = [v for v in values if v is not None]
    return sum(part) / len(part) if part else None


def nice_ticks(low: float, high: float, count: int = 5) -> list[float]:
    if high <= low:
        high = low + 1
    raw: float = (high - low) / count
    magnitude: float = 10 ** _floor_log10(raw)
    step: float = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    first: float = step * (low // step)
    ticks: list[float] = []
    value: float = first
    while value <= high + step * 0.001:
        if value >= low - step * 0.001:
            ticks.append(round(value, 10))
        value += step
    return ticks


def _floor_log10(value: float) -> int:
    exponent: int = 0
    while value >= 10:
        value /= 10
        exponent += 1
    while value < 1:
        value *= 10
        exponent -= 1
    return exponent


def fmt(value: float | None, digits: int = 1, sign: bool = False) -> str:
    if value is None:
        return "–"
    return f"{value:+.{digits}f}" if sign else f"{value:.{digits}f}"


def duration(seconds: float | None) -> str:
    if seconds is None:
        return "–"
    seconds = int(max(0, seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours} h {minutes:02d} min" if hours else (f"{minutes} min {secs:02d} s" if minutes else f"{secs} s")


def blend(colour: str, into: str, amount: float) -> str:
    """``colour`` mixed with ``into`` (amount 0 = colour, 1 = into)."""
    a = [int(colour[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(into[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b))


# ====================================================================== a chart
class Series:
    def __init__(self, name: str, colour: str, values: list[float | None], smooth: list[float | None]) -> None:
        self.name, self.colour, self.values, self.smooth = name, colour, values, smooth


class LineChart(tk.Canvas):
    """Epoch values (thin) and their moving average (thick), with a shared hover line."""

    LEFT, RIGHT, TOP, BOTTOM = 52, 14, 34, 26

    def __init__(self, master: tk.Misc, title: str, unit: str = "", fixed: tuple[float, float] | None = None,
                 digits: int = 1, on_hover=None, markers: bool = False) -> None:
        """``on_hover(epoch, chart)``: the mouse is over ``epoch`` of ``chart`` (None, None: it left)."""
        super().__init__(master, background=PANEL, highlightthickness=0, height=220, width=420)
        self.title, self.unit, self.fixed, self.digits, self.on_hover = title, unit, fixed, digits, on_hover
        self.markers: bool = markers  # a dot on every value (few points far apart)
        self.empty: str = "no data yet"
        self.epochs: list[float] = []
        self.x_span: tuple[float, float] | None = None  # the epochs the x axis shows (None = those of the data)
        self.series: list[Series] = []
        self.hover: int | None = None  # index into epochs
        self.bind("<Configure>", lambda _e: self.draw())
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda _e: self.on_hover and self.on_hover(None, None))

    def set_data(self, epochs: list[float], series: list[Series], x_span: tuple[float, float] | None = None) -> None:
        self.epochs, self.series, self.x_span = epochs, series, x_span
        self.draw()

    def set_hover(self, epoch: float | None, owner: LineChart | None) -> None:
        """Mark ``epoch`` if this chart has a value for it."""
        self.hover = None if epoch is None else next((i for i, x in enumerate(self.epochs) if x == epoch), None)
        self._owner = owner is self
        self.draw()

    # ---------------------------------------------------------- geometry
    def _area(self) -> tuple[int, int, int, int]:
        return self.LEFT, self.TOP, max(self.LEFT + 10, self.winfo_width() - self.RIGHT), \
            max(self.TOP + 10, self.winfo_height() - self.BOTTOM)

    def _ranges(self) -> tuple[float, float, float, float]:
        x0, x1 = self.x_span or ((self.epochs[0], self.epochs[-1]) if self.epochs else (0.0, 1.0))
        if x1 <= x0:
            x1 = x0 + 1
        if self.fixed:
            return x0, x1, *self.fixed
        values = [v for s in self.series for v in s.values + s.smooth if v is not None]
        if not values:
            return x0, x1, 0.0, 1.0
        y0, y1 = min(values), max(values)
        pad = (y1 - y0) * 0.08 or abs(y1) * 0.1 or 1.0
        return x0, x1, y0 - pad, y1 + pad

    # ---------------------------------------------------------- drawing
    def draw(self) -> None:
        self.delete("all")
        left, top, right, bottom = self._area()
        x0, x1, y0, y1 = self._ranges()
        sx = lambda x: left + (x - x0) / (x1 - x0) * (right - left)  # noqa: E731
        sy = lambda y: bottom - (y - y0) / (y1 - y0) * (bottom - top)  # noqa: E731

        self.create_text(12, 14, text=self.title, anchor="w", fill=TEXT, font=FONT_TITLE)
        legend_x = right
        for s in reversed(self.series):  # legend, right-aligned on the title row
            item = self.create_text(legend_x, 14, text=s.name, anchor="e", fill=MUTED, font=FONT_SMALL)
            bbox = self.bbox(item)
            self.create_rectangle(bbox[0] - 14, 10, bbox[0] - 5, 18, fill=s.colour, outline="")
            legend_x = bbox[0] - 22

        for tick in nice_ticks(y0, y1, max(2, (bottom - top) // 40)):
            y = sy(tick)
            self.create_line(left, y, right, y, fill=GRID)
            label = f"{tick:.{max(0, self.digits - 1) if abs(y1 - y0) < 5 else 0}f}{self.unit}"
            self.create_text(left - 6, y, text=label, anchor="e", fill=MUTED, font=FONT_SMALL)
        if self.epochs or self.x_span:
            for tick in nice_ticks(x0, x1, max(2, (right - left) // 70)):
                if tick == int(tick):
                    x = sx(tick)
                    self.create_line(x, bottom, x, bottom + 4, fill=AXIS)
                    self.create_text(x, bottom + 6, text=str(int(tick)), anchor="n", fill=MUTED, font=FONT_SMALL)
        self.create_line(left, bottom, right, bottom, fill=AXIS)

        if not self.epochs:
            self.create_text((left + right) / 2, (top + bottom) / 2, text=self.empty, fill=MUTED, font=FONT)
            return
        for s in self.series:
            self._line([(sx(x), sy(v)) if v is not None else None for x, v in zip(self.epochs, s.values)],
                       blend(s.colour, PANEL, 0.6), 1)
        for s in self.series:
            self._line([(sx(x), sy(v)) if v is not None else None for x, v in zip(self.epochs, s.smooth)],
                       s.colour, 2.5)
        if self.markers:
            for s in self.series:
                for x, v in zip(self.epochs, s.values):
                    if v is not None:
                        self.create_oval(sx(x) - 3.5, sy(v) - 3.5, sx(x) + 3.5, sy(v) + 3.5, fill=s.colour,
                                         outline=PANEL)

        if self.hover is not None and 0 <= self.hover < len(self.epochs):
            x = sx(self.epochs[self.hover])
            self.create_line(x, top, x, bottom, fill=AXIS, dash=(3, 3))
            lines = [f"epoch {int(self.epochs[self.hover])}"]
            for s in self.series:
                value, avg = s.values[self.hover], s.smooth[self.hover]
                if value is not None:
                    y = sy(value)
                    self.create_oval(x - 3, y - 3, x + 3, y + 3, fill=s.colour, outline=PANEL)
                lines.append(f"{s.name}: {fmt(value, self.digits)}{self.unit}  (avg {fmt(avg, self.digits)})")
            if getattr(self, "_owner", False):
                self._tooltip(x, top, right, lines)

    def _line(self, points: list[tuple[float, float] | None], colour: str, width: float) -> None:
        run: list[float] = []
        for point in points + [None]:
            if point is None:
                if len(run) >= 4:
                    self.create_line(*run, fill=colour, width=width, capstyle="round", joinstyle="round")
                elif len(run) == 2:
                    self.create_oval(run[0] - 1.5, run[1] - 1.5, run[0] + 1.5, run[1] + 1.5, fill=colour,
                                     outline="")
                run = []
            else:
                run.extend(point)

    def _tooltip(self, x: float, top: int, right: int, lines: list[str]) -> None:
        text = self.create_text(0, 0, text="\n".join(lines), anchor="nw", fill=TEXT, font=FONT_SMALL)
        x1, y1, x2, y2 = self.bbox(text)
        w, h = x2 - x1, y2 - y1
        tx = x + 10 if x + 10 + w + 8 < right else x - 10 - w - 8
        self.coords(text, tx + 4, top + 4)
        box = self.create_rectangle(tx, top, tx + w + 8, top + h + 8, fill=BG, outline=AXIS)
        self.tag_raise(text, box)

    def _motion(self, event: tk.Event) -> None:
        if not self.epochs or not self.on_hover:
            return
        left, _top, right, _bottom = self._area()
        x0, x1, _y0, _y1 = self._ranges()
        epoch = x0 + (min(max(event.x, left), right) - left) / max(1, right - left) * (x1 - x0)
        index = min(range(len(self.epochs)), key=lambda i: abs(self.epochs[i] - epoch))
        self.on_hover(self.epochs[index], self)


# ====================================================================== a statistic card
class Card(tk.Frame):
    def __init__(self, master: tk.Misc, title: str) -> None:
        super().__init__(master, background=PANEL, padx=12, pady=8)
        tk.Label(self, text=title, bg=PANEL, fg=MUTED, font=FONT_SMALL, anchor="w").pack(fill="x")
        self.value = tk.Label(self, text="–", bg=PANEL, fg=TEXT, font=FONT_BIG, anchor="w")
        self.value.pack(fill="x")
        self.note = tk.Label(self, text="", bg=PANEL, fg=MUTED, font=FONT_SMALL, anchor="w")
        self.note.pack(fill="x")
        self.extra = tk.Label(self, text="", bg=PANEL, fg=MUTED, font=FONT_SMALL, anchor="w")
        self.extra.pack(fill="x")

    def show(self, value: str, note: str = "", colour: str = MUTED, extra: str = "") -> None:
        self.value.config(text=value)
        self.note.config(text=note, fg=colour)
        self.extra.config(text=extra, fg=MUTED)


def trend(now: float | None, before: float | None, digits: int, unit: str, good_up: bool | None) -> tuple[str, str]:
    """'▲ +2.0' (last epochs vs the ones before) and its colour (green = better, red = worse, grey = no judgement)."""
    if now is None or before is None:
        return "", MUTED
    change = now - before
    if abs(change) < 10 ** -digits / 2:
        return "■ no change", MUTED
    arrow = "▲" if change > 0 else "▼"
    colour = MUTED if good_up is None else (WIN if (change > 0) == good_up else LOSS)
    return f"{arrow} {change:+.{digits}f}{unit}", colour


# ====================================================================== the benchmark
@dataclass
class BenchmarkJob:
    """One benchmark: the weights of epoch ``epoch`` of training run ``run``, copied to ``weights``."""

    run: str
    epoch: int
    weights: Path
    started: float = 0.0  # time.time() when its process started


class Benchmark:
    """Plays ``tools/eval_ai_bot.py`` (the BENCHMARK_* settings, against TRAIN_MONITOR_BENCHMARK_AI) on copies of the
    weights file, one process at a time.

    Its files are next to the statistics file ``stats``: ``<stem>.benchmark.csv`` (one row per benchmark, read like the
    statistics), ``.bgn`` (the games, emptied when the first benchmark of a new run starts), ``.log`` (eval_ai_bot's
    Arguments / Result lines), ``.out`` (the console of the last benchmark) and ``.e<epoch>.safetensors`` (the copied
    weights, deleted when their benchmark ends).
    """

    def __init__(self, stats: Path) -> None:
        base: str = stats.stem + BENCHMARK_NAME
        self.folder: Path = stats.parent
        self.base: str = base
        self.csv_path: Path = stats.with_name(base + ".csv")
        self.games_path: Path = stats.with_name(base + ".bgn")
        self.log_path: Path = stats.with_name(base + ".log")
        self.out_path: Path = stats.with_name(base + ".out")
        self.source: Source = Source(self.csv_path, BENCHMARK_NUMBERS)
        self.source.poll()
        self.queue: list[BenchmarkJob] = []
        self.job: BenchmarkJob | None = None  # the one being played
        self.process: subprocess.Popen | None = None
        self._out = None  # the open .out file of the process
        self.error: str = ""  # why the last benchmark failed ("" = it did not)
        self.seen: set[tuple[str, float | None]] = set()  # (run, epoch) of the statistics rows already looked at

    # ---------------------------------------------------------- when
    def watch(self, stats: Source, start: bool = True) -> None:
        """Look at the statistics rows not seen yet: a new epoch of the newest run that is a multiple of
        BENCHMARK_EVERY_EPOCHS gets a benchmark (``start`` False: only remember them, e.g. a file just opened)."""
        names: list[str] = stats.run_names()
        for name in names:
            for row in stats.runs[name]:
                key = (name, row["epoch"])
                if key in self.seen:
                    continue
                self.seen.add(key)
                epoch = row["epoch"]
                if (start and name == names[-1] and epoch is not None and int(epoch) == epoch
                        and int(epoch) % C.BENCHMARK_EVERY_EPOCHS == 0):
                    self.request(name, int(epoch))

    def has(self, run: str, epoch: int) -> bool:
        """A benchmark of this epoch was played, is being played or is waiting."""
        return (any(row["epoch"] == epoch for row in self.source.runs.get(run, []))
                or any(job.run == run and job.epoch == epoch for job in [self.job, *self.queue] if job is not None))

    def request(self, run: str, epoch: int) -> None:
        """Copy the weights file now (they are this epoch's) and play their benchmark as soon as none is playing."""
        if self.has(run, epoch):
            return
        weights: Path = self.folder / f"{self.base}.e{epoch}.safetensors"
        try:
            shutil.copyfile(C.AI_WEIGHTS_FILE, weights)
        except OSError as error:
            self.error = f"epoch {epoch}: could not copy {C.AI_WEIGHTS_FILE.name}: {error}"
            return
        self.queue.append(BenchmarkJob(run, epoch, weights))
        self._start_next()

    # ---------------------------------------------------------- the process
    def _start_next(self) -> None:
        if self.process is not None or not self.queue:
            return
        job: BenchmarkJob = self.queue.pop(0)
        self.source.poll()
        names: list[str] = self.source.run_names()
        try:
            if not names or names[-1] != job.run:  # the first benchmark of a run: its games only
                self.games_path.write_text("", encoding="utf-8")
            self._out = open(self.out_path, "w", encoding="utf-8")
            flags: int = (getattr(subprocess, "CREATE_NO_WINDOW", 0)  # Windows: no console, training goes first
                          | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
            self.process = subprocess.Popen(
                [sys.executable, str(EVAL_SCRIPT), "--weights", str(job.weights), "--csv", str(self.csv_path),
                 "--bgn", str(self.games_path), "--log", str(self.log_path), "--run", job.run,
                 "--epoch", str(job.epoch), "--opponent", "ai",
                 "--opponent-weights", str(C.TRAIN_MONITOR_BENCHMARK_AI)],
                cwd=str(EVAL_SCRIPT.parent), stdin=subprocess.DEVNULL, stdout=self._out, stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONUNBUFFERED": "1"}, creationflags=flags)
        except OSError as error:
            self._close_out()
            self._delete(job)
            self.error = f"epoch {job.epoch}: could not start {EVAL_SCRIPT.name}: {error}"
            self._start_next()
            return
        job.started = time.time()
        self.job, self.error = job, ""

    def poll(self) -> bool:
        """New rows in the csv file, or a benchmark ended. True when something changed."""
        changed: bool = self.source.poll()
        if self.process is not None and self.process.poll() is not None:
            code: int = self.process.returncode
            job: BenchmarkJob | None = self.job
            self.process, self.job = None, None
            self._close_out()
            if job is not None:
                self._delete(job)
                if code != 0:
                    self.error = f"epoch {job.epoch} failed: {self._last_line() or f'exit code {code}'}"
            self.source.poll()
            self._start_next()
            changed = True
        return changed

    def progress(self) -> tuple[int, int]:
        """Games played and to play by the running benchmark (from its console)."""
        try:
            with open(self.out_path, "rb") as file:
                size: int = file.seek(0, 2)
                file.seek(max(0, size - 4096))
                text: str = file.read().decode("utf-8", errors="replace")
        except OSError:
            return 0, C.BENCHMARK_GAMES
        found = re.findall(r" game (\d+)/(\d+) ", text)
        return (int(found[-1][0]), int(found[-1][1])) if found else (0, C.BENCHMARK_GAMES)

    def _last_line(self) -> str:
        try:
            lines = [line.strip() for line in self.out_path.read_text(encoding="utf-8", errors="replace").splitlines()]
        except OSError:
            return ""
        last: str = next((line for line in reversed(lines) if line), "")
        return last if len(last) <= 90 else last[:87] + "..."

    def _close_out(self) -> None:
        if self._out is not None:
            self._out.close()
            self._out = None

    @staticmethod
    def _delete(job: BenchmarkJob) -> None:
        try:
            job.weights.unlink(missing_ok=True)
        except OSError:
            pass

    def stop(self) -> None:
        """Stop the benchmark being played and forget the waiting ones (the monitor closes)."""
        if self.process is not None:
            self.process.kill()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            self.process = None
        self._close_out()
        for job in [self.job, *self.queue]:
            if job is not None:
                self._delete(job)
        self.job, self.queue = None, []


def benchmark_opponent() -> str:
    """The name of the AI the benchmarks are played against (C.TRAIN_MONITOR_BENCHMARK_AI)."""
    return C.TRAIN_MONITOR_BENCHMARK_AI.name.replace(".safetensors", "")  # as eval_ai_bot.weights_name


def benchmark_epochs(records: list, rows: list[Row], running: tuple[float, int] | None = None
                     ) -> tuple[list[int | None], int]:
    """The training epoch whose benchmark played each game of ``records`` (None = unknown), and the last epoch with
    a benchmark row.

    A game belongs to the benchmark row (any run) that was being played when the game was created: between the row's
    "started" and "time". ``running``: (start time, epoch) of the benchmark being played, whose games have no row yet.
    """
    spans: list[tuple[float, float, int]] = []
    for row in rows:
        start, end = _parse_time(row.get("started", "")), _parse_time(row["time"])
        if start is not None and end is not None and row["epoch"] is not None:
            spans.append((start - 1, end + 1, int(row["epoch"])))
    epochs: list[int | None] = []
    finished: int = 0
    for record in records:
        created: float = record.game_id / 1000
        epoch: int | None = next((e for start, end, e in spans if start <= created <= end), None)
        if epoch is not None:
            finished = max(finished, epoch)
        elif running is not None and created >= running[0] - 1:
            epoch = running[1]
        epochs.append(epoch)
    return epochs, finished


class BenchmarkPanel(tk.Frame):
    """The benchmarks of the shown run (newest first), what is being played, and "Benchmark now"."""

    def __init__(self, master: tk.Misc, on_run, on_open) -> None:
        """``on_run()``: the button; ``on_open(epoch)``: a benchmark was double-clicked."""
        super().__init__(master, background=PANEL, padx=12, pady=8)
        self.on_open = on_open
        head = tk.Frame(self, background=PANEL)
        head.pack(fill="x")
        tk.Label(head, text="Benchmarks", bg=PANEL, fg=TEXT, font=FONT_TITLE).pack(side="left")
        self.button = ttk.Button(head, text="Benchmark now", style="Panel.TButton", command=on_run)
        self.button.pack(side="right")
        opponent = benchmark_opponent()
        tk.Label(self, text=f"every {C.BENCHMARK_EVERY_EPOCHS} epochs: {C.BENCHMARK_GAMES} games vs {opponent}, "
                            f"max {C.BENCHMARK_MAX_TURNS} turns", bg=PANEL, fg=MUTED, font=FONT_SMALL,
                 anchor="w").pack(fill="x", pady=(4, 0))
        self.status = tk.Label(self, text="", bg=PANEL, fg=MUTED, font=FONT_SMALL, anchor="w")
        self.status.pack(fill="x")
        tk.Label(self, text="double-click: watch its games", bg=PANEL, fg=MUTED, font=FONT_SMALL,
                 anchor="w").pack(side="bottom", fill="x")
        listing = tk.Frame(self, background=PANEL)
        listing.pack(fill="both", expand=True, pady=(4, 2))
        columns = (("epoch", "Epoch", 46), ("wins", "Wins", 54), ("losses", "Losses", 54), ("stopped", "Stopped", 58),
                   ("score", "Score", 58), ("turns", "Turns", 48))
        self.tree = ttk.Treeview(listing, columns=[c[0] for c in columns], show="headings", selectmode="browse",
                                 height=4)
        for name, title, width in columns:
            self.tree.heading(name, text=title)
            self.tree.column(name, width=width, minwidth=36, anchor="e", stretch=True)
        scroll = ttk.Scrollbar(listing, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")
        self.tree.bind("<Double-1>", self._open)
        self._shown: list[tuple] = []

    def show(self, rows: list[Row], status: str, colour: str, can_run: bool) -> None:
        def pct(row: Row, name: str) -> str:
            return f"{row[name] / row['games'] * 100:.0f} %" if row[name] is not None and row["games"] else "–"

        values = [(int(row["epoch"]), pct(row, "wins"), pct(row, "losses"), pct(row, "stopped"),
                   fmt(row["score"], 1, sign=True), fmt(row["unit_turns"], 0))
                  for row in reversed(rows) if row["epoch"] is not None]
        if values != self._shown:
            self._shown = values
            self.tree.delete(*self.tree.get_children())
            for value in values:
                self.tree.insert("", "end", values=value)
        self.status.config(text=status, fg=colour)
        self.button.state(["!disabled"] if can_run else ["disabled"])

    def _open(self, event: tk.Event) -> None:
        item = self.tree.identify_row(event.y)
        if item:
            self.on_open(int(self.tree.item(item, "values")[0]))


# ====================================================================== the Games tab
class BgnSource:
    """Games of a .bgn file, read incrementally: each poll parses only the lines added since the last one.

    A file that was emptied or replaced (a new training run) is read again from the start.
    """

    HEAD: int = 64  # first bytes of the file, compared to notice that it was replaced

    def __init__(self, path: Path) -> None:
        self.path: Path = path
        self.reset()

    def reset(self) -> None:
        self.records: list = []  # beastborn.game.bgn.GameRecord, in file order
        self.skipped: int = 0  # lines that are not valid BGN
        self.offset: int = 0
        self.rest: bytes = b""
        self.head: bytes = b""

    def poll(self) -> bool:
        """Read what was added since the last poll. True when the list of games changed."""
        from beastborn.game.bgn import BgnError, GameRecord

        try:
            with open(self.path, "rb") as file:
                head: bytes = file.read(self.HEAD)
                size: int = file.seek(0, 2)
                replaced: bool = size < self.offset or not head.startswith(self.head)
                if replaced:
                    self.reset()
                file.seek(self.offset)
                data: bytes = file.read(size - self.offset)
        except OSError:
            return False
        self.head = head
        self.offset += len(data)
        lines: list[bytes] = (self.rest + data).split(b"\n")
        self.rest = lines.pop()  # an unfinished last line waits for the next poll
        before: int = len(self.records)
        for line in lines:
            text: str = line.decode("utf-8", errors="replace")
            if text.strip():
                try:
                    self.records.append(GameRecord.from_line(text))
                except BgnError:
                    self.skipped += 1
        return replaced or len(self.records) != before


def game_epochs(records: list, runs: dict[str, list[Row]]) -> tuple[list[int | None], int]:
    """The epoch that played each game of ``records`` (None = unknown), and the last epoch with a statistics row.

    The run is the newest one of ``runs`` that started before the first game and whose first epoch ended after it
    (the trainer empties the .bgn file when a run starts). Its rows' "games" counts split the games in file order;
    games after the last row belong to the epoch(s) being played.
    """
    unknown: tuple[list[int | None], int] = [None] * len(records), 0
    if not records or not runs:
        return unknown
    first: float = records[0].game_id / 1000
    best_start: float | None = None
    rows: list[Row] = []
    for name, run_rows in runs.items():
        start = _parse_time(name)
        if run_rows and start is not None and start <= first + 1 and (best_start is None or start >= best_start):
            best_start, rows = start, run_rows
    rows = [row for row in rows if row["epoch"] is not None and row["games"]]
    ended = _parse_time(rows[0]["time"]) if rows else None
    if not rows or ended is None or ended + 1 < first:  # the games are not from a run in the statistics file
        return unknown
    epochs: list[int | None] = []
    for row in rows:
        epochs.extend([int(row["epoch"])] * min(int(row["games"]), len(records) - len(epochs)))
        if len(epochs) >= len(records):
            break
    finished: int = int(rows[-1]["epoch"])
    per_epoch: int = int(rows[-1]["games"])
    for extra in range(len(records) - len(epochs)):
        epochs.append(finished + 1 + extra // per_epoch)
    return epochs, finished


class GameList:
    """One list of the Games tab: its .bgn file and how to find the epoch of each of its games."""

    def __init__(self, epochs_of, unknown: str) -> None:
        """``epochs_of(records)``: (the epoch of each record or None, the last epoch that is finished)."""
        self.epochs_of = epochs_of
        self.unknown: str = unknown  # shown when no game has an epoch
        self.source: BgnSource | None = None


class GamesTab(tk.Frame):
    """Pick a recorded game (BGN) on the left and watch it on the right: step by step, backwards or playing."""

    def __init__(self, master: tk.Misc, training_epochs=lambda records: ([None] * len(records), 0),
                 benchmark_epochs=lambda records: ([None] * len(records), 0)) -> None:
        """``training_epochs`` / ``benchmark_epochs``: the epochs of the games of the two lists (see GameList)."""
        super().__init__(master, background=BG)
        self.lists: dict[str, GameList] = {
            TRAINING_GAMES: GameList(training_epochs, "epochs unknown: no statistics of this run"),
            BENCHMARK_GAMES: GameList(benchmark_epochs, "epochs unknown: no benchmark row for these games"),
        }
        self.kind: str = TRAINING_GAMES  # the list shown
        self.epochs: list[int | None] = []  # per game of self.source.records: the epoch that played it
        self.finished: int = 0  # the last epoch with a statistics row (later ones are still being played)
        self.epoch_filter: int | None = None  # list only the games of this epoch (None = all)
        self.epoch_choices: list[int | None] = [None]  # what each entry of the Epoch box stands for
        self._records: list | None = None  # the records list that is listed (a new one = the file was replaced)
        self._listed: int = 0  # games of self._records already considered for the list
        self.replay = None  # beastborn.ui.replay.Replay of the chosen game
        self.playing: bool = False
        self._job: str | None = None  # the next automatic step (after id)
        self._moving_scale: bool = False  # the position slider is being set by the code, not dragged

        # ---- top bar
        bar = tk.Frame(self, background=BG, padx=14, pady=10)
        bar.pack(fill="x")
        tk.Label(bar, text="Games", bg=BG, fg=MUTED, font=FONT).pack(side="left")
        self.kind_box = ttk.Combobox(bar, state="readonly", width=16, values=tuple(self.lists))
        self.kind_box.current(0)
        self.kind_box.pack(side="left", padx=(6, 12))
        self.kind_box.bind("<<ComboboxSelected>>", lambda _e: self.select(self.kind_box.get()))
        ttk.Button(bar, text="Open .bgn…", command=self.choose_file).pack(side="left")
        self.file_label = tk.Label(bar, text="no file", bg=BG, fg=MUTED, font=FONT)
        self.file_label.pack(side="left", padx=(10, 14))

        body = tk.Frame(self, background=BG, padx=10, pady=4)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        # ---- the list of games, newest first
        listing = tk.Frame(body, background=BG)
        listing.grid(row=0, column=0, sticky="nsw", padx=(4, 10))
        filters = tk.Frame(listing, background=BG)
        filters.pack(side="top", fill="x", pady=(0, 6))
        tk.Label(filters, text="Epoch", bg=BG, fg=MUTED, font=FONT).pack(side="left")
        self.epoch_box = ttk.Combobox(filters, state="readonly", width=30, values=("all epochs",))
        self.epoch_box.current(0)
        self.epoch_box.pack(side="left", padx=(6, 10))
        self.epoch_box.bind("<<ComboboxSelected>>", lambda _e: self.pick_epoch())
        self.shown_label = tk.Label(filters, text="", bg=BG, fg=MUTED, font=FONT_SMALL, anchor="w")
        self.shown_label.pack(side="left", fill="x", expand=True)
        columns = (("number", "#", 44), ("epoch", "Epoch", 50), ("created", "Created", 112), ("seed", "Seed", 60),
                   ("players", "Players", 150), ("commands", "Steps", 50))
        self.tree = ttk.Treeview(listing, columns=[c[0] for c in columns], show="headings", selectmode="browse")
        for name, title, width in columns:
            self.tree.heading(name, text=title)
            self.tree.column(name, width=width, anchor="w" if name == "players" else "e", stretch=False)
        scroll = ttk.Scrollbar(listing, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="y")
        scroll.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.pick_game())

        # ---- the chosen game
        viewer = tk.Frame(body, background=BG)
        viewer.grid(row=0, column=1, sticky="nsew")
        self.players = tk.Frame(viewer, background=BG)
        self.players.pack(fill="x")
        self.chips: list[tuple[tk.Frame, tk.Label, tk.Label]] = []  # per player: frame, name, score
        self.result = tk.Label(viewer, text="", bg=BG, fg=TEXT, font=FONT_BIG, anchor="w")
        self.result.pack(fill="x", pady=(6, 0))
        self.status = tk.Label(viewer, text="Pick a game on the left", bg=BG, fg=MUTED, font=FONT, anchor="w")
        self.status.pack(fill="x")

        controls = tk.Frame(viewer, background=BG, pady=6)
        controls.pack(side="bottom", fill="x")
        for text, command in (("|<", self.first), ("<", self.backward)):
            ttk.Button(controls, text=text, width=4, command=command).pack(side="left", padx=(0, 4))
        self.play_button = ttk.Button(controls, text="Play", width=7, command=self.toggle_play)
        self.play_button.pack(side="left", padx=(0, 4))
        for text, command in ((">", self.forward), (">|", self.last)):
            ttk.Button(controls, text=text, width=4, command=command).pack(side="left", padx=(0, 4))
        tk.Label(controls, text="ms / step", bg=BG, fg=MUTED, font=FONT).pack(side="right")
        self.delay = tk.IntVar(value=DEFAULT_DELAY_MS)
        ttk.Spinbox(controls, from_=50, to=5000, increment=50, width=6,
                    textvariable=self.delay).pack(side="right", padx=(12, 6))
        self.position = ttk.Scale(controls, from_=0, to=1, orient="horizontal", command=self.drag)
        self.position.pack(side="left", fill="x", expand=True, padx=(8, 0))

        middle = tk.Frame(viewer, background=BG)
        middle.pack(fill="both", expand=True, pady=(6, 0))
        self.log = tk.Text(middle, width=48, bg=PANEL, fg=TEXT, font=FONT_SMALL, borderwidth=0, wrap="word",
                           padx=8, pady=6, state="disabled")
        self.log.pack(side="right", fill="y", padx=(8, 0))
        self.board = tk.Canvas(middle, background=PANEL, highlightthickness=0, width=420, height=420)
        self.board.pack(side="left", fill="both", expand=True)
        self.board.bind("<Configure>", lambda _e: self.draw_board())

        top = self.winfo_toplevel()
        for key, action in (("<Right>", self.forward), ("<Left>", self.backward), ("<space>", self.toggle_play),
                            ("<Home>", self.first), ("<End>", self.last)):
            top.bind(key, lambda _e, act=action: self._key(act), add="+")

    # ---------------------------------------------------------- file and list
    @property
    def source(self) -> BgnSource | None:
        """The .bgn file of the list shown."""
        return self.lists[self.kind].source

    def choose_file(self) -> None:
        name = filedialog.askopenfilename(title="Recorded games",
                                          filetypes=[("Beastborn games", "*.bgn"), ("All files", "*.*")])
        if name:
            self.open(Path(name))

    def _can_replay(self) -> bool:
        try:
            import beastborn.ui.replay  # noqa: F401  (the game modules: fail here, not on the first click)
        except ImportError as error:
            self.file_label.config(text=f"the Games tab needs the project's packages (requirements.txt): {error}",
                                   fg=WARN)
            return False
        return True

    def open(self, path: Path, kind: str | None = None) -> None:
        """Show the games of ``path`` in list ``kind`` (default: benchmark games for a .benchmark.bgn file)."""
        if not self._can_replay():
            return
        kind = kind or (BENCHMARK_GAMES if path.name.endswith(BENCHMARK_NAME + ".bgn") else TRAINING_GAMES)
        self.lists[kind].source = BgnSource(path)
        self.select(kind)

    def set_file(self, kind: str, path: Path) -> None:
        """The file of list ``kind``, without showing it (the benchmark games of the opened statistics)."""
        source = self.lists[kind].source
        if source is not None and source.path == path:
            return
        self.lists[kind].source = BgnSource(path)
        if kind == self.kind and self._can_replay():
            self.select(kind)

    def select(self, kind: str) -> None:
        """Show list ``kind``, all its epochs."""
        self.kind = kind
        self.kind_box.current(tuple(self.lists).index(kind))
        self.tree.delete(*self.tree.get_children())
        self._records, self._listed, self.epochs, self.finished, self.epoch_filter = None, 0, [], 0, None
        if self.source is None:
            self.file_label.config(text="no file", fg=MUTED)
            self._fill_epoch_box()
            self.shown_label.config(text="")
            return
        self.file_label.config(text=f"{self.source.path.parent.name}/{self.source.path.name}", fg=MUTED)
        self.poll()

    def show_epoch(self, kind: str, epoch: int) -> None:
        """List ``kind`` with only the games of ``epoch``, the newest one picked."""
        if kind != self.kind or self.source is None:
            self.select(kind)
        else:
            self.poll()
        if epoch not in self.epoch_choices:
            return
        self.epoch_box.current(self.epoch_choices.index(epoch))
        self.pick_epoch()
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])

    def poll(self) -> None:
        """New games of the file, and the epochs of the games as the statistics file grows."""
        if self.source is None:
            return
        changed: bool = self.source.poll()
        records = self.source.records
        epochs, finished = self.lists[self.kind].epochs_of(records)
        if not changed and records is self._records and epochs == self.epochs and finished == self.finished:
            return
        # list again when the file was replaced or the epochs of games already listed changed
        relist: bool = records is not self._records or epochs[:len(self.epochs)] != self.epochs
        self._records, self.epochs, self.finished = records, epochs, finished
        relist = self._fill_epoch_box() or relist
        if relist:
            self.tree.delete(*self.tree.get_children())
            self._listed = 0
        self._add_games()
        skipped = f", {self.source.skipped} unreadable lines" if self.source.skipped else ""
        self.file_label.config(text=f"{self.source.path.parent.name}/{self.source.path.name}  "
                                    f"({len(records)} games{skipped})")

    def _add_games(self) -> None:
        """List the games not considered yet that pass the epoch filter (newest first), and count what is shown."""
        records = self.source.records if self.source is not None else []
        for index in range(self._listed, len(records)):
            epoch = self.epochs[index] if index < len(self.epochs) else None
            if self.epoch_filter is not None and epoch != self.epoch_filter:
                continue
            record = records[index]
            created = datetime.fromtimestamp(record.game_id / 1000).strftime("%m-%d %H:%M:%S")
            self.tree.insert("", 0, iid=str(index), values=(
                index + 1, "" if epoch is None else epoch, created, record.seed, " vs ".join(record.player_names),
                len(record.entries)))
        self._listed = len(records)
        if self.replay is not None and not self.tree.selection():  # the game being watched stays selected
            shown = next((str(i) for i, r in enumerate(records) if r is self.replay.record), None)
            if shown is not None and self.tree.exists(shown):
                self.tree.selection_set(shown)
                self.tree.see(shown)
        if records and all(epoch is None for epoch in self.epochs):
            self.shown_label.config(text=self.lists[self.kind].unknown, fg=WARN)
        else:
            self.shown_label.config(text=f"{len(self.tree.get_children())} of {len(records)} games", fg=MUTED)

    def _fill_epoch_box(self) -> bool:
        """The Epoch box: all epochs, then every epoch of the games, newest first. True when the filter was reset
        because its epoch is gone."""
        counts: dict[int, int] = {}
        for epoch in self.epochs:
            if epoch is not None:
                counts[epoch] = counts.get(epoch, 0) + 1
        self.epoch_choices = [None, *sorted(counts, reverse=True)]
        labels = ["all epochs"] + [f"epoch {epoch}  ({counts[epoch]} games" +
                                   (", unfinished)" if epoch > self.finished else ")")
                                   for epoch in self.epoch_choices[1:]]
        if list(self.epoch_box["values"]) != labels:
            self.epoch_box["values"] = labels
        reset: bool = self.epoch_filter is not None and self.epoch_filter not in counts
        if reset:
            self.epoch_filter = None
        self.epoch_box.current(self.epoch_choices.index(self.epoch_filter))
        return reset

    def pick_epoch(self) -> None:
        index = self.epoch_box.current()
        choice = self.epoch_choices[index] if 0 <= index < len(self.epoch_choices) else None
        if choice == self.epoch_filter:
            return
        self.epoch_filter = choice
        self.tree.delete(*self.tree.get_children())
        self._listed = 0
        self._add_games()
        children = self.tree.get_children()
        if children:
            self.tree.see(children[0])
        self.tree.focus_set()  # arrow keys step through the game again, not through the epochs

    def pick_game(self) -> None:
        from beastborn.ui.replay import Replay

        selected = self.tree.selection()
        if not selected or self.source is None or int(selected[0]) >= len(self.source.records):
            return
        record = self.source.records[int(selected[0])]
        if self.replay is not None and self.replay.record is record:  # listed again: keep watching where it was
            return
        self.pause()
        self.replay = Replay(record)
        self.chips = []
        for child in self.players.winfo_children():
            child.destroy()
        for index, name in enumerate(record.player_names):
            chip = tk.Frame(self.players, background=PANEL, padx=10, pady=6)
            chip.pack(side="left", padx=(0, 8))
            tk.Label(chip, text="●", bg=PANEL, fg=PLAYER_COLOURS[index % len(PLAYER_COLOURS)],
                     font=FONT_TITLE).pack(side="left")
            label = tk.Label(chip, text=f"P{index + 1} {name}", bg=PANEL, fg=TEXT, font=FONT_TITLE)
            label.pack(side="left", padx=(4, 10))
            score = tk.Label(chip, text="", bg=PANEL, fg=MUTED, font=FONT)
            score.pack(side="left")
            self.chips.append((chip, label, score))
        self.show()

    # ---------------------------------------------------------- moving through the game
    def _key(self, action) -> str | None:
        """Keyboard shortcuts work only on this tab and not while typing a number."""
        focus = self.focus_get()
        if not self.winfo_ismapped() or (focus is not None and focus.winfo_class() in ("TSpinbox", "TEntry")):
            return None
        action()
        return "break"

    def forward(self) -> None:
        self._move(lambda replay: replay.step())

    def backward(self) -> None:
        self._move(lambda replay: replay.back())

    def first(self) -> None:
        self._move(lambda replay: replay.goto(0))

    def last(self) -> None:
        self._move(lambda replay: replay.goto(replay.length))

    def drag(self, value: str) -> None:
        if not self._moving_scale and self.replay is not None and round(float(value)) != self.replay.position:
            self._move(lambda replay: replay.goto(round(float(value))))

    def _move(self, change) -> None:
        """A step chosen by hand pauses the automatic play."""
        if self.replay is None:
            return
        self.pause()
        change(self.replay)
        self.show()

    def toggle_play(self) -> None:
        if self.replay is None:
            return
        if self.playing:
            self.pause()
        else:
            if self.replay.at_end:
                self.replay.goto(0)
            self.playing = True
            self._schedule()
        self.show()

    def pause(self) -> None:
        self.playing = False
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None

    def _schedule(self) -> None:
        try:
            delay = max(10, int(self.delay.get()))
        except (tk.TclError, ValueError):
            delay = DEFAULT_DELAY_MS
        self._job = self.after(delay, self._auto_step)

    def _auto_step(self) -> None:
        self._job = None
        if not self.playing or self.replay is None:
            return
        if not self.replay.step():
            self.playing = False
        self.show()
        if self.playing:
            self._schedule()

    # ---------------------------------------------------------- drawing
    def show(self) -> None:
        """Everything of the chosen game at its current step."""
        from beastborn.game.bgn import tile_name

        replay = self.replay
        if replay is None:
            return
        view = replay.view
        over = view.phase.value == "game_over"
        for (chip, label, score), player, points in zip(self.chips, view.players, replay.scores()):
            colour = AXIS if not over and player.index == view.active_player else PANEL
            for widget in (chip, *chip.winfo_children()):
                widget.config(background=colour)
            label.config(fg=MUTED if player.eliminated else TEXT)
            units = len(view.units_of(player.index))
            score.config(text=f"score {points}   units {units}" + ("   eliminated" if player.eliminated else ""))

        ended, winner = replay.outcome()
        self.result.config(text=f"Result: {replay.result_text()}",
                           fg=PLAYER_COLOURS[winner % len(PLAYER_COLOURS)] if ended and winner is not None else TEXT)
        status = f"Step {replay.position} / {replay.length}   ·   round {view.round}"
        if replay.position:
            status += f"   ·   last: {replay.record.entries[replay.position - 1]}"
        unit = view.unit(view.active_unit) if view.active_unit is not None and not over else None
        if unit is not None:
            status += f"   ·   next: P{unit.owner + 1} {unit.name} on {tile_name(unit.position)}"
        elif over:
            status += "   ·   game over"
        if replay.error:
            status += f"   ·   {replay.error}"
        self.status.config(text=status, fg=LOSS if replay.error else MUTED)

        self.play_button.config(text="Pause" if self.playing else "Play")
        self._moving_scale = True
        self.position.config(to=max(1, replay.length))
        self.position.set(replay.position)
        self._moving_scale = False

        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.insert("end", "\n".join(self._tile_names(line) for line in replay.log))
        self.log.see("end")
        self.log.config(state="disabled")
        self.draw_board()

    _POSITION = re.compile(r"\((\d+),(\d+)\)")

    @classmethod
    def _tile_names(cls, line: str) -> str:
        """A log line with tiles as on the board and in BGN: "(1,0)" -> "b1"."""
        from beastborn.domain.position import Position
        from beastborn.game.bgn import tile_name

        return cls._POSITION.sub(lambda match: tile_name(Position(int(match[1]), int(match[2]))), line)

    def draw_board(self) -> None:
        from beastborn.domain.terrain import TerrainType
        from beastborn.game.bgn import COLUMNS
        from beastborn.ui.interaction import AttackAnimation, MoveAnimation

        canvas = self.board
        canvas.delete("all")
        if self.replay is None:
            canvas.create_text(canvas.winfo_width() / 2, canvas.winfo_height() / 2, text="Pick a game on the left",
                               fill=MUTED, font=FONT)
            return
        view = self.replay.view
        board = view.board
        margin = 22  # room for the tile names
        cell = max(6.0, min((canvas.winfo_width() - 2 * margin) / board.width,
                            (canvas.winfo_height() - 2 * margin) / board.height))
        left = (canvas.winfo_width() - cell * board.width) / 2
        top = (canvas.winfo_height() - cell * board.height) / 2

        def centre(pos) -> tuple[float, float]:
            return left + (pos.x + 0.5) * cell, top + (pos.y + 0.5) * cell

        for pos in board.positions():
            tile = board.tile(pos)
            base = MUD if tile.terrain is TerrainType.MUD else GRASS
            x, y = left + pos.x * cell, top + pos.y * cell
            canvas.create_rectangle(x, y, x + cell, y + cell, outline=PANEL,
                                    fill=blend(base, HIGH_GROUND, 0.22 * tile.elevation))
            if tile.elevation:
                canvas.create_text(x + 3, y + 2, text="▲" * tile.elevation, anchor="nw", fill=BG, font=FONT_SMALL)
        for x in range(board.width):  # tile names as in BGN: a1 = top-left
            canvas.create_text(left + (x + 0.5) * cell, top + board.height * cell + 4, text=COLUMNS[x], anchor="n",
                               fill=MUTED, font=FONT_SMALL)
        for y in range(board.height):
            canvas.create_text(left - 6, top + (y + 0.5) * cell, text=str(y + 1), anchor="e", fill=MUTED,
                               font=FONT_SMALL)

        steps = self.replay.last_actions
        for step in steps:  # the last move under the units
            if isinstance(step, MoveAnimation) and len(step.path) > 1:
                canvas.create_line(*[v for pos in step.path for v in centre(pos)], arrow="last", width=3,
                                   dash=(6, 4), fill=PLAYER_COLOURS[step.owner % len(PLAYER_COLOURS)])

        over = view.phase.value == "game_over"
        for unit in view.units:
            cx, cy = centre(unit.position)
            if unit.id == view.active_unit and not over:
                canvas.create_rectangle(cx - cell / 2 + 2, cy - cell / 2 + 2, cx + cell / 2 - 2, cy + cell / 2 - 2,
                                        outline=ACTIVE, width=3)
            radius = cell * (0.38 if unit.is_boss else 0.32)
            canvas.create_oval(cx - radius, cy - radius - cell * 0.06, cx + radius, cy + radius - cell * 0.06,
                               fill=PLAYER_COLOURS[unit.owner % len(PLAYER_COLOURS)],
                               outline=BOSS_RING if unit.is_boss else BG, width=3 if unit.is_boss else 1)
            canvas.create_text(cx, cy - cell * 0.06, text=unit.stats.code, fill="white",
                               font=("Segoe UI Semibold", max(7, int(cell * 0.28))))
            bar_left, bar_y, width = cx - cell * 0.38, cy + cell * 0.36, cell * 0.76
            canvas.create_rectangle(bar_left, bar_y, bar_left + width, bar_y + 4, fill=HP_EMPTY, outline="")
            canvas.create_rectangle(bar_left, bar_y, bar_left + width * max(0, unit.hp) / unit.stats.hp, bar_y + 4,
                                    fill=HP_BAR, outline="")
            if unit.effects:
                canvas.create_oval(cx + radius - 4, cy - radius - 6, cx + radius + 4, cy - radius + 2, fill=VENOM,
                                   outline="")

        for step in steps:  # the last attacks over the units
            if isinstance(step, AttackAnimation):
                (x1, y1), (x2, y2) = centre(step.source), centre(step.target)
                canvas.create_line(x1, y1, x2, y2, arrow="last", width=3, fill=LOSS,
                                   dash=(2, 3) if step.ranged else ())
                canvas.create_text(x2 + cell * 0.3, y2 - cell * 0.42, text=f"-{step.damage}" +
                                   (" ✕" if step.killed else ""), fill=LOSS, font=FONT_TITLE)


# ====================================================================== the Network tab
# Health of an AfterstateNet weights file. Nothing about the network's size or layers is written here: the weights
# table lists every tensor of the file, the network is built from beastborn's own AfterstateNet class (its sizes read
# from the file's shapes) and the activations table lists every ReLU-like activation the network calls, found while
# it runs.
HEALTH_STATES: int = 256  # afterstates the network is checked on (the "States" box)
HEALTH_GAMES: int = 12  # they come from the newest training games (or random games when there are none)
HEALTH_AUTO_SECONDS: float = 60.0  # "Auto": a new check when the file changed, at most once per this many seconds
ZERO_EPS: float = 1e-8  # |weight| below this counts as zero
RARE_ACTIVE: float = 0.01  # a unit positive on fewer than 1 % of the states is "rarely active"
DEAD_WARN, DEAD_BAD = 0.10, 0.50  # dead units of one layer: from 10 % a warning, from 50 % a problem
ZERO_WARN, ZERO_BAD = 0.05, 0.50  # zero weights of one tensor: the same
# activations where "dead" means something (the output is never above 0); found while the network runs
ACTIVATIONS: tuple[str, ...] = ("relu", "leaky_relu", "relu6", "elu", "celu", "selu", "gelu", "quick_gelu", "silu",
                                "swish", "hardswish", "mish")
OK, WARNING, PROBLEM = "ok", "warning", "problem"
STATUS_COLOURS = {OK: TEXT, WARNING: WARN, PROBLEM: LOSS}


def read_arrays(path: Path) -> tuple[dict, dict[str, str]]:
    """The tensors of a ``.safetensors`` file as numpy arrays (file order), and its ``__metadata__``."""
    import json
    import numpy as np

    data: bytes = path.read_bytes()
    size: int = int.from_bytes(data[:8], "little")
    header: dict = json.loads(data[8:8 + size])
    body = memoryview(data)[8 + size:]
    types = {"F32": "<f4", "F16": "<f2", "F64": "<f8", "BF16": None}
    arrays: dict = {}
    for name, info in header.items():
        if name == "__metadata__":
            continue
        kind = types.get(info["dtype"])
        if kind is None:
            raise ValueError(f"{name}: {info['dtype']} tensors are not supported")
        start, end = info["data_offsets"]
        arrays[name] = np.frombuffer(body[start:end], dtype=kind).reshape(info["shape"]).astype(np.float32)
    return arrays, dict(header.get("__metadata__", {}))


def training_weights(stats: Path | None) -> Path:
    """The weights file the trainer writes: the ``save every N to <file>`` of its newest log line (the ``.log`` next
    to the statistics file), else AI_WEIGHTS_FILE (the trainer's default ``--out``)."""
    log: Path | None = None if stats is None else stats if stats.suffix.lower() == ".log" else stats.with_suffix(".log")
    found: str | None = None
    try:
        if log is not None:
            with open(log, encoding="utf-8", errors="replace") as file:
                for line in file:
                    if "save every" in line:
                        match = re.search(r"save every \d+ to (.+?)\s*$", line)
                        found = match.group(1) if match else found
    except OSError:
        pass
    if found:
        path = Path(found)
        for candidate in ((path,) if path.is_absolute() else (log.parent / path, Path.cwd() / path)):
            if candidate.is_file():
                return candidate.resolve()
    return C.AI_WEIGHTS_FILE


@dataclass
class TensorHealth:
    """One tensor of the weights file."""

    name: str
    shape: tuple
    values: object  # np.ndarray, flat
    mean: float
    std: float
    absmax: float
    zero: float  # share of weights with |w| < ZERO_EPS
    zero_units: int  # output units (first axis) whose weights are all zero; 0 for 1-D tensors
    units: int  # size of the first axis (1-D tensors: 0)
    nonfinite: int  # NaN / inf weights
    change: float | None = None  # ||w - w_before|| / ||w_before|| against the previous save (None = none seen)
    grad_zero: float | None = None  # share of weights whose gradient is exactly 0 on the states
    grad_norm: float | None = None  # ||grad|| / ||w||

    @property
    def status(self) -> str:
        if self.nonfinite or self.zero >= ZERO_BAD or (self.units and self.zero_units >= self.units * ZERO_BAD) \
                or (self.grad_zero is not None and self.grad_zero >= ZERO_BAD):
            return PROBLEM
        if self.zero >= ZERO_WARN or self.zero_units or (self.grad_zero is not None and self.grad_zero >= ZERO_WARN) \
                or self.change == 0:
            return WARNING
        return OK


@dataclass
class LayerHealth:
    """One activation the network called: which layer's output went into it, and how its units behaved."""

    name: str
    shape: tuple  # of one state's output
    active: object  # np.ndarray (units,): share of the states (and positions) where a unit's output is > 0
    mean: float
    std: float
    nonfinite: int

    @property
    def units(self) -> int:
        return len(self.active)

    @property
    def dead(self) -> int:
        return int((self.active == 0).sum())

    @property
    def rare(self) -> int:
        return int(((self.active > 0) & (self.active < RARE_ACTIVE)).sum())

    @property
    def status(self) -> str:
        if self.nonfinite or self.dead >= self.units * DEAD_BAD:
            return PROBLEM
        if self.dead >= max(1, self.units * DEAD_WARN) or self.rare >= self.units * 0.25:
            return WARNING
        return OK


@dataclass
class HealthReport:
    path: Path
    mtime: float
    arrays: dict  # name -> np.ndarray, for the next check's "change"
    tensors: list[TensorHealth]
    slope: float | None = None  # LeakyReLU slope of the file (None = not kept: plain ReLU)
    sizes: str = ""  # the AfterstateNet arguments read from the shapes
    layers: list[LayerHealth] | None = None
    output: object = None  # np.ndarray: the network's prediction for every state
    states: int = 0
    states_from: str = ""
    error: str = ""  # why the network could not be run (the weights table is still filled)
    seconds: float = 0.0


def tensor_health(name: str, array, before) -> TensorHealth:
    import numpy as np

    flat = array.reshape(-1)
    finite = flat[np.isfinite(flat)]
    rows = array.reshape(array.shape[0], -1) if array.ndim >= 2 else None
    change = None
    if before is not None and before.shape == array.shape:
        change = float(np.linalg.norm(array - before) / max(float(np.linalg.norm(before)), 1e-12))
    return TensorHealth(
        name=name, shape=tuple(array.shape), values=flat,
        mean=float(finite.mean()) if finite.size else float("nan"),
        std=float(finite.std()) if finite.size else float("nan"),
        absmax=float(np.abs(finite).max()) if finite.size else float("nan"),
        zero=float((np.abs(flat) < ZERO_EPS).mean()) if flat.size else 0.0,
        zero_units=int((np.abs(rows) < ZERO_EPS).all(axis=1).sum()) if rows is not None else 0,
        units=int(array.shape[0]) if rows is not None else 0,
        nonfinite=int(flat.size - finite.size), change=change)


def _network_sizes(arrays: dict) -> dict[str, int]:
    """AfterstateNet's size arguments that its __init__ takes, read from the tensor shapes: ``channels`` / ``filters``
    = input / output planes of the first convolution, ``blocks`` = how many ``blocks.<n>.`` there are, ``size`` =
    board side, from the first linear layer's inputs and the last convolution's outputs."""
    import math

    convs = [a.shape for a in arrays.values() if a.ndim == 4]
    linears = [a.shape for a in arrays.values() if a.ndim == 2]
    sizes: dict[str, int] = {}
    if convs:
        sizes["filters"], sizes["channels"] = int(convs[0][0]), int(convs[0][1])
        if linears and linears[0][1] % convs[-1][0] == 0:
            sizes["size"] = math.isqrt(linears[0][1] // convs[-1][0])
    blocks = {m.group(1) for name in arrays if (m := re.match(r"blocks\.(\d+)\.", name))}
    if blocks:
        sizes["blocks"] = len(blocks)
    return sizes


def build_network(arrays: dict, slope: float):
    """beastborn's AfterstateNet with these weights; its sizes are taken from the shapes of the tensors."""
    import inspect

    from tinygrad import Tensor
    from tinygrad.nn.state import get_state_dict, load_state_dict

    from beastborn.control.ai_find_action_strategy import AfterstateNet

    accepted = inspect.signature(AfterstateNet.__init__).parameters
    sizes = {k: v for k, v in _network_sizes(arrays).items() if k in accepted}
    if "leaky_slope" in accepted:
        sizes["leaky_slope"] = slope
    net = AfterstateNet(**sizes)
    expected = get_state_dict(net)
    missing, extra = expected.keys() - arrays.keys(), arrays.keys() - expected.keys()
    if missing or extra:
        raise ValueError("the file does not fit AfterstateNet"
                         + (f"; missing: {', '.join(sorted(missing))}" if missing else "")
                         + (f"; not used: {', '.join(sorted(extra))}" if extra else ""))
    load_state_dict(net, {name: Tensor(array.copy()) for name, array in arrays.items()}, verbose=False)
    return net, {k: v for k, v in sizes.items() if k != "leaky_slope"}


def sample_states(records: list, count: int, channels: int):
    """``count`` afterstates as the network sees them in training (each one for the player who just moved), evenly
    taken from the moves of ``records`` (recorded games); from random games when there are none. And where from."""
    import random

    import numpy as np

    from beastborn import constant as BC
    from beastborn.control.ai_find_action_strategy import AIFindActionStrategy
    from beastborn.control.find_action_strategy import RandomAllFindActionStrategy
    from beastborn.game.setup import new_game

    if channels != BC.AI_CHANNELS:
        raise ValueError(f"the network reads {channels} input planes, the game encodes {BC.AI_CHANNELS}")
    encoder = AIFindActionStrategy()  # only its board encoding (no network is built)
    states: list = []
    games: int = 0

    def play(gsm, commands) -> None:
        for command in commands:
            me = gsm.active_player
            if command is None or not gsm.submit(command).ok:
                return
            states.append(encoder.state_to_tensor(gsm, me))

    for record in reversed(records):
        if (record.width, record.height) != (BC.AI_BOARD_SIZE, BC.AI_BOARD_SIZE) or record.num_players != 2:
            continue
        try:
            gsm = record.new_game()
            play(gsm, (entry.to_command(gsm) for entry in record.entries))
            games += 1
        except Exception:  # a record that does not fit the game any more: skip it
            continue
    source = f"{games} newest training games"
    if not states:  # no recorded games: a few random ones
        rng = random.Random(1)
        for seed in range(1, 5):
            gsm = new_game(2, seed, BC.AI_BOARD_SIZE, BC.AI_BOARD_SIZE)
            strategy = RandomAllFindActionStrategy(rng)

            def moves(gsm=gsm, strategy=strategy):
                for _ in range(BC.BENCHMARK_MAX_TURNS):
                    actions = strategy.find_all_possible_actions(gsm)
                    yield actions[0] if actions else None

            play(gsm, moves())
        source = "4 random games (no training games found)"
    if not states:
        raise ValueError("no states to check the network on")
    picked = np.linspace(0, len(states) - 1, min(count, len(states))).round().astype(int)
    return np.stack([states[i] for i in picked]).astype(np.float32), f"{len(picked)} states of {source}"


class _Probe:
    """Stands in for one layer (any object the network calls) and remembers its output, to name the activations."""

    def __init__(self, inner, name: str, recorder: _Recorder) -> None:
        self._inner, self._name, self._recorder = inner, name, recorder

    def __call__(self, *args, **kwargs):
        self._recorder.stack.append(self._name)
        try:
            out = self._inner(*args, **kwargs)
        finally:
            self._recorder.stack.pop()
        self._recorder.last = (self._name, out)
        return out

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


class _Recorder:
    """While the network runs: every ReLU-like activation it calls, named after the layer whose output it got."""

    def __init__(self) -> None:
        self.stack: list[str] = []  # layers being called, outermost first
        self.last: tuple[str, object] | None = None  # (name, output) of the layer called last
        self.layers: list[tuple[str, object]] = []  # (name, np.ndarray output)
        self.depth: int = 0  # inside an activation (one built from others is recorded once)

    @staticmethod
    def _is_layer(value) -> bool:
        from tinygrad import Tensor

        return (callable(value) and hasattr(value, "__dict__") and not isinstance(value, (Tensor, type))
                and not _is_function(value))

    def wrap(self, obj, prefix: str = "") -> None:
        """Put a _Probe in front of every layer of ``obj`` (its attributes and lists of them, any depth)."""
        for attr, value in list(vars(obj).items()):
            name = f"{prefix}{attr}"
            if isinstance(value, list):
                for i, item in enumerate(value):
                    if self._is_layer(item):
                        self.wrap(item, f"{name}.{i}.")
                        value[i] = _Probe(item, f"{name}.{i}", self)
            elif self._is_layer(value):
                self.wrap(value, f"{name}.")
                setattr(obj, attr, _Probe(value, name, self))

    def activation(self, original, kind: str):
        recorder = self

        def run(tensor, *args, **kwargs):
            if recorder.depth:
                return original(tensor, *args, **kwargs)
            recorder.depth += 1
            try:
                out = original(tensor, *args, **kwargs)
            finally:
                recorder.depth -= 1
            if recorder.last is not None and recorder.last[1] is tensor:
                name = f"{recorder.last[0]} → {kind}"  # straight after a layer
            else:  # after something computed in a layer (e.g. a residual sum)
                name = f"{recorder.stack[-1] if recorder.stack else 'net'} (sum) → {kind}"
            taken = sum(1 for n, _ in recorder.layers if n == name or n.startswith(name + " #"))
            recorder.layers.append((name if not taken else f"{name} #{taken + 1}", out.numpy()))
            return out

        return run


def _is_function(value) -> bool:
    import functools
    import types

    return isinstance(value, (types.FunctionType, types.MethodType, types.BuiltinFunctionType, functools.partial))


def layer_health(name: str, output) -> LayerHealth:
    import numpy as np

    units = output.reshape(output.shape[0], output.shape[1], -1) if output.ndim >= 2 else output.reshape(-1, 1, 1)
    finite = np.isfinite(units)
    return LayerHealth(name=name, shape=tuple(output.shape[1:]), active=(units > 0).mean(axis=(0, 2)),
                       mean=float(units[finite].mean()) if finite.any() else float("nan"),
                       std=float(units[finite].std()) if finite.any() else float("nan"),
                       nonfinite=int((~finite).sum()))


def run_network(net, states) -> tuple[list[LayerHealth], object, dict]:
    """Run ``net`` on ``states``: every activation's health, the predictions, and the gradient of their mean for
    every weight (name -> np.ndarray). ``net`` gets probes, so it is not to be used again."""
    from tinygrad import Tensor
    from tinygrad.nn.state import get_state_dict

    params = get_state_dict(net)
    for tensor in params.values():
        tensor.requires_grad = True
    out = net(Tensor(states))
    out.mean().backward()
    grads = {name: t.grad.numpy() for name, t in params.items() if t.grad is not None}

    recorder = _Recorder()
    recorder.wrap(net)
    patched: dict[str, object] = {}
    for kind in ACTIVATIONS:
        original = getattr(Tensor, kind, None)
        if original is not None:
            patched[kind] = Tensor.__dict__.get(kind)
            setattr(Tensor, kind, recorder.activation(original, kind))
    try:
        predicted = net(Tensor(states)).numpy()
    finally:
        for kind, original in patched.items():
            if original is None:
                delattr(Tensor, kind)  # it came from a base class
            else:
                setattr(Tensor, kind, original)
    return [layer_health(name, output) for name, output in recorder.layers], predicted, grads


def check_network(path: Path, before: dict | None, records: list, count: int) -> HealthReport:
    """The whole health check of the weights file ``path`` (``before``: the arrays of its previous save)."""
    import numpy as np

    started: float = time.time()
    mtime: float = path.stat().st_mtime
    arrays, metadata = read_arrays(path)
    report = HealthReport(path=path, mtime=mtime, arrays=arrays,
                          tensors=[tensor_health(name, a, (before or {}).get(name)) for name, a in arrays.items()])
    report.slope = float(metadata["leaky_slope"]) if "leaky_slope" in metadata else None
    try:
        net, sizes = build_network(arrays, report.slope or 0.0)
        report.sizes = ", ".join(f"{k} {v}" for k, v in sizes.items())
        states, report.states_from = sample_states(records, count, sizes.get("channels", C.AI_CHANNELS))
        report.states = len(states)
        report.layers, report.output, grads = run_network(net, states)
        for tensor in report.tensors:
            grad = grads.get(tensor.name)
            if grad is not None:
                tensor.grad_zero = float((grad == 0).mean())
                tensor.grad_norm = float(np.linalg.norm(grad) / max(float(np.linalg.norm(arrays[tensor.name])),
                                                                     1e-12))
    except Exception as error:  # the weights table is still worth showing
        report.error = f"{type(error).__name__}: {error}"
    report.seconds = time.time() - started
    return report


class HealthTab(tk.Frame):
    """Health of an AfterstateNet weights file: zero weights, dead units, gradients and how much the weights change.

    By default the file the trainer writes (``training_weights``), checked again when it changes ("Auto"); "Open
    weights…" checks any other one.
    """

    def __init__(self, master: tk.Misc, stats=lambda: None, records=lambda: []) -> None:
        """``stats()``: the statistics file of the Training tab (to find the trainer's weights file); ``records()``:
        recorded training games (their states are the network's input)."""
        super().__init__(master, background=BG)
        self.stats, self.records = stats, records
        self.follow: bool = True  # the trainer's weights file
        self.path: Path | None = None
        self.versions: dict[Path, list[tuple[float, dict]]] = {}  # per file: (mtime, arrays) of its last 2 saves
        self.report: HealthReport | None = None
        self.job = None  # concurrent.futures.Future of the check being run
        self.checked_at: float = 0.0
        self.executor = None
        self._cells: list[tuple[float, float, float, float, str]] = []  # unit map: rectangles and their text

        # ---- top bar
        bar = tk.Frame(self, background=BG, padx=14, pady=10)
        bar.pack(fill="x")
        ttk.Button(bar, text="Training weights", command=self.use_training).pack(side="left")
        ttk.Button(bar, text="Open weights…", command=self.choose_file).pack(side="left", padx=(6, 0))
        self.file_label = tk.Label(bar, text="", bg=BG, fg=MUTED, font=FONT)
        self.file_label.pack(side="left", padx=(10, 14))
        self.status = tk.Label(bar, text="", bg=BG, fg=MUTED, font=FONT)
        self.status.pack(side="right")
        self.check_button = ttk.Button(bar, text="Check now", command=self.check)
        self.check_button.pack(side="right", padx=(12, 12))
        self.auto = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Auto", variable=self.auto).pack(side="right")
        self.count = tk.IntVar(value=HEALTH_STATES)
        ttk.Spinbox(bar, from_=16, to=4096, increment=64, width=6, textvariable=self.count).pack(side="right",
                                                                                                padx=(6, 14))
        tk.Label(bar, text="States", bg=BG, fg=MUTED, font=FONT).pack(side="right")

        # ---- cards
        cards = tk.Frame(self, background=BG, padx=10)
        cards.pack(fill="x")
        names = ("Health", "Parameters", "Zero weights", "Dead units", "No gradient", "Changed", "Output")
        self.cards: dict[str, Card] = {}
        for column, name in enumerate(names):
            card = Card(cards, name)
            card.grid(row=0, column=column, sticky="nsew", padx=4, pady=4)
            cards.columnconfigure(column, weight=1, uniform="cards")
            self.cards[name] = card

        # ---- tables on the left, pictures on the right
        body = tk.Frame(self, background=BG, padx=10, pady=4)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=8, uniform="body")
        body.columnconfigure(1, weight=5, uniform="body")
        body.rowconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)
        weights = (("name", "Weights", 150, "w"), ("shape", "Shape", 82, "w"), ("mean", "Mean", 58, "e"),
                   ("std", "Std", 50, "e"), ("absmax", "|max|", 50, "e"), ("zero", "Zero", 50, "e"),
                   ("zero_units", "Zero units", 66, "e"), ("change", "Δ last save", 74, "e"),
                   ("grad_zero", "Grad 0", 52, "e"), ("grad_norm", "|grad|/|w|", 66, "e"), ("status", "", 62, "w"))
        layers = (("name", "Activations", 200, "w"), ("shape", "Output", 80, "w"), ("units", "Units", 48, "e"),
                  ("dead", "Dead", 82, "e"), ("rare", "Rare", 46, "e"), ("active", "Active", 58, "e"),
                  ("mean", "Mean", 58, "e"), ("std", "Std", 52, "e"), ("status", "", 62, "w"))
        self.weights = self._table(body, weights, 0, "Zero: |w| < 1e-8 · Zero units: output units with only zero "
                                                     "weights · Grad 0: weights with no gradient on the states")
        self.layers = self._table(body, layers, 1, "Dead: never above 0 on any state (any position) · Rare: above 0 "
                                                   "on < 1 % · Active: mean share above 0")
        self.weights.bind("<<TreeviewSelect>>", lambda _e: self.pick(self.weights))
        self.layers.bind("<<TreeviewSelect>>", lambda _e: self.pick(self.layers))

        side = tk.Frame(body, background=BG)
        side.grid(row=0, column=1, rowspan=2, sticky="nsew", padx=(8, 0))
        self.map = tk.Canvas(side, background=PANEL, highlightthickness=0)
        self.map.pack(fill="both", expand=True)
        self.map.bind("<Configure>", lambda _e: self.draw_map())
        self.map.bind("<Motion>", self._map_motion)
        self.map_note = tk.Label(side, text="", bg=BG, fg=MUTED, font=FONT_SMALL, anchor="w")
        self.map_note.pack(fill="x", pady=(2, 6))
        self.histogram = tk.Canvas(side, background=PANEL, highlightthickness=0, height=190)
        self.histogram.pack(fill="x")
        self.histogram.bind("<Configure>", lambda _e: self.draw_histogram())
        self.shown: tuple[str, object, str] | None = None  # histogram: (title, values, what is counted)
        self.show_report()

    def _table(self, master: tk.Misc, columns, row: int, note: str) -> ttk.Treeview:
        frame = tk.Frame(master, background=BG)
        frame.grid(row=row, column=0, sticky="nsew", pady=(0, 6))
        tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings", selectmode="browse")
        for name, title, width, anchor in columns:
            tree.heading(name, text=title, anchor=anchor)
            tree.column(name, width=width, minwidth=40, anchor=anchor, stretch=name == "name")
        for status, colour in STATUS_COLOURS.items():
            tree.tag_configure(status, foreground=colour)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        if note:
            tk.Label(frame, text=note, bg=BG, fg=MUTED, font=FONT_SMALL, anchor="w").pack(side="bottom", fill="x")
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")
        return tree

    # ---------------------------------------------------------- which file
    def use_training(self) -> None:
        self.follow = True
        self.check()

    def choose_file(self) -> None:
        name = filedialog.askopenfilename(title="Network weights", initialdir=str(C.AI_WEIGHTS_FILE.parent),
                                          filetypes=[("Weights", "*.safetensors*"), ("All files", "*.*")])
        if name:
            self.follow = False
            self.path = Path(name)
            self.check()

    def target(self) -> Path:
        if self.follow or self.path is None:
            self.path = training_weights(self.stats())
        return self.path

    # ---------------------------------------------------------- checking
    def visible(self) -> bool:
        try:
            return self.master.select() == str(self)
        except tk.TclError:
            return False

    def poll(self) -> None:
        """A finished check is shown; with "Auto", a changed file is checked again while the tab is shown."""
        if self.job is not None:
            if self.job.done():
                job, self.job = self.job, None
                try:
                    self.report = job.result()
                except Exception as error:  # the file could not be read (e.g. being replaced): try again later
                    self.report, self.checked_at = None, 0.0
                    self.status.config(text=f"could not check: {error}", fg=WARN)
                else:
                    versions = self.versions.setdefault(self.report.path, [])
                    if not versions or versions[-1][0] != self.report.mtime:
                        versions.append((self.report.mtime, self.report.arrays))
                        del versions[:-2]
                self.show_report()
            return
        path = self.target()
        self._file_text(path)
        if not self.visible() or not self.auto.get():
            return
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return
        report = self.report
        new = report is None or report.path != path or report.mtime != mtime
        if new and time.time() - self.checked_at >= (HEALTH_AUTO_SECONDS if report and report.path == path else 0):
            self.check()

    def check(self) -> None:
        if self.job is not None:
            return
        path = self.target()
        self._file_text(path)
        if not path.is_file():
            self.status.config(text=f"no weights file {path.name}", fg=WARN)
            return
        try:
            mtime = path.stat().st_mtime
            count = max(1, int(self.count.get()))
        except (OSError, tk.TclError, ValueError):
            return
        before = next((arrays for m, arrays in reversed(self.versions.get(path, [])) if m != mtime), None)
        try:
            records = list(self.records())[-HEALTH_GAMES:]
        except Exception:
            records = []
        self.job = self._executor().submit(check_network, path, before, records, count)
        self.checked_at = time.time()
        self.status.config(text="checking… (the first check compiles the network, it takes longer)", fg=TEXT)
        self.check_button.state(["disabled"])

    def _executor(self):
        """tinygrad runs on the one thread the game's AI uses for it (tinygrad is not thread-safe)."""
        if self.executor is None:
            try:
                from beastborn.control.ai_find_action_strategy import _tinygrad_pool
                self.executor = _tinygrad_pool
            except ImportError:  # no tinygrad: the weights table only (the network check reports why)
                from concurrent.futures import ThreadPoolExecutor
                self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="health")
        return self.executor

    def _file_text(self, path: Path) -> None:
        try:
            age = time.time() - path.stat().st_mtime
            saved = f"saved {duration(age)} ago" if age >= 60 else f"saved {int(age)} s ago"
        except OSError:
            saved = "missing"
        source = "trainer's weights" if self.follow else "opened"
        self.file_label.config(text=f"{path.parent.name}/{path.name}  ({source}, {saved})")

    # ---------------------------------------------------------- showing
    def show_report(self) -> None:
        self.check_button.state(["!disabled"])
        r = self.report
        for tree in (self.weights, self.layers):
            tree.delete(*tree.get_children())
        if r is None:
            for card in self.cards.values():
                card.show("–")
            self.cards["Health"].show("–", "open the Network tab to check", MUTED)
            self.shown = None
            self.draw_map()
            self.draw_histogram()
            return
        when = datetime.fromtimestamp(r.mtime).strftime("%H:%M:%S")
        error = r.error if len(r.error) <= 120 else r.error[:117] + "…"
        self.status.config(text=error or f"checked in {r.seconds:.1f} s on {r.states_from}",
                           fg=WARN if r.error else MUTED)

        def num(value: float | None, digits: int = 3) -> str:
            if value is None:
                return "–"
            return f"{value:.{digits}g}" if abs(value) < 1e5 else f"{value:.2e}"

        def pct(value: float | None) -> str:
            return "–" if value is None else f"{value * 100:.1f} %" if value < 0.995 or value == 1 else "99.5+ %"

        for i, t in enumerate(r.tensors):
            self.weights.insert("", "end", iid=f"w{i}", tags=(t.status,), values=(
                t.name, "×".join(map(str, t.shape)), num(t.mean), num(t.std), num(t.absmax), pct(t.zero), f"{t.zero_units} / {t.units}" if t.units else "–", pct(t.change),
                pct(t.grad_zero), num(t.grad_norm), "" if t.status == OK else t.status))
        for i, layer in enumerate(r.layers or []):
            self.layers.insert("", "end", iid=f"l{i}", tags=(layer.status,), values=(
                layer.name, "×".join(map(str, layer.shape)), layer.units,
                f"{layer.dead} ({layer.dead / max(1, layer.units) * 100:.0f} %)", layer.rare,
                pct(float(layer.active.mean())), num(layer.mean), num(layer.std),
                "" if layer.status == OK else layer.status))

        # cards
        items = [t.status for t in r.tensors] + [layer.status for layer in r.layers or []]
        problems, warnings = items.count(PROBLEM), items.count(WARNING)
        checked = f"{len(r.tensors)} tensors, {len(r.layers or [])} activations"
        if r.error:
            self.cards["Health"].show("?", "the network did not run", WARN, "weights only, see the top right")
        elif problems:
            self.cards["Health"].show("problem", f"{problems} problems, {warnings} warnings", LOSS, checked)
        elif warnings:
            self.cards["Health"].show("warning", f"{warnings} warnings", WARN, checked)
        else:
            self.cards["Health"].show("healthy", "no zero weights or dead units", WIN, checked)
        total = sum(len(t.values) for t in r.tensors)
        slope = "ReLU (no slope kept)" if r.slope is None else f"LeakyReLU slope {r.slope:g}"
        self.cards["Parameters"].show(f"{total:,}".replace(",", " "), f"{len(r.tensors)} tensors", MUTED, slope)
        zero = sum(t.zero * len(t.values) for t in r.tensors) / max(1, total)
        zero_units = sum(t.zero_units for t in r.tensors)
        nonfinite = sum(t.nonfinite for t in r.tensors)
        self.cards["Zero weights"].show(
            f"{zero * 100:.2f} %", f"{nonfinite} NaN / inf" if nonfinite else f"{zero_units} all-zero units",
            LOSS if nonfinite or zero >= ZERO_BAD else WARN if zero >= ZERO_WARN or zero_units else MUTED,
            f"|w| < {ZERO_EPS:g}")
        if r.layers:
            units = sum(layer.units for layer in r.layers)
            dead = sum(layer.dead for layer in r.layers)
            rare = sum(layer.rare for layer in r.layers)
            worst = max(r.layers, key=lambda layer: layer.dead / max(1, layer.units))
            self.cards["Dead units"].show(f"{dead} / {units}", f"{rare} rarely active",
                                          STATUS_COLOURS[worst.status] if worst.dead else MUTED,
                                          f"most: {worst.name.split(' → ')[0]}" if worst.dead else "")
        else:
            self.cards["Dead units"].show("–")
        graded = [t for t in r.tensors if t.grad_zero is not None]
        if graded:
            no_grad = sum(t.grad_zero * len(t.values) for t in graded) / max(1, sum(len(t.values) for t in graded))
            frozen = [t.name for t in graded if t.grad_zero == 1]
            self.cards["No gradient"].show(f"{no_grad * 100:.2f} %", "of the weights",
                                           LOSS if frozen else WARN if no_grad >= ZERO_WARN else MUTED,
                                           f"none at all: {', '.join(frozen)}" if frozen else "weights get learning")
        else:
            self.cards["No gradient"].show("–")
        changes = [(t.change, len(t.values)) for t in r.tensors if t.change is not None]
        if changes:
            change = sum(c * n for c, n in changes) / max(1, sum(n for _c, n in changes))
            still = sum(1 for c, _n in changes if c == 0)
            self.cards["Changed"].show(f"{change * 100:.3f} %", f"{still} tensors did not change" if still
                                       else "since the save before", WARN if still else MUTED, f"save of {when}")
        else:
            self.cards["Changed"].show("–", "after the next save", MUTED, f"save of {when}")
        if r.output is not None and len(r.output):
            spread = float(r.output.std())
            self.cards["Output"].show(f"{float(r.output.mean()):+.3f}", f"spread {spread:.3g}",
                                      WARN if spread < 1e-6 else MUTED,
                                      f"{float(r.output.min()):+.2f} … {float(r.output.max()):+.2f}")
            self.shown = ("network output on the states", r.output, "states")
        else:
            self.cards["Output"].show("–")
            self.shown = None
        self.draw_map()
        self.draw_histogram()

    def pick(self, tree: ttk.Treeview) -> None:
        """A row was picked: the histogram of its weights, or of its units' active shares."""
        selected = tree.selection()
        if not selected or self.report is None:
            return
        index = int(selected[0][1:])
        if tree is self.weights:
            t = self.report.tensors[index]
            self.shown = (f"values of {t.name}", t.values, "weights")
            self.layers.selection_remove(*self.layers.selection())
        else:
            layer = (self.report.layers or [])[index]
            self.shown = (f"active share of the units of {layer.name}", layer.active * 100, "units")
            self.weights.selection_remove(*self.weights.selection())
        self.draw_histogram()

    def draw_map(self) -> None:
        """One small square per unit of every activation, coloured by how often it is above 0."""
        canvas = self.map
        canvas.delete("all")
        self._cells = []
        width = max(canvas.winfo_width(), 200)
        canvas.create_text(12, 10, text="Units  (red = dead, orange = rarely active, green = often active)",
                           anchor="nw", fill=TEXT, font=FONT_TITLE)
        if self.report is not None and self.report.sizes:
            slope = "ReLU" if self.report.slope is None else f"LeakyReLU {self.report.slope:g}"
            canvas.create_text(12, 28, text=f"AfterstateNet: {self.report.sizes} · {slope}", anchor="nw",
                               fill=MUTED, font=FONT_SMALL)
        layers = self.report.layers if self.report is not None else None
        if not layers:
            canvas.create_text(width / 2, 60, text="no activations yet" if self.report is None or not self.report.error
                               else "the network did not run", fill=MUTED, font=FONT)
            return
        height = max(canvas.winfo_height(), 200)
        # the largest square that fits all layers in the canvas
        cell = 14
        while cell > 3:
            per_row = max(1, (width - 24) // cell)
            total = 50 + sum(16 + -(-layer.units // per_row) * cell + 6 for layer in layers)
            if total <= height:
                break
            cell -= 1
        per_row = max(1, (width - 24) // cell)
        y = 50
        for layer in layers:
            canvas.create_text(12, y, text=f"{layer.name}   {layer.dead} dead / {layer.units}", anchor="nw",
                               fill=STATUS_COLOURS[layer.status] if layer.status != OK else MUTED, font=FONT_SMALL)
            y += 16
            for unit, share in enumerate(layer.active):
                x0 = 12 + (unit % per_row) * cell
                y0 = y + (unit // per_row) * cell
                colour = LOSS if share == 0 else WARN if share < RARE_ACTIVE else blend(GRID, WIN, 0.25 + 0.75 * share)
                canvas.create_rectangle(x0, y0, x0 + cell - 1, y0 + cell - 1, fill=colour, outline="")
                self._cells.append((x0, y0, x0 + cell, y0 + cell,
                                    f"{layer.name}, unit {unit}: above 0 on {share * 100:.1f} % of the states"))
            y += -(-layer.units // per_row) * cell + 6

    def _map_motion(self, event: tk.Event) -> None:
        text = next((t for x0, y0, x1, y1, t in self._cells if x0 <= event.x < x1 and y0 <= event.y < y1), "")
        self.map_note.config(text=text)

    def draw_histogram(self) -> None:
        canvas = self.histogram
        canvas.delete("all")
        width, height = max(canvas.winfo_width(), 200), max(canvas.winfo_height(), 120)
        if self.shown is None:
            canvas.create_text(width / 2, height / 2, text="pick a row to see its values", fill=MUTED, font=FONT)
            return
        import numpy as np

        title, values, counted = self.shown
        values = np.asarray(values, dtype=np.float64)
        values = values[np.isfinite(values)]
        canvas.create_text(12, 8, text=title, anchor="nw", fill=TEXT, font=FONT_TITLE)
        if not values.size:
            return
        low, high = float(values.min()), float(values.max())
        if high - low < 1e-12:
            low, high = low - 0.5, high + 0.5
        counts, _edges = np.histogram(values, bins=40, range=(low, high))
        left, right, top, bottom = 44, width - 12, 30, height - 22
        peak = max(1, int(counts.max()))
        step = (right - left) / len(counts)
        for i, n in enumerate(counts):
            if n:
                y = bottom - (bottom - top) * n / peak
                canvas.create_rectangle(left + i * step, y, left + (i + 1) * step - 1, bottom, fill=TURNS,
                                        outline="")
        canvas.create_line(left, bottom, right, bottom, fill=AXIS)
        if low < 0 < high:  # where 0 is
            x = left + (right - left) * (0 - low) / (high - low)
            canvas.create_line(x, top - 4, x, bottom, fill=WARN, dash=(3, 2))
        exact = int((np.abs(values) < ZERO_EPS).sum()) if counted == "weights" else None
        for x, value, anchor in ((left, low, "nw"), (right, high, "ne")):
            canvas.create_text(x, bottom + 4, text=f"{value:.3g}", anchor=anchor, fill=MUTED, font=FONT_SMALL)
        canvas.create_text(left - 6, top, text=str(peak), anchor="ne", fill=MUTED, font=FONT_SMALL)
        info = f"{values.size} {counted}, mean {values.mean():.3g}, std {values.std():.3g}"
        if exact is not None:
            info += f", {exact} exactly 0"
        canvas.create_text(right, 8, text=info, anchor="ne", fill=MUTED, font=FONT_SMALL)


# ====================================================================== the window
class Monitor(tk.Tk):
    def __init__(self, path: Path | None) -> None:
        super().__init__()
        self.title("Beastborn training monitor")
        self.configure(background=BG)
        self.geometry("1280x860")
        self.minsize(1100, 680)
        self.source: Source | None = None
        self.benchmark: Benchmark | None = None  # of the opened statistics file
        self.shown_run: str | None = None
        self.protocol("WM_DELETE_WINDOW", self.close)

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground=PANEL, background=PANEL, foreground=TEXT, arrowcolor=TEXT)
        style.map("TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("readonly", TEXT)])
        style.configure("TCheckbutton", background=BG, foreground=TEXT)
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("TButton", background=PANEL, foreground=TEXT, borderwidth=0, padding=(10, 4))
        style.map("TButton", background=[("active", AXIS)])
        style.configure("Panel.TButton", background=AXIS, padding=(8, 2))
        style.map("Panel.TButton", background=[("active", MUTED), ("disabled", PANEL)],
                  foreground=[("disabled", MUTED)])
        style.configure("TSpinbox", fieldbackground=PANEL, foreground=TEXT, arrowcolor=TEXT, background=PANEL)
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(16, 6), borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", AXIS)], foreground=[("selected", TEXT)])
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT, borderwidth=0,
                        rowheight=22)
        style.configure("Treeview.Heading", background=BG, foreground=MUTED, borderwidth=0)
        style.map("Treeview", background=[("selected", AXIS)], foreground=[("selected", TEXT)])
        style.configure("Horizontal.TScale", background=BG, troughcolor=PANEL)
        self.option_add("*TCombobox*Listbox.background", PANEL)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)
        training = tk.Frame(self.notebook, background=BG)
        self.notebook.add(training, text="Training")
        self.games = GamesTab(
            self.notebook,
            training_epochs=lambda records: game_epochs(records, self.source.runs if self.source else {}),
            benchmark_epochs=self.benchmark_epochs)
        self.notebook.add(self.games, text="Games")
        self.health = HealthTab(self.notebook, stats=lambda: self.source.path if self.source else None,
                                records=self.training_records)
        self.notebook.add(self.health, text="Network")
        self.notebook.bind("<<NotebookTabChanged>>", lambda _e: self.health.poll(), add="+")

        # ---- top bar
        bar = tk.Frame(training, background=BG, padx=14, pady=10)
        bar.pack(fill="x")
        ttk.Button(bar, text="Open file…", command=self.choose_file).pack(side="left")
        self.file_label = tk.Label(bar, text="no file", bg=BG, fg=MUTED, font=FONT)
        self.file_label.pack(side="left", padx=(10, 14))
        tk.Label(bar, text="Run", bg=BG, fg=MUTED, font=FONT).pack(side="left")
        self.run_box = ttk.Combobox(bar, state="readonly", width=24)
        self.run_box.pack(side="left", padx=(6, 12))
        self.run_box.bind("<<ComboboxSelected>>", lambda _e: self.pick_run())
        self.follow = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Follow newest run", variable=self.follow, command=self.refresh).pack(side="left")
        tk.Label(bar, text="Average", bg=BG, fg=MUTED, font=FONT).pack(side="left", padx=(18, 6))
        self.window = tk.IntVar(value=10)
        ttk.Spinbox(bar, from_=1, to=100, width=4, textvariable=self.window,
                    command=self.refresh).pack(side="left")
        tk.Label(bar, text="epochs", bg=BG, fg=MUTED, font=FONT).pack(side="left", padx=(6, 14))
        tk.Label(bar, text="Show", bg=BG, fg=MUTED, font=FONT).pack(side="left", padx=(0, 6))
        self.span = ttk.Combobox(bar, state="readonly", width=10,
                                 values=("all epochs", "last 25", "last 50", "last 100", "last 200"))
        self.span.current(0)
        self.span.pack(side="left")
        self.span.bind("<<ComboboxSelected>>", lambda _e: self.refresh())

        # ---- cards
        cards = tk.Frame(training, background=BG, padx=10)
        cards.pack(fill="x")
        names = ("Epoch", "Wins", "Losses", "Stopped", "Unit turns", "Score", "Loss value", "Speed")
        self.cards: dict[str, Card] = {}
        for column, name in enumerate(names):
            card = Card(cards, name)
            card.grid(row=0, column=column, sticky="nsew", padx=4, pady=4)
            cards.columnconfigure(column, weight=1, uniform="cards")
            self.cards[name] = card

        # ---- charts: the benchmark next to the training results, the other charts below
        charts = tk.Frame(training, background=BG, padx=10, pady=4)
        charts.pack(fill="both", expand=True)
        self.results = LineChart(charts, "Results per epoch", "%", fixed=(0, 100), digits=0, on_hover=self.hover)
        self.bench = LineChart(charts, f"Benchmark every {C.BENCHMARK_EVERY_EPOCHS} epochs", "%", fixed=(0, 100),
                               digits=0, on_hover=self.hover, markers=True)
        self.bench.empty = f"first benchmark after epoch {C.BENCHMARK_EVERY_EPOCHS}"
        self.panel = BenchmarkPanel(charts, on_run=self.benchmark_now, on_open=self.show_benchmark_games)
        self.turns = LineChart(charts, "Unit turns per game", on_hover=self.hover)
        self.score = LineChart(charts, "Score per game", on_hover=self.hover)
        self.loss = LineChart(charts, "Training loss", digits=4, on_hover=self.hover)
        self.charts = (self.results, self.bench, self.turns, self.score, self.loss)
        for i, widget in enumerate((self.results, self.bench, self.panel, self.turns, self.score, self.loss)):
            widget.grid(row=i // 3, column=i % 3, sticky="nsew", padx=4, pady=4)
        for i in range(3):
            charts.columnconfigure(i, weight=1, uniform="charts")
        for i in range(2):
            charts.rowconfigure(i, weight=1, uniform="charts")

        self.window.trace_add("write", lambda *_: self.refresh())
        games: Path | None = path if path is not None and path.suffix.lower() == ".bgn" else None
        asked_games: bool = games is not None
        # with a .bgn file, the statistics next to it give the epochs of its games
        path = self.find_file(CANDIDATES, games.parent) if games else path or self.find_file()
        if path:
            self.open(path)
        games = games or self.find_file(GAMES_CANDIDATES, path.parent if path else None)
        if games:
            self.games.open(games)
            if path is None or asked_games:
                self.notebook.select(self.games)
        self.after(POLL_MS, self.poll)
        self.after(1000, self.tick)

    # ---------------------------------------------------------- file
    @staticmethod
    def find_file(names: tuple[str, ...] = CANDIDATES, first: Path | None = None) -> Path | None:
        """The first of ``names`` in ``first``, the current folder, this folder or the project folder."""
        folders = (first, Path.cwd(), Path(__file__).resolve().parent, Path(__file__).resolve().parents[1])
        for folder in folders:
            for name in names:
                if folder is not None and (folder / name).is_file():
                    return folder / name
        return None

    def choose_file(self) -> None:
        name = filedialog.askopenfilename(title="Training statistics",
                                          filetypes=[("Training csv or log", "*.csv *.log"), ("All files", "*.*")])
        if name:
            self.open(Path(name))

    def open(self, path: Path) -> None:
        self.source = Source(path)
        self.shown_run = None
        self.file_label.config(text=f"{path.parent.name}/{path.name}")
        self.source.poll()
        if self.benchmark is not None:
            self.benchmark.stop()
        self.benchmark = Benchmark(path)
        self.benchmark.watch(self.source, start=False)  # epochs written before: played with other weights
        self.games.set_file(BENCHMARK_GAMES, self.benchmark.games_path)
        self.refresh()

    def poll(self) -> None:
        changed: bool = False
        if self.source is not None and self.source.poll():
            if self.benchmark is not None:
                self.benchmark.watch(self.source)  # a new epoch: its benchmark, if it is time for one
            changed = True
        if self.benchmark is not None and self.benchmark.poll():
            changed = True
        if changed:
            self.refresh()
        else:
            self.update_panel()  # the running benchmark's progress
        self.games.poll()
        self.health.poll()
        self.after(POLL_MS, self.poll)

    def close(self) -> None:
        if self.benchmark is not None:
            self.benchmark.stop()
        self.destroy()

    def training_records(self) -> list:
        """The training games of the Games tab (the Network tab checks the network on their states)."""
        source = self.games.lists[TRAINING_GAMES].source
        if source is None:
            return []
        if self.games.kind != TRAINING_GAMES:  # the list shown is polled by the Games tab, this one is not
            source.poll()
        return source.records

    # ---------------------------------------------------------- benchmark
    def newest(self) -> tuple[str, Row] | None:
        """The newest run and its last epoch row."""
        names = self.source.run_names() if self.source else []
        return (names[-1], self.source.runs[names[-1]][-1]) if names else None

    def benchmark_now(self) -> None:
        """A benchmark of the newest run's last epoch, with the weights file as it is now."""
        newest = self.newest()
        if self.benchmark is not None and newest is not None and newest[1]["epoch"] is not None:
            self.benchmark.request(newest[0], int(newest[1]["epoch"]))
            self.refresh()

    def benchmark_epochs(self, records: list) -> tuple[list[int | None], int]:
        """The Games tab: the epoch of each benchmark game."""
        b = self.benchmark
        if b is None:
            return [None] * len(records), 0
        return benchmark_epochs(records, b.source.all_rows(), (b.job.started, b.job.epoch) if b.job else None)

    def show_benchmark_games(self, epoch: int) -> None:
        self.notebook.select(self.games)
        self.games.show_epoch(BENCHMARK_GAMES, epoch)

    def update_panel(self) -> None:
        b = self.benchmark
        rows = b.source.runs.get(self.shown_run, []) if b is not None and self.shown_run else []
        newest = self.newest()
        can_run = (b is not None and newest is not None and newest[1]["epoch"] is not None
                   and not b.has(newest[0], int(newest[1]["epoch"])))
        colour = MUTED
        if b is None:
            status = "open a train_ai_bot .csv or .log file"
        elif b.job is not None:
            played, total = b.progress()
            status, colour = f"playing epoch {b.job.epoch}: game {played} / {total}", TEXT
            if b.queue:
                status += f", {len(b.queue)} waiting"
        elif b.error:
            status, colour = b.error, WARN
        elif newest is None:
            status = "waiting for the first epoch…"
        elif newest[1]["epoch"] is not None and newest[1]["epochs"] and newest[1]["epoch"] >= newest[1]["epochs"]:
            status = "the newest run is finished"
        else:
            last = int(newest[1]["epoch"] or 0)
            status = f"next after epoch {(last // C.BENCHMARK_EVERY_EPOCHS + 1) * C.BENCHMARK_EVERY_EPOCHS}"
        self.panel.show(rows, status, colour, can_run)

    # ---------------------------------------------------------- run choice
    def pick_run(self) -> None:
        names = self.source.run_names() if self.source else []
        index = self.run_box.current()
        if 0 <= index < len(names):
            self.shown_run = names[::-1][index]
            self.follow.set(index == 0)
        self.refresh()

    def rows(self) -> list[Row]:
        if self.source is None:
            return []
        names = self.source.run_names()
        labels = [f"{name}  ({len(self.source.runs[name])} ep.)" for name in names[::-1]]
        if list(self.run_box["values"]) != labels:
            self.run_box["values"] = labels
        if not names:
            return []
        if self.follow.get() or self.shown_run not in names:
            self.shown_run = names[-1]
        self.run_box.current(names[::-1].index(self.shown_run))
        return self.source.runs[self.shown_run]

    # ---------------------------------------------------------- drawing
    def refresh(self) -> None:
        try:
            window = max(1, int(self.window.get()))
        except (tk.TclError, ValueError):
            window = 10
        rows = self.rows()
        self.update_cards(rows, window)
        span = {"last 25": 25, "last 50": 50, "last 100": 100, "last 200": 200}.get(self.span.get())

        def column(name: str) -> list[float | None]:
            return [row[name] for row in rows]

        def percent(name: str) -> list[float | None]:
            return [row[name] / row["games"] * 100 if row[name] is not None and row["games"] else None
                    for row in rows]

        def series(name: str, colour: str, values: list[float | None]) -> Series:
            return Series(name, colour, values, moving_average(values, window))

        groups = (
            (self.results, [series("wins", WIN, percent("wins")), series("losses", LOSS, percent("losses")),
                            series("stopped", STOPPED, percent("stopped"))]),
            (self.turns, [series("all", TURNS, column("unit_turns")), series("won", WIN, column("win_turns")),
                          series("lost", LOSS, column("loss_turns"))]),
            (self.score, [series("score", SCORE, column("score"))]),
            (self.loss, [series("loss", TRAIN_LOSS, column("loss"))]),
        )
        epochs = [row["epoch"] or 0.0 for row in rows]
        start = max(0, len(epochs) - span) if span else 0
        for chart, lines in groups:
            for s in lines:
                s.values, s.smooth = s.values[start:], s.smooth[start:]
            chart.hover = None
            chart.set_data(epochs[start:], lines)
        self.refresh_benchmark(epochs[start:], window)
        self.update_panel()

    def refresh_benchmark(self, epochs: list[float], window: int) -> None:
        """The Benchmark chart: the shown run's benchmarks over the same epochs as the other charts."""
        rows = self.benchmark.source.runs.get(self.shown_run, []) if self.benchmark and self.shown_run else []
        rows = sorted((row for row in rows if row["epoch"] is not None), key=lambda row: row["epoch"])
        if epochs:
            rows = [row for row in rows if epochs[0] <= row["epoch"]]
        window = max(1, round(window / C.BENCHMARK_EVERY_EPOCHS))  # "Average" is in epochs

        def series(name: str, colour: str) -> Series:
            values = [row[name] / row["games"] * 100 if row[name] is not None and row["games"] else None
                      for row in rows]
            return Series(name, colour, values, moving_average(values, window))

        self.bench.hover = None
        self.bench.set_data([row["epoch"] for row in rows],
                            [series("wins", WIN), series("losses", LOSS), series("stopped", STOPPED)],
                            (epochs[0], max(epochs[-1], epochs[0] + 1)) if epochs else None)

    def update_cards(self, rows: list[Row], window: int) -> None:
        c = self.cards
        if not rows:
            for card in c.values():
                card.show("–")
            return
        last, recent, previous = rows[-1], rows[-window:], rows[-2 * window:-window]

        def pct(part: list[Row], name: str) -> float | None:
            games = sum(r["games"] or 0 for r in part)
            return sum(r[name] or 0 for r in part) / games * 100 if games else None

        def avg(part: list[Row], name: str) -> float | None:
            return mean_of([r[name] for r in part])

        seconds = avg(recent, "seconds")
        remaining = ((last["epochs"] or 0) - (last["epoch"] or 0)) * seconds if seconds else None
        games = int(sum(r["games"] or 0 for r in rows))
        self.games_text = f"{games} games"
        c["Epoch"].show(f"{int(last['epoch'] or 0)} / {int(last['epochs'] or 0)}",
                        f"{duration(remaining)} left" if remaining else "finished", MUTED, self.games_text)
        label = f"last {len(recent)} vs previous {len(previous)}" if previous else f"last {len(recent)} epochs"
        for card, name, good_up in (("Wins", "wins", True), ("Losses", "losses", False),
                                    ("Stopped", "stopped", False)):
            now = pct(recent, name)
            note, colour = trend(now, pct(previous, name), 1, " pts", good_up)
            c[card].show(f"{fmt(now)} %", note, colour, label)
        now = avg(recent, "unit_turns")
        note, colour = trend(now, avg(previous, "unit_turns"), 1, "", False)
        c["Unit turns"].show(fmt(now), note, colour,
                             f"won {fmt(avg(recent, 'win_turns'), 0)} · lost {fmt(avg(recent, 'loss_turns'), 0)}")
        now = avg(recent, "score")
        note, colour = trend(now, avg(previous, "score"), 1, "", True)
        c["Score"].show(fmt(now, 1, sign=True), note, colour, label)
        now = avg(recent, "loss")
        note, colour = trend(now, avg(previous, "loss"), 4, "", None)
        c["Loss value"].show(fmt(now, 4), note, colour, label)
        memory = last["memory"]
        c["Speed"].show(f"{fmt(seconds, 0)} s", "per epoch", MUTED,
                        f"memory {int(memory):,}".replace(",", " ") if memory is not None else "")

    def hover(self, epoch: float | None, owner: LineChart | None) -> None:
        for chart in self.charts:
            chart.set_hover(epoch, owner)

    def tick(self) -> None:
        """How long since the last epoch row (Epoch card and window title), in orange when it is overdue."""
        rows = self.source.runs.get(self.shown_run, []) if self.source and self.shown_run else []
        card = self.cards["Epoch"]
        if not self.source:
            card.show("–", "open a train_ai_bot", MUTED, ".csv or .log file")
        elif not rows:
            card.show("–", "waiting for the", MUTED, "first epoch…")
        else:
            written = _parse_time(rows[-1]["time"])
            if written is not None:
                age = time.time() - written
                late = age > 3 * (mean_of([r["seconds"] for r in rows[-10:]]) or 60) + 30
                ago = f"{int(age // 60)} min" if age >= 60 else f"{int(age)} s"
                card.extra.config(text=f"stopped? {ago}" if late else f"updated {ago} ago",
                                  fg=WARN if late else MUTED)
                self.title(f"Beastborn training monitor – epoch {int(rows[-1]['epoch'] or 0)}, "
                           f"{getattr(self, 'games_text', '')}" + (" – no new epoch, stopped?" if late else ""))
        self.after(1000, self.tick)


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    Monitor(Path(argv[0]) if argv else None).mainloop()


if __name__ == "__main__":
    main()
