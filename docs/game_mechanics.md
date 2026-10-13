# Beastborn Tactics: 1. `resolve_attack` Calculation Rules

## 1. Energy Verification
* **Condition:** If `Energy_current < en_atk`, attack fails.
* **Consumption:** `Energy_remaining = Energy_current - en_atk`

## 2. Elevation Momentum
Calculates raw kinetic power based on height difference.
* `Elevation_Modifier = Elev_attacker - Elev_defender`
* `ELEVATION_COEF` (constant `ELEVATION_COEF` in `beastborn/constant.py`)
* `Momentum_Damage = ATK + (ELEVATION_COEF * ATK * Elevation_Modifier)`

## 3. Range Dissipation (RP Factor)
For units with `attack_range > 1`. RP is a damage **multiplier** that depends only on the distance (height does not change it).
* **Melee units** (`attack_range = 1`): `RP = 1`.
* **Base Damage:** Apply the RP multiplier to the momentum.
  * `Base_Damage = Momentum_Damage * RP`

## 4. Defender Effective DEF
* **GRASS:** `Effective_DEF = Current_DEF`
* **MUD:** `Effective_DEF = floor(Current_DEF / 2)`

## 5. Final Damage Calculation
* `Final_Damage = max(1, floor(Base_Damage - Effective_DEF))`
* **Minimum damage is always 1** for every attack that is performed, melee and ranged.

## 6. On-Hit Effects (Applied if attack resolves)
### VENOM
* **Damage:** `max(1, floor(ATK / 4))` DoT for 5 rounds.
* **Stacking:** One stack per attacker. A hit from a unit whose Venom is already on the target refreshes that stack to 5 rounds.
  A hit from another unit adds a new stack. Every stack ticks separately: two rats = two Venom ticks per turn.
### ACID
* **Effect:** Permanently reduces target's current DEF.
* **Reduction Amount:** `max(1, floor(0.1 * ATK))` 
* **Calculation:** `Defender_Current_DEF = max(0, Defender_Current_DEF - Reduction_Amount)`

## Implementation notes
Decisions taken while implementing (`beastborn/engine/standard_engine.py`, numbers in `beastborn/constant.py`):
* **Rounding:** everything is calculated exactly (fractions, so `0.8` is exactly 4/5); only `Final_Damage` is rounded down. Example: `7 * 0.8 - 2 = 3.6` -> 3.
* **Momentum** never goes below 0. Note the `ELEVATION_COEF`.
* **Every performed hit** spends energy and applies Venom / Acid.
* **Out of range** (> 4 tiles, or more than the unit's own range): the attack is rejected and costs no energy.
* **Acid / Venom amounts** use whole-number division, which equals the floor in the formulas.
