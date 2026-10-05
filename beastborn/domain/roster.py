"""Loads unit definitions from JSON."""
from __future__ import annotations

import json
from pathlib import Path

from beastborn.domain.effects import EffectKind, EffectSpec
from beastborn.domain.unit import UnitStats

DEFAULT_UNITS_FILE = Path(__file__).resolve().parent.parent / "data" / "units.json"


class Roster:
    """All unit types available in a game, keyed by ``UnitStats.key``."""

    def __init__(self, units: list[UnitStats]):
        self._units = {u.key: u for u in units}
        if len(self._units) != len(units):
            raise ValueError("Duplicate unit keys in roster")

    def __getitem__(self, key: str) -> UnitStats:
        return self._units[key]

    def __iter__(self):
        return iter(self._units.values())

    def __len__(self) -> int:
        return len(self._units)

    @property
    def boss(self) -> UnitStats:
        bosses = [u for u in self._units.values() if u.is_boss]
        if len(bosses) != 1:
            raise ValueError(f"Roster must contain exactly one boss, found {len(bosses)}")
        return bosses[0]

    @property
    def recruits(self) -> list[UnitStats]:
        """Non-boss units, sorted by key so random draws are deterministic for a seed."""
        return sorted((u for u in self._units.values() if not u.is_boss), key=lambda u: u.key)


def _parse_unit(raw: dict) -> UnitStats:
    effects = tuple(
        EffectSpec(EffectKind(e["kind"]), int(e["magnitude"]), int(e.get("duration", 0)))
        for e in raw.get("on_hit", [])
    )
    stats = UnitStats(
        key=raw["key"],
        name=raw["name"],
        code=raw.get("code", raw["name"][0].upper()),
        hp=int(raw["hp"]),
        atk=int(raw["atk"]),
        en_atk=int(raw["en_atk"]),
        defense=int(raw["def"]),
        max_en=int(raw["max_en"]),
        reg_en=int(raw["reg_en"]),
        attack_range=int(raw.get("range", 1)),
        is_boss=bool(raw.get("boss", False)),
        on_hit=effects,
    )
    for field_name in ("hp", "max_en"):
        if getattr(stats, field_name) <= 0:
            raise ValueError(f"{stats.key}: {field_name} must be > 0")
    for field_name in ("atk", "en_atk", "defense", "reg_en"):
        if getattr(stats, field_name) < 0:
            raise ValueError(f"{stats.key}: {field_name} must be >= 0")
    return stats


def load_roster(path: str | Path | None = None) -> Roster:
    data = json.loads(Path(path or DEFAULT_UNITS_FILE).read_text(encoding="utf-8"))
    return Roster([_parse_unit(u) for u in data["units"]])
