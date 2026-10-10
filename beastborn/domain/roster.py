"""Loads unit definitions from JSON."""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from beastborn.constance import DEFAULT_ATTACK_RANGE, DEFAULT_MOVE_PENALTY, DEFAULT_UNITS_FILE
from beastborn.domain.effects import EffectKind, EffectSpec
from beastborn.domain.unit import UnitStats

_POSITIVE_FIELDS: tuple[str, ...] = ("hp", "max_en", "attack_range")
_NON_NEGATIVE_FIELDS: tuple[str, ...] = ("atk", "en_atk", "defense", "reg_en", "move_penalty")


class Roster:
    """All unit types available in a game, keyed by ``UnitStats.key``."""

    def __init__(self, units: list[UnitStats]):
        self._units: dict[str, UnitStats] = {u.key: u for u in units}
        if len(self._units) != len(units):
            raise ValueError("Duplicate unit keys in roster")

    def __getitem__(self, key: str) -> UnitStats:
        return self._units[key]

    def __iter__(self) -> Iterator[UnitStats]:
        return iter(self._units.values())

    def __len__(self) -> int:
        return len(self._units)

    @property
    def boss(self) -> UnitStats:
        bosses: list[UnitStats] = [u for u in self if u.is_boss]
        if len(bosses) != 1:
            raise ValueError(f"Roster must contain exactly one boss, found {len(bosses)}")
        return bosses[0]

    @property
    def recruits(self) -> list[UnitStats]:
        """Non-boss units, sorted by key so random draws are deterministic for a seed."""
        return sorted((u for u in self if not u.is_boss), key=lambda u: u.key)


def _parse_unit(raw: dict[str, Any]) -> UnitStats:
    effects: tuple[EffectSpec, ...] = tuple(EffectSpec(EffectKind(e["kind"])) for e in raw.get("on_hit", []))
    stats: UnitStats = UnitStats(
        key=raw["key"],
        name=raw["name"],
        code=raw.get("code", raw["name"][0].upper()),
        hp=int(raw["hp"]),
        atk=int(raw["atk"]),
        en_atk=int(raw["en_atk"]),
        defense=int(raw["def"]),
        max_en=int(raw["max_en"]),
        reg_en=int(raw["reg_en"]),
        attack_range=int(raw.get("range", DEFAULT_ATTACK_RANGE)),
        move_penalty=int(raw.get("move_penalty", DEFAULT_MOVE_PENALTY)),
        is_boss=bool(raw.get("boss", False)),
        on_hit=effects,
    )
    _validate(stats)
    return stats


def _validate(stats: UnitStats) -> None:
    for field_name in _POSITIVE_FIELDS:
        if getattr(stats, field_name) <= 0:
            raise ValueError(f"{stats.key}: {field_name} must be > 0")
    for field_name in _NON_NEGATIVE_FIELDS:
        if getattr(stats, field_name) < 0:
            raise ValueError(f"{stats.key}: {field_name} must be >= 0")


def load_roster(path: str | Path | None = None) -> Roster:
    data: dict[str, Any] = json.loads(Path(path or DEFAULT_UNITS_FILE).read_text(encoding="utf-8"))
    return Roster([_parse_unit(u) for u in data["units"]])
