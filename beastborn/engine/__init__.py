"""Calculation Engine (CE): stateless rule calculations. Must not import game/ui/pygame."""
from beastborn.engine.interface import BoardState, CalculationEngine
from beastborn.engine.results import AttackResult, CombatantSnapshot, EffectTick, TickResult
from beastborn.engine.standard_engine import StandardCalculationEngine

__all__ = [
    "BoardState", "CalculationEngine", "AttackResult", "CombatantSnapshot", "EffectTick", "TickResult",
    "StandardCalculationEngine",
]
