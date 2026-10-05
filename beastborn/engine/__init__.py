"""Calculation Engine (CE): stateless rule calculations. Must not import game/ui/pygame."""
from beastborn.engine.interface import CalculationEngine
from beastborn.engine.results import AttackResult, CombatantSnapshot, EffectTick, TickResult
from beastborn.engine.rules_config import RulesConfig
from beastborn.engine.standard_engine import StandardCalculationEngine

__all__ = [
    "CalculationEngine", "AttackResult", "CombatantSnapshot", "EffectTick", "TickResult",
    "RulesConfig", "StandardCalculationEngine",
]
