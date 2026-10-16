# Need new function to find ALL possible commands that unit can do it in its turn.
## Technical Perquisite
* Please include new command type WrapperCommand - that will keep ordered list of commands.
* Change code of GameStateMachine so now it can submit WrapperCommand
  * Just make a loop there in submit and continue to with WrapperCommand.list execution one by one. 
    * If submitting command from list will be not succeed, stop processing and handle error as normal.
* Consider refactoring of all commends to provide common enum there or base class, so code that handle it can be simplified.

## The logical requirements
* REQ_LOG_08_01 - create functionality that it will return all possible actions for actually selected unit (including action to end turn)
  * All possible actions - so one action is one returned command (including WrapperCommand)!
  * All actions returned should be valid on current GameStateMachine object.
* REQ_LOG_08_02 - this functionality is read only, can't change game state! 

## Technical change:
* It should be developed in GameStateMachine. WrapperCommand should be keep on command_log of course.
* Unit action can be WrapperCommand in case there are multiple attacks, or move and attack as separate commands. Do NOT create silly results like WrapperCommand with few moves only (as that could be one command!) 
* Reuse existing code to lookup moves, attacks.
* Method should be fast, so please think about performance optimizations like cashing etc...
* Write just simple unit test, happy path + some most important border cases.
  * Include some of returned command execution by GameStateMachine just to validate.
  * GameStateMachine can execute just one of the command from list (as after execution GameStateMachine state will be changed, so other moves could be not valid any more)

## Note:
If something is not logical, or/and will cause technical debt ask me please.



