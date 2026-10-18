# Need to add bot player
## Logical requirements
* Please add bot as player possible to select.
* It's possible to add only one bot, and its available only for 2 players (for now its limited by bot algorithm)
* If 2 players selected by default one player is bot.
* During bot move, in UI player need to see that he is not active player, so UI need to be blocked by taking moves, and all actions should be blocked (except new game button). 
  * Moreover, he needs to see other players actions (move, attacks, all commands)
  * This functionality is called WatchState.

## Technical requirements
* New player type need to be created **BotPlayerController** extends **PlayerController**
* Method choose_command need to calculate next move with following:
  * Use Alpha-beta pruning algorithm called ALG
  * As possible moves use **GameStateMachine.find_all_possible_actions** method.
  * Use **GameStateMachine.rollback** when move need to be removed from state.
  * The score is difference between my score minus opponent score (if It's plus, then it's good for me of course). As score please use **GameStateMachine.score** method. 
  * Provide just simple tests - like happy path.
  * Default deep search is 5.
  * Be sure that GameStateMachine state is not changed after move will be found. That is critical.
  * There should be a little drop of humor in bot :)
    * Extend bot with some text message after every turn, so user can see that in UI.
    * For now just add simple message about start, attack, defense. Prepare that place to be extend with more text on bot side.
    * This is available only for Bot player.
  * Do not check performance.

## Note:
If something is not logical, or/and will cause technical debt ask me please.



