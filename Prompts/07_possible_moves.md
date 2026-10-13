# Need a method which will return list of possible actions (commands) for user on current board state.
* This method will be used by other future algorithm.
* It should be method in GameStateMachine
* Implementation should be very efficient, it should reuse excising methods like reachable_tiles, attack_targets etc.
* New method name findAllPossibleActions(filter: CommandSorter) ->  list of possible Command.
* Default CommandSorter implementation will return as first attacks on Boss, then attacks on animals, then moves on top of hills, and the rest later on. So most scorable commands should be first.
  * Name should be GameScoreMaxCommandSorter and it should be in same folder as GameStateMachine.
* 




