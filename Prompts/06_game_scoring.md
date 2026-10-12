# Need new method to calculate score for current board
Please create a function that will calculate score for given player of given game state (GameState object)
This function will be used by some AI algorithms to calculate best moves for players or boots.
# Rules of calculation
1. Calculate 1 point for every 1 point of missing energy of all opponent animals, and 10 points for every 1 point missing in opponent Bosses.
2. Calculate +5 point for every level above the ground for each of my animals.

# Additional requirements
Display this points in UI for current user, and refresh it after every move. Keep it simple, all calculations are on backend

# Technical side
* This should be just new method inside CalculationEngine.
* Do just simple happy path testing only inside existing python test file.
* All constant values should be in constant.py


