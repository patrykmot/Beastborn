"""Live training monitor: charts and statistics of ``tools/train_ai_bot.py`` runs, epoch by epoch.

Run (any Python 3.10+ with tkinter, no extra packages)::

    python tools/train_monitor.py                         # finds train_ai_bot.csv (or .log) by itself
    python tools/train_monitor.py tools/train_ai_bot.csv
    python tools/train_monitor.py tools/train_ai_bot.log  # a run started before the csv existed

It reads the epoch rows the trainer appends to ``--csv`` (or the epoch lines of its log file) and checks the file for
new rows every 2 seconds, reading only what was added since the last check. Every run in the file is listed; with
"Follow newest run" the view jumps to a new run as soon as its first epoch is written.

Charts: results (% wins, losses, stopped), unit turns (all games, won ones, lost ones), score and training loss.
Thin lines are the epoch values, thick ones the moving average over the "Average" epochs. Move the mouse over a chart
to read the values of one epoch.
"""
from __future__ import annotations

import csv
import re
import sys
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, ttk

POLL_MS: int = 2000
CANDIDATES: tuple[str, ...] = ("train_ai_bot.csv", "train_ai_bot.log")
NUMBERS: tuple[str, ...] = ("epoch", "epochs", "games", "wins", "losses", "draws", "stopped", "unit_turns",
                            "win_turns", "loss_turns", "score", "loss", "memory", "steps", "seconds")

# ---------------------------------------------------------------------- colours
BG, PANEL, TEXT, MUTED, GRID, AXIS = "#1c1d22", "#25272e", "#e8e8ea", "#9a9ca5", "#33353d", "#4a4d57"
WIN, LOSS, STOPPED = "#4cc38a", "#ef6461", "#a0a4ad"
TURNS, SCORE, TRAIN_LOSS, WARN = "#6aa9ff", "#c58af9", "#f2a53a", "#f2a53a"
FONT = ("Segoe UI", 9)
FONT_SMALL = ("Segoe UI", 8)
FONT_BIG = ("Segoe UI Semibold", 16)
FONT_TITLE = ("Segoe UI Semibold", 10)


# ====================================================================== reading the file
Row = dict  # one epoch: the NUMBERS as float | None, plus "run" and "time" (str)


