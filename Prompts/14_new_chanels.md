# Task: Expand Neural Network Input Channels for Unit Identity and Status Effects

Please update the PyTorch input encoding in `constant.py` and `ai_find_action_strategy.py`. The goal is to replace generic unit channels with specific unit type channels and add a status effect channel.

## Step 1: Update `constant.py`
1. Recalculate and increase `AI_CHANNELS` to match the new total number of input planes.
2. Replace `CH_MY_UNITS` and `CH_ENEMY_UNITS` with specific constants for each unit type found in `units.json`:
   * `CH_MY_RAT` / `CH_ENEMY_RAT`
   * `CH_MY_PEASANT` / `CH_ENEMY_PEASANT`
   * `CH_MY_ARCHER` / `CH_ENEMY_ARCHER`
   * *(Note: `CH_MY_BOSS` / `CH_ENEMY_BOSS` already exist, keep them)*
3. Add channels for the Venom status effect:
   * `CH_MY_VENOM` / `CH_ENEMY_VENOM`

## Step 2: Update `ai_find_action_strategy.py`
1. Update the channel index assignments at the top of the file to match the new layout.
2. Rewrite the `_encode` method inside `AIFindActionStrategy`:
   * **Unit Identity:** Instead of writing a generic `1.0` to a single unit channel, check the unit's type (e.g., via `unit.stats.key`) and write `1.0` to the specific plane (Rat, Peasant, Archer, Boss) for the correct owner (My/Enemy).
   * **Venom Status:** Check if the unit is currently suffering from venom. If so, normalize the effect (e.g., `current_duration / VENOM_DURATION`) and write it to the corresponding venom channel at the unit's `(x, y)` coordinate.
   
Please output only the modified constants and the updated `_encode` method.