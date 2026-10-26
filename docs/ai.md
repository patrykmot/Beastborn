# This is just some thoughts to understand how AI flow works.


## From bottom to top flow
*  Training - finding target value for network:
  * `AIFindActionStrategy.targets` -> `return (rewards + self.gamma * following * (1.0 - done))`
    * `rewards` -> `Transition.reward` (this is taken from score difference between previous move score and current move score) 
      * <- `self.memory` 
      * <- `remember`:  `self.memory.append(Transition(state, float(reward), next_state, bool(done)))`
      * <- `_play: pending.afterstate, scale_score_for_ai(ranked[0].score - pending.score), ranked[0].afterstate, done=False`
        * reward = `scale_score_for_ai(ranked[0].score - pending.score)`
        * So **reward = ( current_score - previous_score) / C.AI_SCORE_SCALE**
    * `following` -> `self.predict(transition.next_state)` ->  `AfterstateNet(state)`
    * So Score difference is reward.
  * How score is calculated
    * `AIFindActionStrategy.rank` -> 
      * Find all possible actions `base.find_all_possible_actions` then for each action (command) run:
        * Calculate score = `gsm.score(me) - gsm.score(opponent)`
        * GameStateMachine:score -> Points for ``player``: HP the opponents' units have lost (Bosses count more) A fallen opponent (killed, or its Boss died) counts with all of its HP missing, plus a bonus for the kill.

