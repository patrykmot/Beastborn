"""What the bot says after its turns: a little humour, chosen by mood.

The lines live in ``beastborn/data/bot_lines.json`` (``constant.BOT_LINES_FILE``), so new text needs no code.
A new kind of message = a new ``Mood`` value + the same key in the JSON file (every mood must have lines).
"""
from __future__ import annotations

import json
import random
from collections.abc import Mapping, Sequence
from enum import Enum
from pathlib import Path
from typing import Any

from beastborn.constant import BOT_LINES_FILE


class Mood(Enum):
    """Why the bot speaks. Ideas for later: KILL, BOSS_HIT, WINNING / LOSING (from the search value), GAME_OVER."""

    START = "start"  # the bot's first turn in the game
    ATTACK = "attack"  # its unit attacked this turn
    DEFENSE = "defense"  # its unit only moved or waited


def load_bot_lines(path: Path = BOT_LINES_FILE) -> dict[Mood, tuple[str, ...]]:
    """Read the lines file; every ``Mood`` needs at least one line (keys starting with ``_`` are comments)."""
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    lines: dict[Mood, tuple[str, ...]] = {}
    for mood in Mood:
        texts: list[Any] = data.get(mood.value) or []
        if not texts or not all(isinstance(t, str) and t.strip() for t in texts):
            raise ValueError(f"{path}: mood '{mood.value}' needs a list of non-empty lines")
        lines[mood] = tuple(texts)
    return lines


class BotVoice:
    def __init__(self, lines: Mapping[Mood, Sequence[str]] | None = None, rng: random.Random | None = None):
        self._lines: Mapping[Mood, Sequence[str]] = lines if lines is not None else load_bot_lines()
        self._rng: random.Random = rng or random.Random()
        self._last: str | None = None

    def line(self, mood: Mood) -> str:
        """A random line for ``mood``; never the line said just before (when there is another one)."""
        options: list[str] = [t for t in self._lines[mood] if t != self._last] or list(self._lines[mood])
        self._last = self._rng.choice(options)
        return self._last
