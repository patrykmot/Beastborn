# Beastborn Tactics: 1. `resolve_attack` Calculation Rules

## 1. Energy Verification
* **Condition:** If `Energy_current < en_atk`, attack fails.
* **Consumption:** `Energy_remaining = Energy_current - en_atk`

## 2. Elevation Momentum
Calculates raw kinetic power based on height difference.
* `Elevation_Modifier = Elev_attacker - Elev_defender`
* `Momentum_Damage = ATK + (0.5 * ATK * Elevation_Modifier)`

## 3. Range Dissipation (RP Factor)
For units with `attack_range > 1`.
* **Raw RP values:** 
  * 1 tile: `RP = 2`
  * 2 tiles: `RP = 1`
  * 3 tiles: `RP = 2`
  * 4 tiles: `RP = 4`
  * > 4 tiles: `Damage = 0` (Attack fails) (It means it can't be performed, so no energy drop)
* **Effective RP:** Elevation advantage mitigates the penalty, but RP cannot drop below 1.
  * `Effective_RP = max(1, Raw_RP - max(0, Elevation_Modifier))`
* **Base Damage:** Apply the RP divisor to the momentum.
  * `Base_Damage = Momentum_Damage / Effective_RP`

## 4. Defender Effective DEF
* **GRASS:** `Effective_DEF = Current_DEF`
* **MUD:** `Effective_DEF = floor(Current_DEF / 2)`

## 5. Final Damage Calculation
* `Final_Damage = max(0, Base_Damage - Effective_DEF)`

## 6. On-Hit Effects (Applied if attack resolves)
### VENOM
* **Damage:** `max(1, floor(ATK / 4))` DoT for 5 rounds.
* **Stacking:** Does not stack. Refreshes duration to 5 rounds if already present.
### ACID
* **Effect:** Permanently reduces target's current DEF.
* **Reduction Amount:** `max(1, floor(0.1 * ATK))` 
* **Calculation:** `Defender_Current_DEF = max(0, Defender_Current_DEF - Reduction_Amount)`

## Implementation notes
Decisions taken while implementing (`beastborn/engine/standard_engine.py`, numbers in `RulesConfig`):
* **Rounding:** everything is calculated exactly (fractions); only `Final_Damage` is rounded down. Example: `7 / 2 - 2 = 1.5` -> 1.
* **Melee units** (`attack_range = 1`) skip section 3, so `Effective_RP = 1`.
* **Momentum** never goes below 0 (attacking 2 levels up gives `ATK - ATK = 0`).
* **0-damage hits** still count as resolved: energy is spent and Venom / Acid are applied.
* **Out of range** (> 4 tiles, or more than the unit's own range): the attack is rejected and costs no energy.
* **Acid / Venom amounts** use whole-number division (`ATK // 10`, `ATK // 4`), which equals the floor in the formulas.
* **Archer** (`beastborn/data/units.json`): range 4, `move_penalty: 1` (+1 EN on every step), so it moves about one tile per turn.
