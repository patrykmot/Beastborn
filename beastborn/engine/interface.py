"""The Calculation Engine (CE) interface.

The CE is stateless: it takes values and returns values, and never changes game state.
Every game rule that is a calculation (movement cost, energy, damage, status effects)
goes through this interface, so swapping the implementation changes the rules without
touching the Game State Machine or the UI.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from beastborn.domain.effects import StatusEffect
from beastborn.domain.terrain import Tile
from beastborn.domain.unit import UnitStats
from beastborn.engine.results import AttackResult, CombatantSnapshot, TickResult


class CalculationEngine(ABC):
    # ---------- Movement ----------
    @abstractmethod
    def step_cost(self, unit: UnitStats, src: Tile, dst: Tile) -> int:
        """Energy needed to move one tile from ``src`` to the adjacent ``dst``."""

    # ---------- Energy ----------
    @abstractmethod
    def initial_energy(self, unit: UnitStats) -> int:
        """Energy a unit has when the game starts."""

    @abstractmethod
    def regenerate(self, unit: UnitStats, current_energy: int) -> int:
        """Energy after the start-of-turn regeneration."""

    @abstractmethod
    def attack_cost(self, attacker: UnitStats) -> int:
        """Energy spent on one attack."""

    @abstractmethod
    def in_attack_range(self, attacker: UnitStats, distance: int) -> bool:
        """Can the attacker hit a target ``distance`` tiles away (Manhattan)?"""

    # ---------- Combat ----------
    @abstractmethod
    def elevation_modifier(self, attacker_tile: Tile, defender_tile: Tile) -> int:
        """EM - flat damage bonus/penalty from the elevation difference."""

    @abstractmethod
    def current_defense(self, defense: int, tile: Tile) -> int:
        """DEF after the terrain penalty (TDM)."""

    @abstractmethod
    def resolve_attack(self, attacker: CombatantSnapshot, defender: CombatantSnapshot) -> AttackResult:
        """Full attack outcome: damage, block, armour shred and status effects on the defender."""

    # ---------- Status effects ----------
    @abstractmethod
    def tick_effects(self, effects: tuple[StatusEffect, ...], hp: int, defense: int) -> TickResult:
        """Start-of-turn processing of status effects (e.g. Venom damage)."""
