# Game Mechanics Documentation: Turn-Based Beast Strategy

## 1. General Concepts

* **Genre:** Turn-Based Tactics.
* **Theme:** Combat between fantasy monsters/beasts on a highly varied battlefield.
* **Win Condition:** Eliminate enemy Boss.
* **Key Feature:** Zero Random Number Generation (RNG) in combat. The outcome of clashes depends entirely on resource management (Energy), positioning (Terrain and Elevation), and unit statistics. Advanced tactics provide a significant advantage.
* Only human players for now (later we will add BOT to decide move). We can have up to 4 players.
* Map is created randomly:
  * Hills max to level 2.
  * Each player starting in one corner (as Boss), surrounded by its characters.
  * Add 4 random characters for each Boss.

## 2. Unit Statistics

Each character is defined by the following parameters:

* **Health Points (HP):** Determines durability. Dropping to 0 means death.
* **Attack (ATK):** The base multiplier for strike power.
* **Attack Energy (EN_ATK):** A constant value defining how much energy this specific character consumes to perform an attack.
* **Defense (DEF):** The value that reduces incoming damage.
* **Maximum Energy (MAX_EN):** The maximum amount of energy a unit can store.
* **Energy Regeneration (REG_EN):** The amount of energy recovered at the start of each turn (allows for hoarding energy by "resting" if MAX_EN is higher than REG_EN).

## 3. Energy System (The Core Decision Axis)

Energy is used for both moving and attacking. During their turn, the player has an energy pool for a given unit and decides how to distribute it.

### Movement (Energy Cost)

The battlefield is divided into a grid (squares).

* **Normal terrain "EN_NT":** 2 EN per tile.
* **Difficult terrain "EN_DT":** 3 EN per tile.
* **Elevation difference (Moving Uphill) "EN_MU":** +2 EN for each elevation level gained (e.g., moving to a normal tile one level higher costs 2 + 2 = 4 EN. Moving to a muddy tile one level higher costs 3 + 2 = 5 EN).
* **Elevation difference (Moving Downhill) "EN_MD":** 2 EN if moving into Normal Terrain. 5 EN (2 EN + 3 EN penalty) if moving into Difficult Terrain.

## 4. Combat Calculations and Formulas

### Step 1: Elevation Modifier (EM)

* Attacking from above grants a flat bonus to damage: EM = (Attacker Elevation - Defender Elevation) * 2.
* Attacking from below (optional mechanic) can apply a penalty: -1 damage for each level below the target (minimum penalty of 0).

### Step 2: Terrain Defense Modifier (TDM)

* A unit standing on difficult terrain has reduced defense because it is bogged down.
* Current DEF = DEF - Terrain Penalty (e.g., Mud penalty = 2, so a unit with 3 DEF standing in mud only has 1 Current DEF).

### Step 3: Total Damage Formula

The damage dealt to the target (DMG) is calculated using the following formula:
DMG = (ATK * EN_ATK) + EM - Current DEF

*Example:*

* A beast (ATK = 2, EN_ATK = 2) is standing on a hill (1 elevation level higher than the target, EM = +2). It has enough energy to perform its attack. 
* The target has a base DEF = 4, but is standing in mud (Penalty = 2), so its Current DEF = 2.
* Calculation: DMG = (2 * 2) + 2 - 2 = 4. The target loses 4 HP.

*Rule RL_1: Minimum damage from a successful attack is 1, unless the game allows for a block (DMG <= 0).*

## 5. Special Effects and Unit Types

### Units

1. **Big Rat:** Huge rat that can bite, and moves at moderate speed. Status Effect - VENOM!
2. **Peasant:** Just a big man with a pitchfork.
3. **Boss:** Each boss has a massive health pool, but movement range is very limited.
4. *Other units will be added later.*

### Status Effects

Status effects to their attacks:

* **Venom (Damage over Time):** The target loses a specific amount of HP at the start of its turn.
* **Acid (Armor Shred):** Permanently reduces the target's DEF by 1 point after every hit (down to a minimum of zero).

### Technical Rules

* The Game State Machine (GSM) needs to be isolated from the Calculation Engine (CE). One interface should be created for the CE, with one initial implementation, so it can be easily replaced or expanded.
  * Changing calculations (attack, defense, energy costs, status effect) should be possible solely by swapping the CE implementation. So the game mechanic is well isolated.
  * The CE should contain **only** calculation logic, leaving state management to the GSM.
* UI Layer should be separated from game, so at any time I can change how its rendered (consol, desktop, web), and how it's interacting with user (like keyboard, nouse etc..)
* First implementation of UI should be SIMPLE and done in Pygame as Desktop application. Just simple stuff that can be easily extended in future.
