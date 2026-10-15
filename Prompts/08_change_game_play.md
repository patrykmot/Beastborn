# Need to change how game is played between players
## The logical change in game
* Players can't select any unit to move.
* Game engine GE will select unit SU to move.
* Every play got one unit action at one time (move, wait, attack).
  * After move, there is next user to move his next unit - turn change TC.
  * GE need to steer it that in one round, all players can move their all units.
  * After one round, second round is automatically started.
  * After user move his unit, and can't do other action, GE will do TC.
  * "End turn" UI button should do TC.
  * To avoid situation where there is not space to move or attack, this unit should selected at the end.  

## Technical change:
* GE changes should be implemented in GameStateMachine. Its include SU, TC and other above changes.
* Reuse the code as much you can.
* Review UI, and clean not needed code, and API calls.
* Review backend code, and clean not needed code, and API entries.
* Review unit tests, remove not needed ones.

## Note:
If something is not logical, or/and will cause technical debt ask me please.