class Source:
    """Epoch rows of every run in a csv or log file, read incrementally (only new complete lines each poll)."""

    def __init__(self, path: Path) -> None:
        self.path: Path = path
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
        row: Row = {"run": raw.get("run", "?"), "time": raw.get("time", "")}
        for name in NUMBERS:
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
                 digits: int = 1, on_hover=None) -> None:
        super().__init__(master, background=PANEL, highlightthickness=0, height=220, width=420)
        self.title, self.unit, self.fixed, self.digits, self.on_hover = title, unit, fixed, digits, on_hover
        self.epochs: list[float] = []
        self.series: list[Series] = []
        self.hover: int | None = None  # index into epochs
        self.bind("<Configure>", lambda _e: self.draw())
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda _e: self.on_hover and self.on_hover(None, None))

    def set_data(self, epochs: list[float], series: list[Series]) -> None:
        self.epochs, self.series = epochs, series
        self.draw()

    def set_hover(self, index: int | None, owner: LineChart | None) -> None:
        self.hover = index
        self._owner = owner is self
        self.draw()

    # ---------------------------------------------------------- geometry
    def _area(self) -> tuple[int, int, int, int]:
        return self.LEFT, self.TOP, max(self.LEFT + 10, self.winfo_width() - self.RIGHT), \
            max(self.TOP + 10, self.winfo_height() - self.BOTTOM)

    def _ranges(self) -> tuple[float, float, float, float]:
        x0, x1 = (self.epochs[0], self.epochs[-1]) if self.epochs else (0.0, 1.0)
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
        if self.epochs:
            for tick in nice_ticks(x0, x1, max(2, (right - left) // 70)):
                if tick == int(tick):
                    x = sx(tick)
                    self.create_line(x, bottom, x, bottom + 4, fill=AXIS)
                    self.create_text(x, bottom + 6, text=str(int(tick)), anchor="n", fill=MUTED, font=FONT_SMALL)
        self.create_line(left, bottom, right, bottom, fill=AXIS)

        if not self.epochs:
            self.create_text((left + right) / 2, (top + bottom) / 2, text="no data yet", fill=MUTED, font=FONT)
            return
        for s in self.series:
            self._line([(sx(x), sy(v)) if v is not None else None for x, v in zip(self.epochs, s.values)],
                       blend(s.colour, PANEL, 0.6), 1)
        for s in self.series:
            self._line([(sx(x), sy(v)) if v is not None else None for x, v in zip(self.epochs, s.smooth)],
                       s.colour, 2.5)

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
        self.on_hover(index, self)


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


# ====================================================================== the window
class Monitor(tk.Tk):
    def __init__(self, path: Path | None) -> None:
        super().__init__()
        self.title("Beastborn training monitor")
        self.configure(background=BG)
        self.geometry("1280x860")
        self.minsize(1100, 680)
        self.source: Source | None = None
        self.shown_run: str | None = None

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TCombobox", fieldbackground=PANEL, background=PANEL, foreground=TEXT, arrowcolor=TEXT)
        style.map("TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("readonly", TEXT)])
        style.configure("TCheckbutton", background=BG, foreground=TEXT)
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("TButton", background=PANEL, foreground=TEXT, borderwidth=0, padding=(10, 4))
        style.map("TButton", background=[("active", AXIS)])
        style.configure("TSpinbox", fieldbackground=PANEL, foreground=TEXT, arrowcolor=TEXT, background=PANEL)
        self.option_add("*TCombobox*Listbox.background", PANEL)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)

        # ---- top bar
        bar = tk.Frame(self, background=BG, padx=14, pady=10)
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
        cards = tk.Frame(self, background=BG, padx=10)
        cards.pack(fill="x")
        names = ("Epoch", "Wins", "Losses", "Stopped", "Unit turns", "Score", "Loss value", "Speed")
        self.cards: dict[str, Card] = {}
        for column, name in enumerate(names):
            card = Card(cards, name)
            card.grid(row=0, column=column, sticky="nsew", padx=4, pady=4)
            cards.columnconfigure(column, weight=1, uniform="cards")
            self.cards[name] = card

        # ---- charts
        charts = tk.Frame(self, background=BG, padx=10, pady=4)
        charts.pack(fill="both", expand=True)
        self.results = LineChart(charts, "Results per epoch", "%", fixed=(0, 100), digits=0, on_hover=self.hover)
        self.turns = LineChart(charts, "Unit turns per game", on_hover=self.hover)
        self.score = LineChart(charts, "Score per game", on_hover=self.hover)
        self.loss = LineChart(charts, "Training loss", digits=4, on_hover=self.hover)
        self.charts = (self.results, self.turns, self.score, self.loss)
        for i, chart in enumerate(self.charts):
            chart.grid(row=i // 2, column=i % 2, sticky="nsew", padx=4, pady=4)
        for i in range(2):
            charts.columnconfigure(i, weight=1, uniform="charts")
            charts.rowconfigure(i, weight=1, uniform="charts")

        self.window.trace_add("write", lambda *_: self.refresh())
        path = path or self.find_file()
        if path:
            self.open(path)
        self.after(POLL_MS, self.poll)
        self.after(1000, self.tick)

    # ---------------------------------------------------------- file
    @staticmethod
    def find_file() -> Path | None:
        for folder in (Path.cwd(), Path(__file__).resolve().parent, Path(__file__).resolve().parents[1]):
            for name in CANDIDATES:
                if (folder / name).is_file():
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
        self.refresh()

    def poll(self) -> None:
        if self.source is not None and self.source.poll():
            self.refresh()
        self.after(POLL_MS, self.poll)

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

    def hover(self, index: int | None, owner: LineChart | None) -> None:
        for chart in self.charts:
            chart.set_hover(index, owner)

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
